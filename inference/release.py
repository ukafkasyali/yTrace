"""Validate and stage an evaluated OpenTSLM checkpoint for inference."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import shutil
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    try:
        temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def verified_evaluation(path: Path) -> dict[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("state") != "complete":
        raise ValueError("Post-training evaluation is not complete")
    metrics = payload.get("metrics")
    numeric = (
        [
            value
            for value in metrics.values()
            if isinstance(value, (int, float)) and not isinstance(value, bool)
        ]
        if isinstance(metrics, dict)
        else []
    )
    if not numeric or any(not math.isfinite(float(value)) for value in numeric):
        raise ValueError("Post-training evaluation has no valid numeric metrics")
    return payload


def verified_checkpoint(path: Path, expects_lora: bool) -> dict[str, object]:
    import torch

    state = torch.load(path, map_location="cpu", weights_only=True)
    if not isinstance(state, dict):
        raise ValueError("Checkpoint must be a dictionary")
    expected = {"encoder_state", "projector_state", "lora_enabled"}
    if state.get("lora_enabled") is True:
        expected.add("lora_state")
    if set(state) != expected:
        raise ValueError(f"Checkpoint keys do not match the runtime contract: {sorted(state)}")
    if not isinstance(state.get("lora_enabled"), bool) or state["lora_enabled"] != expects_lora:
        raise ValueError("Checkpoint LoRA state does not match the inference configuration")
    for key in ("encoder_state", "projector_state") + (("lora_state",) if expects_lora else ()):
        values = state.get(key)
        valid = (
            isinstance(values, Mapping)
            and bool(values)
            and all(torch.is_tensor(value) for value in values.values())
        )
        if not valid:
            raise ValueError(f"{key} must be a non-empty tensor mapping")
    return state


def stage_release(
    candidate: Path,
    evaluation_status: Path,
    active_config: Path,
    models_dir: Path,
    output_config: Path,
) -> dict[str, object]:
    evaluation = verified_evaluation(evaluation_status)
    config = json.loads(active_config.read_text(encoding="utf-8"))
    verified_checkpoint(candidate, expects_lora=bool(config.get("lora")))
    digest = sha256(candidate)
    destination = models_dir.resolve() / f"opentslm-{digest[:12]}.pt"
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        if sha256(destination) != digest:
            raise ValueError(f"Existing staged checkpoint has the wrong digest: {destination}")
    else:
        temporary = destination.with_name(f".{destination.name}.tmp")
        try:
            shutil.copyfile(candidate, temporary)
            if sha256(temporary) != digest:
                raise ValueError("Checkpoint changed or was corrupted while staging")
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)

    released_config = dict(config)
    released_config["checkpoint_path"] = str(destination)
    released_config["checkpoint_sha256"] = digest
    atomic_json(output_config, released_config)
    manifest = {
        "checkpoint_path": str(destination),
        "checkpoint_sha256": digest,
        "config_path": str(output_config.resolve()),
        "evaluation_status": str(evaluation_status.resolve()),
        "evaluation_metrics": evaluation["metrics"],
        "released_at": datetime.now(UTC).isoformat(),
    }
    atomic_json(destination.with_suffix(".release.json"), manifest)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--post-training-status", type=Path, required=True)
    parser.add_argument("--active-config", type=Path, required=True)
    parser.add_argument("--models-dir", type=Path, required=True)
    parser.add_argument("--output-config", type=Path, required=True)
    args = parser.parse_args()
    result = stage_release(
        args.candidate,
        args.post_training_status,
        args.active_config,
        args.models_dir,
        args.output_config,
    )
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

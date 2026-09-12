"""Verify that predictions respond to temporal shifts and channel permutations."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import torch
from opentslm.model.llm.OpenTSLMSP import OpenTSLMSP

from robot_observability.constants import JOINT_NAMES
from robot_observability.metrics import parse_answer
from robot_observability.opentslm_dataset import RobotQADataset


def generate(model: OpenTSLMSP, sample: dict[str, object]) -> tuple[str, dict[str, object] | None]:
    with torch.no_grad():
        output = model.generate([sample], max_new_tokens=128, do_sample=False)[0]
    return output, parse_answer(output)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--prepared-root", type=Path, default=Path("data/prepared/v1"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--samples", type=int, default=8)
    parser.add_argument("--shift-ms", type=int, default=64)
    args = parser.parse_args()

    model = OpenTSLMSP(llm_id="meta-llama/Llama-3.2-1B", device="cuda")
    model.enable_lora()
    model.load_from_file(str(args.checkpoint))
    model.eval()
    dataset = RobotQADataset(args.prepared_root, "validation", mode="summary")
    selected = []
    for index in range(len(dataset)):
        sample = dataset[index]
        if sample["metadata"]["contact"]:
            selected.append(sample)
        if len(selected) == args.samples:
            break
    rows = []
    for sample in selected:
        base_output, base = generate(model, sample)
        shifted = dict(sample)
        shifted_signal = torch.zeros_like(sample["time_series"])
        shifted_signal[:, args.shift_ms :] = sample["time_series"][:, : -args.shift_ms]
        shifted["time_series"] = shifted_signal
        shifted_output, shifted_prediction = generate(model, shifted)

        strongest = str(sample["metadata"]["strongest_joint"])
        source_index = JOINT_NAMES.index(strongest)
        target_index = (source_index + 1) % len(JOINT_NAMES)
        permuted = dict(sample)
        permuted_signal = sample["time_series"].clone()
        temporary = permuted_signal[source_index].clone()
        permuted_signal[source_index] = permuted_signal[target_index]
        permuted_signal[target_index] = temporary
        permuted["time_series"] = permuted_signal
        permuted_output, permuted_prediction = generate(model, permuted)
        expected_permuted_joint = JOINT_NAMES[target_index]

        row = {
            "record_id": sample["record_id"],
            "target_onset_ms": sample["metadata"]["onset_sample"],
            "target_joint": strongest,
            "expected_permuted_joint": expected_permuted_joint,
            "base": base,
            "shifted": shifted_prediction,
            "permuted": permuted_prediction,
            "outputs": {"base": base_output, "shifted": shifted_output, "permuted": permuted_output},
        }
        rows.append(row)

    shift_errors = []
    permutation_hits = []
    for row in rows:
        base, shifted, permuted = row["base"], row["shifted"], row["permuted"]
        if base and shifted and base.get("onset_ms") is not None and shifted.get("onset_ms") is not None:
            shift_errors.append(abs((float(shifted["onset_ms"]) - float(base["onset_ms"])) - args.shift_ms))
        if permuted:
            permutation_hits.append(permuted.get("strongest_joint") == row["expected_permuted_joint"])
    summary = {
        "samples": len(rows),
        "shift_ms": args.shift_ms,
        "shift_equivariance_mae_ms": float(np.mean(shift_errors)) if shift_errors else None,
        "channel_permutation_accuracy": float(np.mean(permutation_hits)) if permutation_hits else None,
    }
    args.output.mkdir(parents=True, exist_ok=False)
    (args.output / "rows.jsonl").write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows), encoding="utf-8"
    )
    (args.output / "metrics.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()

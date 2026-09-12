"""Leakage-resistant, resumable plot-VLM evaluation on locked held-out windows."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import time
import urllib.error
import urllib.request
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from robot_observability.constants import JOINT_NAMES
from robot_observability.metrics import evaluate_rows, parse_answer
from robot_observability.prepared import PreparedSplit
from robot_observability.qa import answer_payload, channel_descriptions

MANIFEST_VERSION = 1
PROMPT_VERSION = "plot-summary-v1"
EVENT_TYPES = ("free", "intentional", "accidental")


@dataclass(frozen=True)
class PlotSettings:
    width_px: int = 1600
    height_px: int = 1200
    dpi: int = 100
    shared_y_axis: bool = True
    y_limit: float = 20.0
    sampling_hz: int = 1000
    line_width: float = 0.8


@dataclass(frozen=True)
class InferenceSettings:
    max_tokens: int = 256
    temperature: float = 0.0
    seed: int = 20260912
    timeout_seconds: int = 180
    max_retries: int = 3


def build_prompt() -> str:
    """Return a window-independent prompt with no label-derived metadata."""
    channel_schema = "\n".join(channel_descriptions({}))
    return (
        "Use only the seven-panel telemetry plot as evidence. Diagnose the complete telemetry window. "
        "The panels are synchronized and their x axis starts at 0 ms. Return one short evidence sentence, "
        "then `Answer:` and compact valid JSON with exactly these keys: contact, event_type, onset_ms, "
        "strongest_joint, affected_joints, evidence_start_ms, evidence_end_ms. Use null onset/joint/interval "
        "values and an empty affected_joints list for free motion. Event types are free, intentional, or "
        "accidental. Joint names are J1 through J7. Do not use markdown fences.\n\n"
        f"Channel schema (not measurements from this example):\n{channel_schema}"
    )


def select_locked_subset(records: list[dict[str, object]], limit: int, seed: int) -> list[dict[str, object]]:
    """Mirror ``evaluate_opentslm.fixed_subset`` and lock selected IDs in a manifest."""
    if limit <= 0:
        raise ValueError("limit must be positive")
    if not records:
        return []
    seen: set[str] = set()
    for index, metadata in enumerate(records):
        record_id = str(metadata["record_id"])
        event_type = str(metadata["event_type"])
        if record_id in seen:
            raise ValueError(f"Duplicate record_id in prepared split: {record_id}")
        if event_type not in EVENT_TYPES:
            raise ValueError(f"Unexpected event_type {event_type!r} for {record_id}")
        seen.add(record_id)
    target_size = min(limit, len(records))
    if target_size == len(records):
        indices = list(range(len(records)))
    else:
        rng = np.random.default_rng(seed)
        indices = sorted(rng.choice(len(records), size=target_size, replace=False).tolist())
    return [
        {
            "dataset_index": index,
            "record_id": str(metadata["record_id"]),
            "session_id": str(metadata.get("session_id", "")),
            "event_type": str(metadata["event_type"]),
        }
        for index in indices
        for metadata in (records[index],)
    ]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def build_manifest(
    prepared_root: Path,
    dataset: PreparedSplit,
    selected: list[dict[str, object]],
    *,
    model: str,
    model_revision: str | None,
    limit: int,
    selection_seed: int,
    inference: InferenceSettings,
    plot: PlotSettings,
    quantization: str,
    server_info: dict[str, object],
) -> dict[str, object]:
    records_path = prepared_root / "test" / "records.jsonl"
    signals_path = prepared_root / "test" / "signals.npy"
    prompt = build_prompt()
    return {
        "manifest_version": MANIFEST_VERSION,
        "benchmark": "plot_vlm_zero_shot",
        "training_performed": False,
        "split": "test",
        "selection": "NumPy PCG64 fixed random subset without replacement; sorted dataset indices",
        "selection_seed": selection_seed,
        "requested_samples": limit,
        "actual_samples": len(selected),
        "class_counts": dict(Counter(str(item["event_type"]) for item in selected)),
        "prepared_records_sha256": _sha256(records_path),
        "prepared_signals": {
            "path": str(signals_path),
            "size_bytes": signals_path.stat().st_size,
            "shape": list(dataset.signals.shape),
            "dtype": str(dataset.signals.dtype),
            "sha256": _sha256(signals_path),
        },
        "model": model,
        "model_revision": model_revision,
        "serving_backend": "vllm_openai_compatible",
        "server_info": server_info,
        "quantization": quantization,
        "prompt_version": PROMPT_VERSION,
        "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
        "inference": {
            "max_tokens": inference.max_tokens,
            "temperature": inference.temperature,
            "seed": inference.seed,
        },
        "plot": {
            "width_px": plot.width_px,
            "height_px": plot.height_px,
            "dpi": plot.dpi,
            "shared_y_axis": plot.shared_y_axis,
            "y_limit": plot.y_limit,
            "sampling_hz": plot.sampling_hz,
            "line_width": plot.line_width,
        },
        "records": selected,
    }


def write_or_validate_manifest(path: Path, manifest: dict[str, object], *, resume: bool) -> None:
    if path.exists():
        existing = json.loads(path.read_text(encoding="utf-8"))
        if existing != manifest:
            raise ValueError(
                "Existing manifest does not match this evaluation request; use a new output directory"
            )
        if not resume:
            raise FileExistsError(f"Evaluation already initialized: {path.parent}; pass --resume")
        return
    if resume and any(path.parent.iterdir()):
        raise FileNotFoundError(
            f"Cannot safely resume {path.parent}: files exist but manifest.json is missing"
        )
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def render_plot(signal: np.ndarray, path: Path, settings: PlotSettings | None = None) -> None:
    """Render only model-visible telemetry; no label, onset, or source metadata is included."""
    settings = settings or PlotSettings()
    if signal.ndim != 2 or signal.shape[0] != len(JOINT_NAMES):
        raise ValueError(f"Expected [7, time] signal, got {signal.shape}")
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    path.parent.mkdir(parents=True, exist_ok=True)
    time_ms = np.arange(signal.shape[1], dtype=np.float64) * 1000.0 / settings.sampling_hz
    figure, axes = plt.subplots(
        len(JOINT_NAMES),
        1,
        figsize=(settings.width_px / settings.dpi, settings.height_px / settings.dpi),
        sharex=True,
        sharey=settings.shared_y_axis,
        dpi=settings.dpi,
    )
    for index, axis in enumerate(axes):
        axis.plot(time_ms, signal[index], linewidth=settings.line_width, color="#2563eb")
        axis.axhline(0, linewidth=0.5, color="#64748b")
        if settings.shared_y_axis:
            axis.set_ylim(-settings.y_limit, settings.y_limit)
        axis.set_ylabel(JOINT_NAMES[index], rotation=0, labelpad=18)
        axis.grid(alpha=0.18)
    axes[-1].set_xlabel("Time from window start (ms)")
    figure.supylabel("Train-robust-scaled external torque")
    figure.suptitle("Seven synchronized robot joint-torque channels")
    figure.tight_layout()
    figure.savefig(path, dpi=settings.dpi, metadata={"Software": "robot-observability"})
    plt.close(figure)


def call_vllm(
    endpoint: str,
    model: str,
    image_path: Path,
    prompt: str,
    settings: InferenceSettings | None = None,
) -> dict[str, object]:
    settings = settings or InferenceSettings()
    encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
    body = {
        "model": model,
        "temperature": settings.temperature,
        "seed": settings.seed,
        "max_tokens": settings.max_tokens,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{encoded}"}},
                    {"type": "text", "text": prompt},
                ],
            }
        ],
    }
    request = urllib.request.Request(
        f"{endpoint.rstrip('/')}/v1/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    last_error: Exception | None = None
    started = time.monotonic()
    for attempt in range(settings.max_retries):
        try:
            with urllib.request.urlopen(request, timeout=settings.timeout_seconds) as response:
                result = json.load(response)
            choice = result["choices"][0]
            return {
                "output": str(choice["message"]["content"]),
                "response_model": str(result.get("model", model)),
                "finish_reason": choice.get("finish_reason"),
                "usage": result.get("usage", {}),
                "latency_seconds": time.monotonic() - started,
                "attempts": attempt + 1,
            }
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, KeyError, IndexError) as error:
            last_error = error
            if attempt + 1 < settings.max_retries:
                time.sleep(min(2**attempt, 8))
    raise RuntimeError(f"vLLM request failed after {settings.max_retries} attempts") from last_error


def probe_vllm(endpoint: str, requested_model: str) -> dict[str, object]:
    """Verify the endpoint/model and capture server version provenance when available."""

    def get_json(path: str) -> dict[str, object]:
        request = urllib.request.Request(f"{endpoint.rstrip('/')}{path}")
        with urllib.request.urlopen(request, timeout=30) as response:
            value = json.load(response)
        if not isinstance(value, dict):
            raise TypeError(f"Unexpected response from {path}")
        return value

    models = get_json("/v1/models")
    served_models = [
        str(item["id"]) for item in models.get("data", []) if isinstance(item, dict) and "id" in item
    ]
    if requested_model not in served_models:
        raise ValueError(
            f"Requested model {requested_model!r} is not served by {endpoint}; found {served_models}"
        )
    try:
        version: object = get_json("/version").get("version", "unknown")
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, TypeError):
        version = "unavailable"
    return {"endpoint": endpoint, "served_models": served_models, "vllm_version": version}


def load_resumable_rows(path: Path, allowed_ids: set[str]) -> dict[str, dict[str, object]]:
    """Load complete JSONL records and repair only a truncated final line."""
    if not path.exists():
        return {}
    raw_lines = path.read_text(encoding="utf-8").splitlines()
    rows: dict[str, dict[str, object]] = {}
    valid_lines: list[str] = []
    repaired = False
    for line_number, line in enumerate(raw_lines, start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            if line_number != len(raw_lines):
                raise ValueError(f"Malformed prediction at line {line_number} of {path}") from None
            repaired = True
            continue
        record_id = str(row.get("record_id", ""))
        if record_id not in allowed_ids:
            raise ValueError(f"Prediction record {record_id!r} is not in the locked manifest")
        if record_id in rows:
            raise ValueError(f"Duplicate prediction for {record_id}")
        rows[record_id] = row
        valid_lines.append(json.dumps(row, sort_keys=True))
    if repaired:
        path.write_text("\n".join(valid_lines) + ("\n" if valid_lines else ""), encoding="utf-8")
    return rows


def _append_row(path: Path, row: dict[str, object]) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def _wandb_run_id(wandb_module: Any) -> str:
    """Generate a run ID across both legacy and current W&B releases."""
    generate_id = getattr(getattr(wandb_module, "util", None), "generate_id", None)
    return str(generate_id()) if callable(generate_id) else secrets.token_hex(4)


class WandbLogger:
    def __init__(
        self,
        output_root: Path,
        manifest: dict[str, object],
        *,
        project: str | None,
        entity: str | None,
        run_name: str | None,
        mode: str,
        table_examples: int,
    ) -> None:
        self.run: Any | None = None
        self.wandb: Any | None = None
        self.table_examples = table_examples
        if not project or mode == "disabled":
            return
        try:
            import wandb
        except ImportError as error:
            raise RuntimeError("W&B logging requested; install the plot-baseline or train extra") from error
        run_id_path = output_root / "wandb_run_id.txt"
        run_id = (
            run_id_path.read_text(encoding="utf-8").strip() if run_id_path.exists() else _wandb_run_id(wandb)
        )
        if not run_id_path.exists():
            run_id_path.write_text(run_id + "\n", encoding="utf-8")
        self.wandb = wandb
        self.run = wandb.init(
            project=project,
            entity=entity,
            name=run_name or output_root.name,
            id=run_id,
            resume="allow",
            mode=mode,
            config={key: value for key, value in manifest.items() if key != "records"},
            tags=["baseline", "zero-shot", "plot-vlm", "test-only"],
        )

    def log_progress(self, completed: int, total: int, rows: list[dict[str, object]]) -> None:
        if self.run is None:
            return
        partial = evaluate_rows(rows)
        payload = {f"progress/{key}": value for key, value in partial.items()}
        payload.update({"progress/completed": completed, "progress/fraction": completed / total})
        self.run.log(payload, step=completed)

    def finish(self, metrics: dict[str, object], rows: list[dict[str, object]], output_root: Path) -> None:
        if self.run is None or self.wandb is None:
            return
        self.run.log(
            {f"test/{key}": value for key, value in metrics.items() if isinstance(value, int | float)}
        )
        columns = [
            "record_id",
            "plot",
            "target",
            "output",
            "prediction",
            "parse_valid",
            "contact_correct",
            "semantics_correct",
            "strongest_joint_correct",
            "onset_abs_error_ms",
            "latency_seconds",
        ]
        table = self.wandb.Table(columns=columns)
        chosen = _balanced_example_rows(rows, self.table_examples)
        for row in chosen:
            diagnostics = example_diagnostics(row)
            table.add_data(
                row["record_id"],
                self.wandb.Image(str(output_root / str(row["plot_path"]))),
                json.dumps(row["target"], sort_keys=True),
                row["output"],
                json.dumps(row["prediction"], sort_keys=True),
                row["prediction"] is not None,
                diagnostics["contact_correct"],
                diagnostics["semantics_correct"],
                diagnostics["strongest_joint_correct"],
                diagnostics["onset_abs_error_ms"],
                row["latency_seconds"],
            )
        self.run.log({"test/examples": table})
        self.run.summary.update(metrics)
        self.run.finish()


def example_diagnostics(row: dict[str, object]) -> dict[str, object]:
    """Expose per-example correctness without changing aggregate metric semantics."""
    target = row["target"]
    prediction = row.get("prediction")
    predicted = prediction if isinstance(prediction, dict) else {}
    target_onset = target["onset_ms"]
    predicted_onset = predicted.get("onset_ms")
    try:
        onset_error = (
            abs(float(predicted_onset) - float(target_onset))
            if target_onset is not None and predicted_onset is not None
            else None
        )
    except (TypeError, ValueError):
        onset_error = None
    return {
        "contact_correct": (
            isinstance(predicted.get("contact"), bool) and predicted.get("contact") == target["contact"]
        ),
        "semantics_correct": predicted.get("event_type") == target["event_type"],
        "strongest_joint_correct": (
            predicted.get("strongest_joint") == target["strongest_joint"]
            if target["strongest_joint"] is not None
            else None
        ),
        "onset_abs_error_ms": onset_error,
    }


def _balanced_example_rows(rows: list[dict[str, object]], limit: int) -> list[dict[str, object]]:
    if limit <= 0:
        return []
    buckets: dict[str, list[dict[str, object]]] = {event_type: [] for event_type in EVENT_TYPES}
    for row in rows:
        event_type = str(row["target"]["event_type"])
        buckets.setdefault(event_type, []).append(row)
    chosen: list[dict[str, object]] = []
    while len(chosen) < min(limit, len(rows)):
        made_progress = False
        for event_type in EVENT_TYPES:
            if buckets[event_type] and len(chosen) < limit:
                chosen.append(buckets[event_type].pop(0))
                made_progress = True
        if not made_progress:
            break
    return chosen


def evaluate_plot_vlm(
    prepared_root: Path,
    output_root: Path,
    *,
    endpoint: str,
    model: str,
    model_revision: str | None = None,
    limit: int = 512,
    selection_seed: int = 20260912,
    quantization: str = "none",
    resume: bool = False,
    plot: PlotSettings | None = None,
    inference: InferenceSettings | None = None,
    wandb_project: str | None = None,
    wandb_entity: str | None = None,
    wandb_run_name: str | None = None,
    wandb_mode: str = "disabled",
    wandb_table_examples: int = 48,
    log_every: int = 10,
) -> dict[str, object]:
    plot = plot or PlotSettings()
    inference = inference or InferenceSettings()
    dataset = PreparedSplit(prepared_root, "test")
    selected = select_locked_subset(dataset.records, limit, selection_seed)
    server_info = probe_vllm(endpoint, model)
    manifest = build_manifest(
        prepared_root,
        dataset,
        selected,
        model=model,
        model_revision=model_revision,
        limit=limit,
        selection_seed=selection_seed,
        inference=inference,
        plot=plot,
        quantization=quantization,
        server_info=server_info,
    )
    output_root.mkdir(parents=True, exist_ok=True)
    write_or_validate_manifest(output_root / "manifest.json", manifest, resume=resume)
    predictions_path = output_root / "predictions.jsonl"
    allowed_ids = {str(item["record_id"]) for item in selected}
    completed_by_id = load_resumable_rows(predictions_path, allowed_ids)
    if completed_by_id and not resume:
        raise FileExistsError(f"Predictions already exist in {output_root}; pass --resume")
    logger = WandbLogger(
        output_root,
        manifest,
        project=wandb_project,
        entity=wandb_entity,
        run_name=wandb_run_name,
        mode=wandb_mode,
        table_examples=wandb_table_examples,
    )
    prompt = build_prompt()
    total = len(selected)
    for item in selected:
        record_id = str(item["record_id"])
        if record_id in completed_by_id:
            continue
        index = int(item["dataset_index"])
        signal, metadata = dataset[index]
        if str(metadata["record_id"]) != record_id:
            raise RuntimeError("Prepared dataset changed after manifest construction")
        plot_relative = Path("plots") / f"{hashlib.sha256(record_id.encode()).hexdigest()[:16]}.png"
        image_path = output_root / plot_relative
        render_plot(signal, image_path, plot)
        response = call_vllm(endpoint, model, image_path, prompt, inference)
        output = str(response["output"])
        row = {
            "record_id": record_id,
            "session_id": metadata.get("session_id"),
            "intent": "summary",
            "target": answer_payload(metadata, "summary"),
            "output": output,
            "prediction": parse_answer(output),
            "plot_path": str(plot_relative),
            "response_model": response["response_model"],
            "finish_reason": response["finish_reason"],
            "usage": response["usage"],
            "latency_seconds": response["latency_seconds"],
            "attempts": response["attempts"],
        }
        _append_row(predictions_path, row)
        completed_by_id[record_id] = row
        completed = len(completed_by_id)
        if completed % log_every == 0 or completed == total:
            ordered_partial = [
                completed_by_id[str(entry["record_id"])]
                for entry in selected
                if str(entry["record_id"]) in completed_by_id
            ]
            logger.log_progress(completed, total, ordered_partial)
        print(json.dumps({"completed": completed, "total": total, "record_id": record_id}))

    rows = [completed_by_id[str(item["record_id"])] for item in selected]
    metrics: dict[str, object] = {
        "benchmark": "plot_vlm_zero_shot",
        "training_performed": False,
        "model": model,
        "model_revision": model_revision,
        "quantization": quantization,
        "split": "test",
        "selection_seed": selection_seed,
        "manifest_sha256": _sha256(output_root / "manifest.json"),
        "target_class_counts": dict(Counter(str(row["target"]["event_type"]) for row in rows)),
        "mean_latency_seconds": (float(np.mean([row["latency_seconds"] for row in rows])) if rows else 0.0),
        **evaluate_rows(rows),
    }
    (output_root / "metrics.json").write_text(
        json.dumps(metrics, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    logger.finish(metrics, rows, output_root)
    return metrics

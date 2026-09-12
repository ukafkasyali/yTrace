"""Independent data, training-health, and completion monitor for unattended runs."""

from __future__ import annotations

import argparse
import json
import math
import statistics
import subprocess
import time
from collections import Counter
from pathlib import Path

import numpy as np


def read_json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def read_jsonl(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def atomic_json(path: Path, payload: dict[str, object]) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def tmux_exists(name: str) -> bool:
    return (
        subprocess.run(
            ["tmux", "has-session", "-t", name],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        ).returncode
        == 0
    )


def gpu_snapshot() -> dict[str, float] | None:
    result = subprocess.run(
        [
            "nvidia-smi",
            "--query-gpu=memory.used,memory.total,utilization.gpu,temperature.gpu,power.draw",
            "--format=csv,noheader,nounits",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        return None
    values = [float(value.strip()) for value in result.stdout.strip().split(",")]
    return dict(
        zip(("memory_used_mb", "memory_total_mb", "utilization_pct", "temperature_c", "power_w"), values)
    )


def data_audit(prepared_root: Path, manifest: dict[str, object]) -> dict[str, object]:
    records = {
        split: read_jsonl(prepared_root / split / "records.jsonl")
        for split in ("train", "validation", "test")
    }
    session_sets = {split: {str(row["session_id"]) for row in rows} for split, rows in records.items()}
    record_sets = {split: {str(row["record_id"]) for row in rows} for split, rows in records.items()}
    overlaps = {}
    for left, right in (("train", "validation"), ("train", "test"), ("validation", "test")):
        overlaps[f"{left}_{right}_sessions"] = len(session_sets[left] & session_sets[right])
        overlaps[f"{left}_{right}_records"] = len(record_sets[left] & record_sets[right])

    seed = int(manifest.get("seed", 0))
    maximum = int(manifest.get("config", {}).get("training", {}).get("max_examples", len(records["train"])))
    rng = np.random.default_rng(seed)
    indices = sorted(rng.choice(len(records["train"]), size=maximum, replace=False).tolist())
    selected = [records["train"][index] for index in indices]
    onset_summary = {}
    for split, rows in records.items():
        onsets = [int(row["onset_sample"]) for row in rows if row.get("onset_sample") is not None]
        onset_summary[split] = {
            "count": len(onsets),
            "unique": len(set(onsets)),
            "min": min(onsets),
            "max": max(onsets),
            "median": statistics.median(onsets),
        }
    return {
        "prepared_summary": read_json(prepared_root / "dataset_summary.json"),
        "session_and_record_overlaps": overlaps,
        "split_integrity_ok": not any(overlaps.values()),
        "selected_train_class_counts": dict(Counter(str(row["event_type"]) for row in selected)),
        "selected_train_sessions": len({str(row["session_id"]) for row in selected}),
        "onset_summary": onset_summary,
        "limitations": [
            "Subject identifiers are absent, so subject-disjoint evaluation cannot be certified.",
            "Strongest-joint and affected-joint targets are deterministic evidence pseudo-labels.",
            "The zero-signal canary is paired but smaller than the 48-window generation canary.",
        ],
    }


def latest_by_event(events: list[dict[str, object]], name: str) -> dict[str, object]:
    matches = [event for event in events if event.get("event") == name]
    return matches[-1] if matches else {}


def onset_diagnostics(run_dir: Path, generation: dict[str, object]) -> dict[str, object]:
    if not generation:
        return {}
    path = run_dir / (f"generation_{generation['phase']}_step_{int(generation['step']):06d}.jsonl")
    rows = read_jsonl(path)
    pairs = []
    for row in rows:
        truth = row.get("target", {}).get("onset_ms")
        prediction = (row.get("prediction") or {}).get("onset_ms")
        if truth is None or prediction is None:
            continue
        try:
            pairs.append((float(truth), float(prediction)))
        except (TypeError, ValueError):
            continue
    if not pairs:
        return {"paired_predictions": 0}
    truth = np.asarray([pair[0] for pair in pairs])
    prediction = np.asarray([pair[1] for pair in pairs])
    correlation = float(np.corrcoef(truth, prediction)[0, 1]) if len(pairs) > 1 else None
    counts = Counter(prediction.tolist())
    return {
        "paired_predictions": len(pairs),
        "prediction_target_correlation": correlation,
        "model_mae_ms": float(np.mean(np.abs(prediction - truth))),
        "constant_460ms_mae_on_same_rows": float(np.mean(np.abs(460.0 - truth))),
        "most_common_prediction_fraction": max(counts.values()) / len(pairs),
        "unique_predictions": len(counts),
    }


def health_snapshot(
    run_dir: Path,
    manifest: dict[str, object],
    audit: dict[str, object],
    training_session: str,
    post_session: str,
) -> dict[str, object]:
    events = read_jsonl(run_dir / "metrics.jsonl")
    train_rows = [event for event in events if event.get("event") == "train_step"]
    validation_rows = [event for event in events if event.get("event") == "validation_check"]
    generation_rows = [event for event in events if event.get("event") == "generation_eval"]
    zero_rows = [event for event in events if event.get("event") == "zero_signal_eval"]
    probe_rows = [event for event in events if event.get("event") == "training_probe_eval"]
    probe_loss_rows = [event for event in events if event.get("event") == "training_probe_loss_eval"]
    probe_zero_rows = [event for event in events if event.get("event") == "training_probe_zero_signal_eval"]
    probe_ablation_rows = [
        event for event in events if event.get("event") == "training_probe_signal_ablation"
    ]
    validation_ablation_rows = [
        event for event in events if event.get("event") == "validation_signal_ablation"
    ]
    latest_train = train_rows[-1] if train_rows else {}
    latest_validation = validation_rows[-1] if validation_rows else {}
    latest_generation = generation_rows[-1] if generation_rows else {}
    latest_zero = zero_rows[-1] if zero_rows else {}
    latest_probe = probe_rows[-1] if probe_rows else {}
    latest_probe_loss = probe_loss_rows[-1] if probe_loss_rows else latest_probe
    latest_probe_zero = probe_zero_rows[-1] if probe_zero_rows else {}
    latest_probe_ablation = probe_ablation_rows[-1] if probe_ablation_rows else {}
    latest_validation_ablation = validation_ablation_rows[-1] if validation_ablation_rows else {}
    zero_class_counts = {}
    if latest_zero:
        zero_path = run_dir / (
            f"generation_zero_signal_{latest_zero['phase']}_step_{int(latest_zero['step']):06d}.jsonl"
        )
        zero_examples = read_jsonl(zero_path)
        zero_class_counts = dict(
            Counter(str(row.get("target", {}).get("event_type")) for row in zero_examples)
        )
    config = manifest.get("config", {})
    batch_size = int(config.get("batch_size", 1))
    accumulation = int(config.get("gradient_accumulation_steps", 1))
    train_examples = int(manifest.get("train_examples", 0))
    epochs = int(config.get("epochs", 1))
    total_steps = math.ceil(math.ceil(train_examples / batch_size) / accumulation) * epochs
    step = int(latest_train.get("step", 0))
    elapsed = float(latest_train.get("elapsed_seconds", 0.0))
    step_rate = step / elapsed if elapsed else 0.0
    eta_seconds = (total_steps - step) / step_rate if step_rate else None
    recent_train_loss = statistics.mean(float(row["loss"]) for row in train_rows[-5:]) if train_rows else None
    best_validation = min((float(row["loss"]) for row in validation_rows), default=None)
    best_validation_step = next(
        (
            int(row["step"])
            for row in validation_rows
            if best_validation is not None and float(row["loss"]) == best_validation
        ),
        None,
    )

    gaps = {
        key.removeprefix("delta/"): value
        for key, value in latest_validation_ablation.items()
        if key.startswith("delta/")
    }
    if not gaps:
        for key in (
            "contact_accuracy",
            "semantics_accuracy",
            "strongest_joint_accuracy",
            "onset_coverage",
        ):
            if key in latest_generation and key in latest_zero:
                gaps[key] = float(latest_generation[key]) - float(latest_zero[key])
    onset = onset_diagnostics(run_dir, latest_generation)
    alerts = []
    if not audit["split_integrity_ok"]:
        alerts.append("RED: session or record overlap exists across splits")
    if step >= 100 and float(latest_generation.get("parse_validity", 0.0)) < 0.95:
        alerts.append("YELLOW: JSON validity is below 95%")
    if step >= 200 and onset.get("model_mae_ms", math.inf) >= onset.get(
        "constant_460ms_mae_on_same_rows", -math.inf
    ):
        alerts.append("RED: onset predictions do not beat a constant 460 ms on the same rows")
    if step >= 200 and abs(float(onset.get("prediction_target_correlation") or 0.0)) < 0.4:
        alerts.append("YELLOW: onset prediction/target correlation is below 0.4")
    if step >= 200 and float(latest_generation.get("strongest_joint_accuracy", 0.0)) < 0.4:
        alerts.append("YELLOW: strongest-joint accuracy is below 40%")
    if step >= 200 and max(gaps.values(), default=0.0) < 0.1:
        alerts.append("RED: real signals do not materially beat zeroed signals")
    if latest_zero and set(zero_class_counts) != {"free", "accidental", "intentional"}:
        alerts.append("RED: zero-signal canary does not cover all three event classes")
    if latest_probe_loss:
        probe_step = int(latest_probe_loss.get("step", 0))
        loss_fraction = float(latest_probe_loss.get("loss_fraction_of_initial", math.inf))
        answer_fit = float(latest_probe.get("answer_exact_match", 0.0))
        schema_fit = float(latest_probe.get("schema_exact_match", 0.0))
        if probe_step >= 100 and loss_fraction <= 0.35 and answer_fit < 0.5:
            alerts.append(
                "RED: teacher-forced training-probe loss collapsed but decoded exact-answer fit is below 50%"
            )
        if probe_step >= 100 and schema_fit < 0.9:
            alerts.append("YELLOW: fewer than 90% of fixed training-probe answers have the exact schema")
        if int(latest_probe.get("epoch", 0)) >= 1 and answer_fit < 0.8:
            alerts.append("YELLOW: exact training prompts are not at least 80% fitted after one epoch")
        relative_signal_loss_gap = float(latest_probe_loss.get("relative_signal_loss_gap", 0.0))
        relevant_deltas = [
            float(value)
            for key, value in latest_probe_ablation.items()
            if key
            in {
                "delta/answer_exact_match",
                "delta/contact_accuracy",
                "delta/semantics_accuracy",
                "delta/strongest_joint_accuracy",
                "delta/onset_coverage",
            }
        ]
        if probe_step >= 200 and relative_signal_loss_gap < 0.05 and max(relevant_deltas, default=0.0) < 0.1:
            alerts.append("RED: fixed training-probe outputs show no measurable dependence on the signal")
        matched_loss = float(latest_probe_loss.get("matched_validation_loss", math.nan))
        probe_loss = float(latest_probe_loss.get("loss", math.nan))
        if (
            probe_step >= 200
            and math.isfinite(matched_loss)
            and math.isfinite(probe_loss)
            and matched_loss > probe_loss * 1.5
        ):
            alerts.append("YELLOW: matched-prompt validation loss is over 50% above training-probe loss")
    loss_overfit = False
    if best_validation is not None and latest_validation:
        loss_overfit = float(latest_validation["loss"]) > best_validation * 1.1
        if loss_overfit:
            alerts.append("YELLOW: validation loss is more than 10% above its best value")
    training_running = tmux_exists(training_session)
    post_running = tmux_exists(post_session)
    status = read_json(run_dir / "status.json")
    post_status = read_json(run_dir / "post_training_status.json")
    if not training_running and status.get("state") != "complete":
        alerts.append("RED: training process exited without successful completion state")
    severity = "red" if any(alert.startswith("RED") for alert in alerts) else "yellow" if alerts else "green"
    return {
        "timestamp": time.time(),
        "severity": severity,
        "alerts": alerts,
        "progress": {
            "step": step,
            "total_steps": total_steps,
            "fraction": step / total_steps if total_steps else 0.0,
            "elapsed_seconds": elapsed,
            "eta_seconds": eta_seconds,
            "training_running": training_running,
            "post_training_running": post_running,
        },
        "loss": {
            "recent_train": recent_train_loss,
            "latest_validation": latest_validation.get("loss"),
            "best_validation": best_validation,
            "best_validation_step": best_validation_step,
            "overfit": loss_overfit,
        },
        "latest_generation": latest_generation,
        "latest_zero_signal": latest_zero,
        "latest_training_probe": latest_probe,
        "latest_training_probe_loss": latest_probe_loss,
        "latest_training_probe_zero_signal": latest_probe_zero,
        "latest_training_probe_ablation": latest_probe_ablation,
        "latest_validation_ablation": latest_validation_ablation,
        "zero_signal_class_counts": zero_class_counts,
        "real_minus_zero_gaps": gaps,
        "onset_diagnostics": onset,
        "run_status": status,
        "post_training_status": post_status,
        "gpu": gpu_snapshot(),
        "data_integrity": {
            "split_integrity_ok": audit["split_integrity_ok"],
            "session_and_record_overlaps": audit["session_and_record_overlaps"],
        },
    }


def flatten_numeric(payload: dict[str, object], prefix: str = "audit") -> dict[str, float | int]:
    flat = {}
    for key, value in payload.items():
        name = f"{prefix}/{key}"
        if isinstance(value, bool):
            flat[name] = int(value)
        elif isinstance(value, (int, float)) and math.isfinite(float(value)):
            flat[name] = value
        elif isinstance(value, dict):
            flat.update(flatten_numeric(value, name))
    return flat


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--prepared-root", type=Path, default=Path("data/prepared/v1"))
    parser.add_argument("--training-session", required=True)
    parser.add_argument("--post-session", required=True)
    parser.add_argument("--poll-seconds", type=int, default=30)
    parser.add_argument("--wandb", action="store_true")
    parser.add_argument("--wandb-project", default="robot-observability")
    args = parser.parse_args()

    manifest = read_json(args.run_dir / "run_manifest.json")
    audit = data_audit(args.prepared_root, manifest)
    atomic_json(args.run_dir / "data_audit.json", audit)
    history_path = args.run_dir / "health.jsonl"
    wandb_run = None
    if args.wandb:
        import wandb

        wandb_run = wandb.init(
            project=args.wandb_project,
            name=f"{args.run_dir.name}-audit",
            group=args.run_dir.name,
            job_type="audit",
            config={"source_run": args.run_dir.name, "data_audit": audit},
            tags=["audit", "methodology", "monitor"],
        )
        wandb_run.define_metric("audit/progress/step")
        wandb_run.define_metric("audit/*", step_metric="audit/progress/step")

    while True:
        snapshot = health_snapshot(args.run_dir, manifest, audit, args.training_session, args.post_session)
        atomic_json(args.run_dir / "health.json", snapshot)
        with history_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(snapshot, sort_keys=True) + "\n")
        print(json.dumps(snapshot, sort_keys=True), flush=True)
        if wandb_run is not None:
            wandb_run.log(flatten_numeric(snapshot))
        if not snapshot["progress"]["training_running"] and not snapshot["progress"]["post_training_running"]:
            break
        time.sleep(args.poll_seconds)
    if wandb_run is not None:
        wandb_run.finish()


if __name__ == "__main__":
    main()

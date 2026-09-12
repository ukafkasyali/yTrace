"""Report whether a tiny OpenTSLM run can fit its fixed training examples.

The default mode is diagnostic and always exits successfully. Pass ``--strict`` to
turn the recommendations into a launch gate for a subsequent expensive run.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def read_events(path: Path, event_name: str) -> list[dict[str, object]]:
    with path.open(encoding="utf-8") as handle:
        return [
            event
            for line in handle
            if line.strip() and (event := json.loads(line)).get("event") == event_name
        ]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--allow-incomplete", action="store_true")
    parser.add_argument("--max-loss-fraction", type=float, default=0.25)
    parser.add_argument("--min-parse-validity", type=float, default=0.95)
    parser.add_argument("--min-schema-fit", type=float, default=0.90)
    parser.add_argument("--min-answer-fit", type=float, default=0.80)
    parser.add_argument("--min-signal-change-rate", type=float, default=0.30)
    parser.add_argument("--min-relative-signal-loss-gap", type=float, default=0.05)
    parser.add_argument("--min-task-ablation-delta", type=float, default=0.10)
    parser.add_argument("--max-relative-generalization-gap", type=float)
    args = parser.parse_args()

    probes = read_events(args.run_dir / "metrics.jsonl", "training_probe_eval")
    zero_probes = read_events(args.run_dir / "metrics.jsonl", "training_probe_zero_signal_eval")
    ablations = read_events(args.run_dir / "metrics.jsonl", "training_probe_signal_ablation")
    if not probes:
        raise RuntimeError(f"No training_probe_eval events in {args.run_dir}")
    latest = probes[-1]
    latest_zero = zero_probes[-1] if zero_probes else {}
    latest_ablation = ablations[-1] if ablations else {}
    status_path = args.run_dir / "status.json"
    status = json.loads(status_path.read_text(encoding="utf-8")) if status_path.exists() else {}
    task_deltas = [
        float(value)
        for key, value in latest_ablation.items()
        if key
        in {
            "delta/answer_exact_match",
            "delta/contact_accuracy",
            "delta/semantics_accuracy",
            "delta/strongest_joint_accuracy",
            "delta/onset_coverage",
            "delta/affected_joints_set_f1",
            "delta/evidence_interval_coverage",
        }
    ]
    intent_answer_metrics = [
        float(value)
        for key, value in latest.items()
        if key.startswith("intent/") and key.endswith("/answer_exact_match")
    ]
    checks = {
        "run_complete": args.allow_incomplete or status.get("state") == "complete",
        "teacher_forced_loss_fitted": float(latest["loss_fraction_of_initial"]) <= args.max_loss_fraction,
        "valid_json": float(latest["parse_validity"]) >= args.min_parse_validity,
        "exact_schema": float(latest["schema_exact_match"]) >= args.min_schema_fit,
        "exact_decoded_answer": float(latest["answer_exact_match"]) >= args.min_answer_fit,
        "every_observed_intent_fitted": bool(intent_answer_metrics)
        and min(intent_answer_metrics) >= args.min_answer_fit,
        "prediction_depends_on_signal": float(latest_zero.get("prediction_change_rate", 0.0))
        >= args.min_signal_change_rate,
        "zero_signal_has_meaningfully_higher_loss": float(latest.get("relative_signal_loss_gap", 0.0))
        >= args.min_relative_signal_loss_gap,
        "relevant_task_degrades_without_signal": max(task_deltas, default=0.0)
        >= args.min_task_ablation_delta,
    }
    if args.max_relative_generalization_gap is not None:
        checks["matched_generalization_gap"] = (
            float(latest.get("matched_relative_generalization_gap", float("inf")))
            <= args.max_relative_generalization_gap
        )
    report = {
        "verdict": "pass" if all(checks.values()) else "investigate",
        "strict": args.strict,
        "checks": checks,
        "start": probes[0],
        "latest": latest,
        "latest_zero_signal": latest_zero,
        "latest_signal_ablation": latest_ablation,
        "run_status": status,
        "interpretation": (
            "A low teacher-forced token loss is insufficient: decoded answers and paired signal "
            "ablation must also pass. Thresholds are launch recommendations, not benchmark claims."
        ),
    }
    print(json.dumps(report, indent=2, sort_keys=True))
    if args.strict and not all(checks.values()):
        raise SystemExit(2)


if __name__ == "__main__":
    main()

"""Audit a completed OpenTSLM run for methodological and behavioral failure modes.

This consumes immutable run receipts only. It does not load a model, alter a
checkpoint, or evaluate the test split. The report deliberately distinguishes
source annotations, deterministic pseudo-labels, and generated predictions.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import json
import math
import re
import statistics
from collections import Counter, defaultdict
from pathlib import Path

EVENT_TYPES = {"free", "intentional", "accidental"}
JOINTS = {f"J{index}" for index in range(1, 8)}
TASK_METRICS = (
    "contact_f1",
    "semantics_macro_f1",
    "strongest_joint_accuracy",
    "affected_joints_set_f1",
    "onset_within_50ms",
    "evidence_interval_iou",
)
GENERATION_FILE = re.compile(r"generation_(?:step|epoch)_step_(\d{6})\.jsonl(?:\.gz)?$")


def read_json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def read_jsonl(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    if path.suffix == ".gz":
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            return [json.loads(line) for line in handle if line.strip()]
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def jsonl_receipt(run_dir: Path, name: str) -> Path:
    plain = run_dir / name
    compressed = run_dir / f"{name}.gz"
    return plain if plain.exists() else compressed


def safe_rate(numerator: float, denominator: int) -> float:
    return float(numerator) / denominator if denominator else 0.0


def finite_number(value: object) -> bool:
    return type(value) in (int, float) and math.isfinite(float(value))


def valid_prediction(prediction: object, target: dict[str, object]) -> bool:
    """Mirror the generated-answer value contract without importing training dependencies."""
    if not isinstance(prediction, dict) or set(prediction) != set(target):
        return False
    if "contact" in prediction and type(prediction["contact"]) is not bool:
        return False
    if "event_type" in prediction and prediction["event_type"] not in EVENT_TYPES:
        return False
    if "onset_ms" in prediction:
        value = prediction["onset_ms"]
        if value is not None and (not finite_number(value) or not 0 <= float(value) < 1024):
            return False
    if "strongest_joint" in prediction:
        value = prediction["strongest_joint"]
        if value is not None and value not in JOINTS:
            return False
    if "affected_joints" in prediction:
        value = prediction["affected_joints"]
        if (
            not isinstance(value, list)
            or any(type(joint) is not str or joint not in JOINTS for joint in value)
            or len(set(value)) != len(value)
        ):
            return False
    for key, endpoint in (("evidence_start_ms", False), ("evidence_end_ms", True)):
        if key not in prediction or prediction[key] is None:
            continue
        if not finite_number(prediction[key]):
            return False
        upper_ok = float(prediction[key]) <= 1024 if endpoint else float(prediction[key]) < 1024
        if not 0 <= float(prediction[key]) or not upper_ok:
            return False
    if {"evidence_start_ms", "evidence_end_ms"} <= set(prediction):
        start, end = prediction["evidence_start_ms"], prediction["evidence_end_ms"]
        if (start is None) != (end is None) or (start is not None and not float(start) < float(end)):
            return False
    summary_keys = {
        "contact",
        "event_type",
        "onset_ms",
        "strongest_joint",
        "affected_joints",
        "evidence_start_ms",
        "evidence_end_ms",
    }
    if set(prediction) == summary_keys:
        if not prediction["contact"]:
            return (
                prediction["event_type"] == "free"
                and not prediction["affected_joints"]
                and all(
                    prediction[key] is None
                    for key in (
                        "onset_ms",
                        "strongest_joint",
                        "evidence_start_ms",
                        "evidence_end_ms",
                    )
                )
            )
        return (
            prediction["event_type"] != "free"
            and prediction["onset_ms"] is not None
            and prediction["strongest_joint"] in prediction["affected_joints"]
            and prediction["evidence_start_ms"] is not None
            and prediction["evidence_end_ms"] is not None
        )
    return True


def rationale(output: object) -> str:
    text = str(output or "")
    rationale_at = text.casefold().find("rationale:")
    answer_at = text.casefold().rfind("answer:")
    if rationale_at < 0 or answer_at <= rationale_at:
        return ""
    return text[rationale_at + len("rationale:") : answer_at].strip()


def normalized_rationale(text: str) -> str:
    text = re.sub(r"\bJ[1-7]\b", "J#", text, flags=re.IGNORECASE)
    text = re.sub(r"\b\d+(?:\.\d+)?\b", "#", text)
    return " ".join(text.casefold().split())


def pearson(left: list[float], right: list[float]) -> float | None:
    if len(left) < 2 or len(left) != len(right):
        return None
    left_mean, right_mean = statistics.mean(left), statistics.mean(right)
    numerator = sum((x - left_mean) * (y - right_mean) for x, y in zip(left, right))
    denominator = math.sqrt(
        sum((x - left_mean) ** 2 for x in left) * sum((y - right_mean) ** 2 for y in right)
    )
    return numerator / denominator if denominator else None


def set_f1(truth: object, prediction: object) -> float:
    truth_set = set(truth) if isinstance(truth, list) else set()
    prediction_set = set(prediction) if isinstance(prediction, list) else set()
    if not truth_set and not prediction_set:
        return 1.0
    if not truth_set or not prediction_set:
        return 0.0
    precision = len(truth_set & prediction_set) / len(prediction_set)
    recall = len(truth_set & prediction_set) / len(truth_set)
    return 2 * precision * recall / (precision + recall) if precision + recall else 0.0


def interval_iou(target: dict[str, object], prediction: object) -> float | None:
    if not isinstance(prediction, dict):
        return None
    values = (
        target.get("evidence_start_ms"),
        target.get("evidence_end_ms"),
        prediction.get("evidence_start_ms"),
        prediction.get("evidence_end_ms"),
    )
    if not all(finite_number(value) for value in values):
        return None
    start, end, predicted_start, predicted_end = (float(value) for value in values)
    intersection = max(0.0, min(end, predicted_end) - max(start, predicted_start))
    union = max(end, predicted_end) - min(start, predicted_start)
    return intersection / union if union > 0 else float(start == predicted_start)


def row_failure_types(row: dict[str, object]) -> list[str]:
    target = row.get("target") if isinstance(row.get("target"), dict) else {}
    prediction = row.get("prediction")
    failures = []
    if not isinstance(prediction, dict):
        failures.append("parse_failure")
    else:
        if set(prediction) != set(target):
            failures.append("schema_key_mismatch")
        elif not valid_prediction(prediction, target):
            failures.append("schema_value_violation")
        if "contact" in target and prediction.get("contact") != target["contact"]:
            failures.append("contact_error")
        if "event_type" in target and prediction.get("event_type") != target["event_type"]:
            failures.append("semantics_error")
        if "strongest_joint" in target and prediction.get("strongest_joint") != target["strongest_joint"]:
            failures.append("strongest_joint_error")
        if (
            "affected_joints" in target
            and set_f1(target["affected_joints"], prediction.get("affected_joints")) < 1
        ):
            failures.append("affected_joints_error")
        if target.get("onset_ms") is not None:
            predicted = prediction.get("onset_ms")
            if not finite_number(predicted):
                failures.append("onset_missing")
            elif abs(float(predicted) - float(target["onset_ms"])) > 50:
                failures.append("onset_error_over_50ms")
        if target.get("evidence_start_ms") is not None:
            iou = interval_iou(target, prediction)
            if iou is None:
                failures.append("evidence_interval_missing")
            elif iou < 0.5:
                failures.append("evidence_interval_iou_below_0.5")
    if not rationale(row.get("output")):
        failures.append("missing_rationale")
    if row.get("retry_used"):
        failures.append("retry_required")
    return failures


def analyze_rows(rows: list[dict[str, object]]) -> dict[str, object]:
    intents: dict[str, list[dict[str, object]]] = defaultdict(list)
    failure_counts: Counter[str] = Counter()
    failure_examples = []
    templates: Counter[str] = Counter()
    target_joint_counts: Counter[str] = Counter()
    contact_confusion: Counter[str] = Counter()
    semantics_confusion: Counter[str] = Counter()
    joint_confusion: Counter[str] = Counter()
    onset_truth, onset_predictions = [], []
    onset_errors, onset_signed_errors = [], []
    interval_ious = []

    for row in rows:
        intents[str(row.get("intent", "unknown"))].append(row)
        target = row.get("target") if isinstance(row.get("target"), dict) else {}
        prediction = row.get("prediction") if isinstance(row.get("prediction"), dict) else {}
        failures = row_failure_types(row)
        failure_counts.update(failures)
        if failures and len(failure_examples) < 30:
            failure_examples.append(
                {
                    "record_id": row.get("record_id"),
                    "intent": row.get("intent"),
                    "failure_types": failures,
                    "target": target,
                    "prediction": row.get("prediction"),
                    "output_excerpt": str(row.get("output", ""))[:300],
                }
            )
        text = rationale(row.get("output"))
        if text:
            templates[normalized_rationale(text)] += 1
        if target.get("strongest_joint") is not None:
            target_joint_counts[str(target["strongest_joint"])] += 1
        for key, confusion in (
            ("contact", contact_confusion),
            ("event_type", semantics_confusion),
            ("strongest_joint", joint_confusion),
        ):
            if key in target:
                confusion[f"{target.get(key)!r} -> {prediction.get(key)!r}"] += 1
        if target.get("onset_ms") is not None and finite_number(prediction.get("onset_ms")):
            truth, predicted = float(target["onset_ms"]), float(prediction["onset_ms"])
            onset_truth.append(truth)
            onset_predictions.append(predicted)
            onset_errors.append(abs(predicted - truth))
            onset_signed_errors.append(predicted - truth)
        iou = interval_iou(target, prediction)
        if iou is not None:
            interval_ious.append(iou)

    intent_report = {}
    for intent, intent_rows in sorted(intents.items()):
        parsed = [row for row in intent_rows if isinstance(row.get("prediction"), dict)]
        schemas = [
            row for row in intent_rows if valid_prediction(row.get("prediction"), row.get("target", {}))
        ]
        rationales = [rationale(row.get("output")) for row in intent_rows]
        intent_report[intent] = {
            "n": len(intent_rows),
            "parse_validity": safe_rate(len(parsed), len(intent_rows)),
            "schema_validity": safe_rate(len(schemas), len(intent_rows)),
            "answer_exact_match": safe_rate(
                sum(row.get("prediction") == row.get("target") for row in intent_rows), len(intent_rows)
            ),
            "first_pass_blank_rate": safe_rate(
                sum(not str(row.get("first_pass_output", "")).strip() for row in intent_rows),
                len(intent_rows),
            ),
            "retry_rate": safe_rate(
                sum(bool(row.get("retry_used")) for row in intent_rows), len(intent_rows)
            ),
            "rationale_presence": safe_rate(sum(bool(text) for text in rationales), len(intent_rows)),
        }

    constant_mae = None
    if onset_truth:
        constant = statistics.median(onset_truth)
        constant_mae = statistics.mean(abs(constant - truth) for truth in onset_truth)
    return {
        "n": len(rows),
        "intent_counts": dict(sorted(Counter(str(row.get("intent")) for row in rows).items())),
        "target_strongest_joint_counts": dict(sorted(target_joint_counts.items())),
        "per_intent": intent_report,
        "failure_counts": dict(failure_counts.most_common()),
        "failure_examples": failure_examples,
        "confusion": {
            "contact": dict(contact_confusion.most_common()),
            "semantics": dict(semantics_confusion.most_common()),
            "strongest_joint": dict(joint_confusion.most_common()),
        },
        "temporal": {
            "paired_onsets": len(onset_errors),
            "onset_coverage": safe_rate(
                len(onset_errors), sum(row.get("target", {}).get("onset_ms") is not None for row in rows)
            ),
            "onset_mae_ms": statistics.mean(onset_errors) if onset_errors else None,
            "onset_bias_ms": statistics.mean(onset_signed_errors) if onset_signed_errors else None,
            "onset_target_prediction_correlation": pearson(onset_truth, onset_predictions),
            "constant_median_mae_ms_on_covered_rows": constant_mae,
            "evidence_interval_mean_iou": statistics.mean(interval_ious) if interval_ious else None,
        },
        "rationales": {
            "presence_rate": safe_rate(sum(templates.values()), len(rows)),
            "normalized_unique_rate": safe_rate(len(templates), sum(templates.values())),
            "most_common_normalized_templates": [
                {"count": count, "template": template} for template, count in templates.most_common(10)
            ],
            "limitation": (
                "Lexical consistency and diversity do not establish that a generated rationale is a faithful "
                "explanation of the model's signal-dependent computation."
            ),
        },
    }


def generation_receipts(run_dir: Path) -> dict[int, Path]:
    receipts = {}
    for path in run_dir.glob("generation_*_step_*.jsonl*"):
        match = GENERATION_FILE.fullmatch(path.name)
        if match:
            receipts[int(match.group(1))] = path
    return receipts


def add_finding(
    findings: list[dict[str, str]],
    severity: str,
    code: str,
    evidence: str,
    implication: str,
    recommendation: str,
) -> None:
    findings.append(
        {
            "severity": severity,
            "code": code,
            "evidence": evidence,
            "implication": implication,
            "recommendation": recommendation,
        }
    )


def analyze_run(run_dir: Path) -> dict[str, object]:
    status = read_json(run_dir / "status.json")
    manifest = read_json(run_dir / "run_manifest.json")
    metrics_path = jsonl_receipt(run_dir, "metrics.jsonl")
    events = read_jsonl(metrics_path)
    generation_events = [row for row in events if row.get("event") == "generation_eval"]
    validation_events = [row for row in events if row.get("event") == "validation_check"]
    selection_events = [row for row in events if row.get("event") == "grounding_checkpoint_selection"]
    probe_events = [row for row in events if row.get("event") == "training_probe_eval"]
    ablation_events = [row for row in events if row.get("event") == "validation_signal_ablation"]
    receipts = generation_receipts(run_dir)
    if not generation_events:
        raise ValueError(f"No generation_eval events found in {metrics_path}")

    final_generation = max(generation_events, key=lambda row: int(row.get("step", -1)))
    final_step = int(final_generation["step"])
    final_rows = read_jsonl(receipts[final_step]) if final_step in receipts else []
    row_analysis = analyze_rows(final_rows) if final_rows else {}
    best_validation = min(validation_events, key=lambda row: float(row["loss"])) if validation_events else {}
    best_observed_selection = (
        max(selection_events, key=lambda row: float(row.get("score", -math.inf))) if selection_events else {}
    )
    best_observed_step = int(best_observed_selection.get("step", final_step))
    generation_by_step = {int(row["step"]): row for row in generation_events}
    best_observed_generation = generation_by_step.get(best_observed_step, final_generation)
    latest_probe = max(probe_events, key=lambda row: int(row.get("step", -1))) if probe_events else {}
    latest_ablation = (
        max(ablation_events, key=lambda row: int(row.get("step", -1))) if ablation_events else {}
    )

    config = manifest.get("config", {}) if isinstance(manifest.get("config"), dict) else {}
    selection_config = config.get("checkpoint_selection", {}) if isinstance(config, dict) else {}
    gates = selection_config.get("gates", {}) if isinstance(selection_config, dict) else {}
    gate_analysis = {}
    for key, threshold in gates.items():
        values = [float(row.get(str(key), 0.0)) for row in generation_events]
        gate_analysis[str(key)] = {
            "threshold": float(threshold),
            "maximum": max(values, default=None),
            "passing_evaluations": sum(value >= float(threshold) for value in values),
        }

    regression = {}
    for metric in TASK_METRICS:
        if metric in best_observed_generation and metric in final_generation:
            regression[metric] = float(final_generation[metric]) - float(best_observed_generation[metric])

    findings: list[dict[str, str]] = []
    if status.get("state") != "complete":
        add_finding(
            findings,
            "critical",
            "run_not_complete",
            f"status.state={status.get('state')!r}",
            "The receipts do not prove a successful training completion.",
            "Resolve the run failure before interpreting model quality.",
        )
    eligible = [row for row in selection_events if row.get("eligible")]
    if selection_events and not eligible:
        failed = ", ".join(
            f"{key} max={details['maximum']:.3f} < {details['threshold']:.3f}"
            for key, details in gate_analysis.items()
            if details["passing_evaluations"] == 0
        )
        add_finding(
            findings,
            "critical",
            "checkpoint_gate_deadlock",
            f"0/{len(selection_events)} decoded evaluations were eligible; never-passed gates: {failed or 'combined gates'}.",
            "No task-selected grounding checkpoint was saved; deployment falls back to token-loss selection.",
            "Tune gates from an explicit pre-run acceptance policy or always retain the best observed decoded checkpoint separately.",
        )
    material_regressions = {key: value for key, value in regression.items() if value <= -0.10}
    if material_regressions:
        add_finding(
            findings,
            "high",
            "late_decoded_regression",
            f"Best observed decoded step={best_observed_step}; final-minus-best metrics={material_regressions}.",
            "Continuing training improved token likelihood while degrading operator-facing answers.",
            "Select checkpoints using predeclared decoded validation metrics and confirm once on unused recording groups.",
        )
    first_schema = float(final_generation.get("first_pass/schema_exact_match", 0.0))
    final_schema = float(final_generation.get("schema_exact_match", 0.0))
    retry_rate = float(final_generation.get("retry_rate", 0.0))
    if first_schema < 0.95 or retry_rate > 0.05:
        add_finding(
            findings,
            "high",
            "retry_masks_generation_instability",
            f"First-pass schema={first_schema:.3f}, post-retry schema={final_schema:.3f}, retry rate={retry_rate:.3f}.",
            "A second decode improves reporting metrics but increases latency and hides unreliable first responses.",
            "Treat first-pass validity as the deployment metric; add constrained decoding or stronger schema supervision.",
        )
    rationale_presence = float(final_generation.get("rationale_presence", 0.0))
    if rationale_presence < 0.9:
        add_finding(
            findings,
            "high",
            "rationale_contract_not_reliable",
            f"Only {rationale_presence:.1%} of final validation outputs contained a parseable Rationale section.",
            "The conversational observability contract is not consistently satisfied even when JSON parses.",
            "Report rationale coverage separately and reject or visibly label missing rationales in the demo.",
        )
    if latest_probe and float(latest_probe.get("answer_exact_match", 0.0)) < 0.8:
        add_finding(
            findings,
            "medium",
            "training_prompts_not_fully_fitted",
            f"Final fixed training-probe exact match={float(latest_probe.get('answer_exact_match', 0.0)):.3f}.",
            "The model remains capacity/optimization limited on exact training prompts; validation errors are not purely overfit.",
            "Inspect prompt-family losses and output-token allocation before adding epochs.",
        )
    hypothesis = str(config.get("experiment", {}).get("hypothesis", ""))
    training_intents = manifest.get("training_intent_counts", {})
    curriculum = config.get("training", {}).get("curriculum_intents")
    if (
        curriculum == "all"
        and isinstance(training_intents, dict)
        and len(training_intents) > 3
        and "three_intent" in hypothesis
    ):
        add_finding(
            findings,
            "medium",
            "manifest_hypothesis_mismatch",
            f"Immutable hypothesis says {hypothesis!r}, while curriculum_intents='all' and receipts contain {len(training_intents)} intents.",
            "A headline metadata field misdescribes the experiment even though detailed receipt fields are correct.",
            "Use the detailed intent counts as authoritative and validate hypothesis labels against materialized views before launch.",
        )
    panel_n = int(final_generation.get("n", len(final_rows)))
    per_intent_min = min((entry["n"] for entry in row_analysis.get("per_intent", {}).values()), default=0)
    if len(generation_events) > 5 and per_intent_min < 30:
        add_finding(
            findings,
            "medium",
            "reused_small_validation_panel",
            f"The same {panel_n}-row panel was decoded {len(generation_events)} times; smallest intent slice n={per_intent_min}.",
            "Checkpoint rankings are noisy and repeated inspection can overfit decisions to this panel.",
            "Use it for monitoring only, then confirm the frozen choice on new recording groups with session-level intervals.",
        )
    missing_joints = sorted(JOINTS - set(row_analysis.get("target_strongest_joint_counts", {})))
    if missing_joints:
        add_finding(
            findings,
            "medium",
            "joint_slices_not_observable",
            f"Final panel has no strongest-joint targets for {', '.join(missing_joints)}.",
            "Aggregate joint accuracy cannot characterize these joints under natural prevalence.",
            "Keep natural-prevalence headline metrics, but add a separate diagnostic joint-coverage panel without training rebalancing.",
        )
    if not any(run_dir.glob("*test*prediction*.jsonl")):
        add_finding(
            findings,
            "medium",
            "no_untouched_test_receipt",
            "The training run directory contains validation/probe receipts but no test prediction receipt.",
            "This run supports model selection analysis, not a new held-out performance claim.",
            "Evaluate the frozen checkpoint once on unused recording groups; do not tune after inspecting them.",
        )
    prediction_change = None
    zero_events = [row for row in events if row.get("event") == "zero_signal_eval"]
    if zero_events:
        prediction_change = float(zero_events[-1].get("prediction_change_rate", 0.0))
    if prediction_change is None or prediction_change < 0.5:
        add_finding(
            findings,
            "high",
            "weak_or_missing_signal_ablation",
            f"Latest paired zero-signal prediction-change rate={prediction_change!r}.",
            "The receipts do not show robust dependence on telemetry.",
            "Require a larger paired zero-signal panel and add channel permutation and temporal-shift checks.",
        )

    severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    findings.sort(key=lambda finding: (severity_order[finding["severity"]], finding["code"]))
    trajectory_keys = (
        "step",
        "contact_f1",
        "semantics_macro_f1",
        "strongest_joint_accuracy",
        "affected_joints_set_f1",
        "onset_within_50ms",
        "evidence_interval_iou",
        "schema_exact_match",
        "first_pass/schema_exact_match",
        "rationale_presence",
        "retry_rate",
    )
    return {
        "report_contract": {
            "scope": "completed-run validation methodology audit; not a test-set score or safety claim",
            "target_sources": {
                "source_annotations": ["contact/event semantics", "manual event onset"],
                "deterministic_measurements_or_pseudolabels": [
                    "strongest joint",
                    "affected joints",
                    "evidence interval",
                ],
                "generated_predictions": "prediction/output fields in generation receipts",
            },
        },
        "run": {
            "path": str(run_dir),
            "state": status.get("state"),
            "stop_reason": status.get("stop_reason"),
            "final_step": status.get("global_step", final_step),
            "elapsed_hours": float(status.get("elapsed_seconds", 0.0)) / 3600,
        },
        "checkpoint_selection": {
            "decoded_evaluations": len(generation_events),
            "eligible_evaluations": len(eligible),
            "best_observed_decoded_step": best_observed_step,
            "best_observed_decoded_score": best_observed_selection.get("score"),
            "best_validation_loss_step": best_validation.get("step"),
            "best_validation_loss": best_validation.get("loss"),
            "gate_analysis": gate_analysis,
            "final_minus_best_observed_decoded": regression,
            "best_observed_metrics": {key: best_observed_generation.get(key) for key in TASK_METRICS},
            "final_metrics": {key: final_generation.get(key) for key in TASK_METRICS},
            "trajectory": [
                {key: row.get(key) for key in trajectory_keys}
                for row in sorted(generation_events, key=lambda entry: int(entry["step"]))
            ],
        },
        "optimization": {
            "final_training_probe": latest_probe,
            "final_validation_signal_ablation": {
                key: value for key, value in latest_ablation.items() if key.startswith("delta/")
            },
            "latest_zero_signal_prediction_change_rate": prediction_change,
        },
        "final_validation": {
            "logged_metrics": {
                key: value
                for key, value in final_generation.items()
                if key not in {"event", "timestamp"} and not key.startswith("first_pass/")
            },
            "row_analysis": row_analysis,
        },
        "findings": findings,
        "methodology_limits": [
            "Validation was repeatedly inspected during training and is not an untouched confirmation set.",
            "The 84-row generation panel is useful for observability but too small for stable rare-slice claims.",
            "Strongest-joint, affected-joint, and evidence-interval targets measure agreement with deterministic signal rules, not physical impact location.",
            "Zeroing all channels is a coarse dependence check; it does not prove temporal or channel-grounded reasoning.",
            "Rationale lexical checks do not prove faithful reasoning.",
        ],
    }


def markdown_report(report: dict[str, object]) -> str:
    run = report["run"]
    selection = report["checkpoint_selection"]
    logged = report["final_validation"]["logged_metrics"]
    rows = report["final_validation"]["row_analysis"]
    lines = [
        "# OpenTSLM failure-mode audit",
        "",
        f"Run state: **{run['state']}** at step {run['final_step']} in {run['elapsed_hours']:.2f} hours.",
        "",
        "This is a validation-methodology audit, not a new test-set or robot-safety claim. Event semantics and manual onset are source annotations; joint attribution and evidence intervals are deterministic pseudo-labels; outputs are model predictions.",
        "",
        "## Headline",
        "",
        f"- Best token-loss step: {selection['best_validation_loss_step']} ({selection['best_validation_loss']:.4f}).",
        f"- Best observed decoded step: {selection['best_observed_decoded_step']} (selection score {selection['best_observed_decoded_score']:.4f}).",
        f"- Eligible decoded checkpoints: {selection['eligible_evaluations']}/{selection['decoded_evaluations']}.",
        f"- Final contact F1: {float(logged.get('contact_f1', 0)):.3f}; semantics macro-F1: {float(logged.get('semantics_macro_f1', 0)):.3f}.",
        f"- Final strongest-joint accuracy: {float(logged.get('strongest_joint_accuracy', 0)):.3f}; affected-joint set F1: {float(logged.get('affected_joints_set_f1', 0)):.3f}.",
        f"- Final onset MAE: {float(logged.get('onset_mae_ms', math.nan)):.1f} ms; evidence IoU: {float(logged.get('evidence_interval_iou', 0)):.3f}.",
        f"- Final schema validity: {float(logged.get('schema_exact_match', 0)):.3f}; rationale coverage: {float(logged.get('rationale_presence', 0)):.3f}.",
        "",
        "## Findings",
        "",
    ]
    for finding in report["findings"]:
        lines.extend(
            [
                f"### {finding['severity'].upper()}: {finding['code']}",
                "",
                f"Evidence: {finding['evidence']}",
                "",
                f"Implication: {finding['implication']}",
                "",
                f"Action: {finding['recommendation']}",
                "",
            ]
        )
    if rows:
        lines.extend(
            [
                "## Per-intent final behavior",
                "",
                "| Intent | n | Exact | Schema | Retry | Rationale |",
                "|---|---:|---:|---:|---:|---:|",
            ]
        )
        for intent, values in rows["per_intent"].items():
            lines.append(
                f"| {intent} | {values['n']} | {values['answer_exact_match']:.3f} | "
                f"{values['schema_validity']:.3f} | {values['retry_rate']:.3f} | {values['rationale_presence']:.3f} |"
            )
        lines.append("")
    lines.extend(["## Methodology limits", ""])
    lines.extend(f"- {limit}" for limit in report["methodology_limits"])
    lines.append("")
    return "\n".join(lines)


def write_trajectory_csv(report: dict[str, object], path: Path) -> None:
    rows = report["checkpoint_selection"]["trajectory"]
    if not rows:
        return
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--strict", action="store_true", help="Exit 2 when critical findings exist.")
    args = parser.parse_args()
    report = analyze_run(args.run_dir)
    if args.output_dir:
        args.output_dir.mkdir(parents=True, exist_ok=True)
        (args.output_dir / "failure_analysis.json").write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        (args.output_dir / "failure_analysis.md").write_text(markdown_report(report), encoding="utf-8")
        write_trajectory_csv(report, args.output_dir / "checkpoint_trajectory.csv")
    print(json.dumps(report, indent=2, sort_keys=True))
    if args.strict and any(finding["severity"] == "critical" for finding in report["findings"]):
        raise SystemExit(2)


if __name__ == "__main__":
    main()

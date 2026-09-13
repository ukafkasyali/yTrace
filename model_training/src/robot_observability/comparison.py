"""Dependency-free audit of frozen predictions; never fits or repairs a model.

Missing/invalid predictions remain abstentions. Temporal MAE is conditional on a
finite in-window estimate; success within tolerance uses all true contact events.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import random
import statistics
from collections import Counter
from pathlib import Path

CLASSES = ("free", "intentional", "accidental")
JOINTS = {f"J{i}" for i in range(1, 8)}
KEYS = {
    "contact",
    "event_type",
    "onset_ms",
    "strongest_joint",
    "affected_joints",
    "evidence_start_ms",
    "evidence_end_ms",
}
MODELS = {
    "features": "Signal features + logistic regression",
    "opentslm": "OpenTSLM canary-v4 (trained Llama 3.2 1B)",
    "qwen": "Qwen3-VL 4B, zero-shot plots",
    "zero-signal": "OpenTSLM zero-signal ablation",
}


def read_bytes(path: Path) -> bytes:
    data = path.read_bytes()
    return gzip.decompress(data) if path.suffix == ".gz" else data


def rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in read_bytes(path).splitlines() if line.strip()]


def unique_index(items: list[dict]) -> dict[str, dict]:
    result = {}
    for row in items:
        key = row["record_id"]
        if key in result:
            raise ValueError(f"Duplicate record ID: {key}")
        result[key] = row
    return result


def time_valid(value: object, *, endpoint: bool = False) -> bool:
    return (
        type(value) in (int, float)
        and math.isfinite(value)
        and 0 <= value <= 1024
        and (endpoint or value < 1024)
    )


def schema_valid(p: object) -> bool:
    if not isinstance(p, dict) or set(p) != KEYS:
        return False
    if type(p["contact"]) is not bool or p["event_type"] not in CLASSES:
        return False
    affected = p["affected_joints"]
    if (
        not isinstance(affected, list)
        or any(type(j) is not str or j not in JOINTS for j in affected)
        or len(set(affected)) != len(affected)
    ):
        return False
    if not p["contact"]:
        return (
            p["event_type"] == "free"
            and not affected
            and all(
                p[k] is None for k in ("onset_ms", "strongest_joint", "evidence_start_ms", "evidence_end_ms")
            )
        )
    return (
        p["event_type"] != "free"
        and p["strongest_joint"] in affected
        and time_valid(p["onset_ms"])
        and time_valid(p["evidence_start_ms"])
        and time_valid(p["evidence_end_ms"], endpoint=True)
        and p["evidence_start_ms"] < p["evidence_end_ms"]
    )


def percentile(values: list[float], fraction: float) -> float | None:
    """Return a linearly interpolated percentile without a NumPy dependency."""
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def score(items: list[dict]) -> dict:
    n = len(items)
    if not n:
        raise ValueError("Cannot score an empty evaluation")
    confusion = {c: Counter() for c in CLASSES}
    contact_confusion = {"false": Counter(), "true": Counter()}
    valid = parsed = contact_valid = contact_correct = joint_correct = 0
    errors = []
    event_n = 0
    affected_scores = []
    for row in items:
        target, p = row["target"], row.get("prediction")
        parsed += isinstance(p, dict)
        valid += schema_valid(p)
        p = p if isinstance(p, dict) else {}
        pred_class = p.get("event_type")
        pred_class = pred_class if isinstance(pred_class, str) and pred_class in CLASSES else "abstain"
        confusion[target["event_type"]][pred_class] += 1
        contact = p.get("contact")
        is_bool = type(contact) is bool
        contact_valid += is_bool
        contact_correct += is_bool and contact == target["contact"]
        contact_confusion[str(target["contact"]).lower()][str(contact).lower() if is_bool else "abstain"] += 1
        if target["contact"]:
            event_n += 1
            joint_correct += p.get("strongest_joint") == target["strongest_joint"]
            onset = p.get("onset_ms")
            if time_valid(onset):
                errors.append(abs(onset - target["onset_ms"]))
            affected = p.get("affected_joints")
            if (
                isinstance(affected, list)
                and all(type(j) is str and j in JOINTS for j in affected)
                and len(set(affected)) == len(affected)
            ):
                truth, predicted = set(target["affected_joints"]), set(affected)
                denominator = len(truth) + len(predicted)
                affected_scores.append(2 * len(truth & predicted) / denominator if denominator else 1.0)
            else:
                affected_scores.append(0.0)
    f1 = {}
    for c in CLASSES:
        tp = confusion[c][c]
        fp = sum(confusion[other][c] for other in CLASSES if other != c)
        fn = sum(confusion[c].values()) - tp
        f1[c] = 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else 0.0
    contact_f1 = {}
    for c in ("false", "true"):
        other = "true" if c == "false" else "false"
        tp, fp = contact_confusion[c][c], contact_confusion[other][c]
        fn = sum(contact_confusion[c].values()) - tp
        contact_f1[c] = 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else 0.0
    return {
        "n": n,
        "contact_events": event_n,
        "json_object_rate": parsed / n,
        "usable_summary_rate": valid / n,
        "semantics_macro_f1": statistics.mean(f1.values()),
        "semantics_accuracy": sum(confusion[c][c] for c in CLASSES) / n,
        "semantics_per_class_f1": f1,
        "semantics_confusion": {c: dict(confusion[c]) for c in CLASSES},
        "contact_answer_coverage": contact_valid / n,
        "contact_accuracy": contact_correct / n,
        "contact_macro_f1": statistics.mean(contact_f1.values()),
        "contact_positive_f1": contact_f1["true"],
        "contact_confusion": {c: dict(contact_confusion[c]) for c in contact_confusion},
        "strongest_joint_accuracy": joint_correct / event_n if event_n else None,
        "affected_joint_set_f1_contact_only": statistics.mean(affected_scores) if affected_scores else None,
        "onset_coverage": len(errors) / event_n if event_n else None,
        "onset_mae_ms": statistics.mean(errors) if errors else None,
        "onset_median_ae_ms": statistics.median(errors) if errors else None,
        "onset_p90_ae_ms": percentile(errors, 0.9),
        "onset_within_50ms_all_contacts": sum(e <= 50 for e in errors) / event_n if event_n else None,
    }


def validate_alignment(predictions: dict[str, list[dict]], records: list[dict], splits: dict) -> list[str]:
    prepared = unique_index(records)
    indices = {name: unique_index(items) for name, items in predictions.items()}
    ids = sorted(next(iter(indices.values())))
    if not ids:
        raise ValueError("Empty evaluation")
    for name, index in indices.items():
        if set(index) != set(ids):
            raise ValueError(f"Unequal test record IDs: {name}")
        for key in ids:
            if key not in prepared:
                raise ValueError(f"Unknown prepared record: {key}")
            record, row = prepared[key], index[key]
            if record.get("split") != "test" or splits.get(record["session_id"]) != "test":
                raise ValueError(f"Record is not in held-out test sessions: {key}")
            if row.get("session_id", record["session_id"]) != record["session_id"]:
                raise ValueError(f"Wrong session: {key}")
            target = {k: record["onset_sample"] if k == "onset_ms" else record[k] for k in KEYS}
            if row["target"] != target:
                raise ValueError(f"Target mismatch for {name}: {key}")
            row["session_id"] = record["session_id"]
    return ids


def bootstrap_difference(left: list[dict], right: list[dict], repeats: int, seed: int) -> dict:
    """Paired recording-cluster bootstrap, OpenTSLM minus feature baseline."""
    groups = {}
    right_by_id = unique_index(right)
    for row in left:
        groups.setdefault(row["session_id"], []).append((row, right_by_id[row["record_id"]]))
    sessions = sorted(groups)
    rng = random.Random(seed)
    metrics = (
        "semantics_accuracy",
        "contact_accuracy",
        "usable_summary_rate",
        "onset_within_50ms_all_contacts",
    )
    samples = {key: [] for key in metrics}
    for _ in range(repeats):
        paired = [pair for s in rng.choices(sessions, k=len(sessions)) for pair in groups[s]]
        a, b = score([p[0] for p in paired]), score([p[1] for p in paired])
        for key in metrics:
            if a[key] is not None and b[key] is not None:
                samples[key].append(a[key] - b[key])
    result = {}
    for key, values in samples.items():
        values.sort()
        result[key] = [values[int(0.025 * (len(values) - 1))], values[int(0.975 * (len(values) - 1))]]
    return {
        "method": "paired recording-cluster percentile bootstrap",
        "repeats": repeats,
        "seed": seed,
        "difference": "OpenTSLM minus signal features",
        "interval_95": result,
    }


def build_report(source: Path, repeats: int = 1000) -> dict:
    if repeats < 100:
        raise ValueError("Use at least 100 bootstrap replicates")
    inventory = json.loads((source / "inventory.json").read_text())
    for name, entry in inventory["files"].items():
        if hashlib.sha256(read_bytes(source / name)).hexdigest() != entry["sha256_uncompressed"]:
            raise ValueError(f"Source checksum mismatch: {name}")
    predictions = {name: rows(source / f"{name}.jsonl.gz") for name in MODELS}
    records = rows(source / "test-records.jsonl.gz")
    splits = json.loads((source / "splits.json").read_text())
    ids = validate_alignment(predictions, records, splits)
    records_hash = hashlib.sha256(read_bytes(source / "test-records.jsonl.gz")).hexdigest()
    for name in ("features", "qwen"):
        manifest = json.loads((source / f"{name}-manifest.json").read_text())
        if manifest["split"] != "test" or manifest["prepared_records_sha256"] != records_hash:
            raise ValueError(f"Wrong prepared records manifest: {name}")
        manifest_ids = manifest.get("record_ids") or [r["record_id"] for r in manifest["records"]]
        if len(manifest_ids) != len(ids) or set(manifest_ids) != set(ids):
            raise ValueError(f"Wrong selection manifest: {name}")
    return {
        "schema_version": 1,
        "split": "test",
        "selection_seed": 20260912,
        "window_count": len(ids),
        "recording_count": len({r["session_id"] for r in predictions["features"]}),
        "class_counts": dict(Counter(r["target"]["event_type"] for r in predictions["features"])),
        "record_ids_sha256": hashlib.sha256(("\n".join(ids) + "\n").encode()).hexdigest(),
        "checkpoint_sha256": inventory["checkpoint_sha256"],
        "source_inventory_sha256": hashlib.sha256((source / "inventory.json").read_bytes()).hexdigest(),
        "models": {name: {"label": MODELS[name], **score(items)} for name, items in predictions.items()},
        "paired_uncertainty": bootstrap_difference(
            predictions["opentslm"], predictions["features"], repeats, 20260912
        ),
    }


def markdown(report: dict) -> str:
    opentslm = report["models"]["opentslm"]
    semantics_confusion = opentslm["semantics_confusion"]
    abstentions = sum(row.get("abstain", 0) for row in semantics_confusion.values())
    free_windows = sum(semantics_confusion.get("free", {}).values())
    free_abstentions = semantics_confusion.get("free", {}).get("abstain", 0)
    answered = opentslm["n"] - abstentions
    correct_answered = sum(row.get(label, 0) for label, row in semantics_confusion.items())
    answered_accuracy = correct_answered / answered if answered else 0.0
    lines = [
        "# Trace: audited held-out comparison",
        "",
        (
            f"{report['window_count']} identical windows from {report['recording_count']} held-out recordings. "
            f"Class counts: {report['class_counts']}. All windows are 1,024 samples at 1 kHz."
        ),
        "",
        "| Task / metric | Signal features | OpenTSLM | Qwen plots | Test size / limitation |",
        "|---|---:|---:|---:|---|",
    ]
    specs = [
        (
            "Semantics macro-F1",
            "semantics_macro_f1",
            "512; three experimental classes; abstentions are errors",
        ),
        ("Semantics accuracy", "semantics_accuracy", "512; missing answers incorrect"),
        ("Contact macro-F1", "contact_macro_f1", "512; includes free-motion failures"),
        ("Contact answer coverage", "contact_answer_coverage", "512; requires a JSON boolean"),
        ("Usable complete summary", "usable_summary_rate", "512; types, ranges and cross-field consistency"),
        (
            "Strongest-joint accuracy",
            "strongest_joint_accuracy",
            "271 contact windows; derived signal target",
        ),
        (
            "Affected-joint set F1",
            "affected_joint_set_f1_contact_only",
            "271 contacts only; invalid lists score zero",
        ),
        ("Onset MAE (ms)", "onset_mae_ms", "Only finite in-window predictions; read with coverage"),
        ("Onset median error (ms)", "onset_median_ae_ms", "Same conditional denominator as MAE"),
        ("Onset P90 error (ms)", "onset_p90_ae_ms", "Same conditional denominator as MAE"),
        ("Onset coverage", "onset_coverage", "271 contact windows"),
        (
            "Onset within 50 ms / all contacts",
            "onset_within_50ms_all_contacts",
            "271; missing/invalid onsets fail",
        ),
    ]
    for title, key, note in specs:
        values = [report["models"][m][key] for m in ("features", "opentslm", "qwen")]
        rendered = ["—" if v is None else f"{v:.2f}" if key.endswith("_ms") else f"{v:.4f}" for v in values]
        lines.append(f"| {title} | " + " | ".join(rendered) + f" | {note} |")
    lines += [
        "",
        "## Interpretation",
        "",
        (
            "The signal-feature baseline outperforms this OpenTSLM checkpoint on semantics, joint ranking, "
            "and successful localization within 50 ms. OpenTSLM has higher onset coverage and slightly lower "
            "conditional mean onset error, but a worse median. These results do not establish OpenTSLM superiority."
        ),
        "",
        (
            f"OpenTSLM returned no usable structured answer for {abstentions} of {opentslm['n']} windows. "
            f"That includes {free_abstentions} of {free_windows} free-motion windows "
            f"({free_abstentions / free_windows:.1%}). On the {answered} answered windows, semantics accuracy "
            f"is {answered_accuracy:.2%} ({correct_answered}/{answered}). This conditional figure is descriptive, "
            "not a paired comparison: the model selects which windows receive an answer, while the baseline "
            "answers every window. Any reliability fix must be selected on validation."
        ),
        "",
        (
            "OpenTSLM was fine-tuned; Qwen3-VL 4B was zero-shot on plots. This comparison changes training "
            "and representation together. Qwen frequently emitted null contact fields: JSON parsing and matching "
            "keys did not mean usable answers. No plain-Llama baseline was run in these artifacts."
        ),
        "",
        (
            "Legacy positive-contact F1 can stay high while free-motion answers are missing. This report adds "
            "contact macro-F1, answer coverage, strict usable-summary rate, and abstention columns in the JSON "
            "confusion matrices. It does not repair generations or substitute labels."
        ),
        "",
        "## Paired uncertainty",
        "",
        (
            "95% percentile intervals for OpenTSLM minus signal features, resampling recording groups "
            f"({report['paired_uncertainty']['repeats']} replicates). Negative values favor signal features."
        ),
        "",
    ]
    for key, interval in report["paired_uncertainty"]["interval_95"].items():
        lines.append(f"- {key}: [{interval[0]:.4f}, {interval[1]:.4f}]")
    lines += [
        "",
        "## Scope and provenance",
        "",
        (
            "The 512 windows are a fixed subset of 4,202 test windows. The source split assigns 311/67/67 "
            "recordings to train/validation/test. Every prediction is joined by record ID, checked against the "
            "prepared target and test-session assignment, and matched across all four input conditions. "
            "Archived input bytes are checksum-verified before scoring. The OpenTSLM evaluation did not "
            "originally emit an input manifest; identity is reconstructed from its prediction IDs and targets."
        ),
        "",
        (
            "One robot, controlled interactions, unknown subject identities, and implement/class confounding "
            "limit generalization. Joint and evidence targets are formulas, not physical contact-location truth. "
            "Previously inspected test results are retrospective evidence, not a new untouched confirmation set. "
            "Select any further model changes on validation; reserve new recording groups for future confirmation."
        ),
        "",
        f"Checkpoint SHA-256: `{report['checkpoint_sha256']}`.",
        "",
        "Reproduce from repository root (Python standard library only):",
        "",
        "```sh",
        "PYTHONPATH=model_training/src python3 -m robot_observability.comparison \\",
        "  --source docs/submission/evaluation/source \\",
        "  --output /tmp/trace-comparison",
        "```",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--bootstrap-repeats", type=int, default=1000)
    args = parser.parse_args()
    report = build_report(args.source, args.bootstrap_repeats)
    args.output.mkdir(parents=True, exist_ok=False)
    (args.output / "comparison.json").write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    (args.output / "comparison.md").write_text(markdown(report))
    print(
        json.dumps(
            {
                "windows": report["window_count"],
                "recordings": report["recording_count"],
                "output": str(args.output),
            }
        )
    )


if __name__ == "__main__":
    main()

"""Strict parsing and task metrics for generative outputs."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable

import numpy as np
from sklearn.metrics import accuracy_score, f1_score

_ANSWER = re.compile(r"Answer:\s*", re.IGNORECASE)
_EVENT_TYPES = {"free", "intentional", "accidental"}
_JOINTS = {f"J{index}" for index in range(1, 8)}
_RATIONALE = re.compile(r"Rationale:\s*(.*?)(?=\n\s*Answer:)", re.IGNORECASE | re.DOTALL)
_MS_VALUE = re.compile(r"(?<![A-Za-z0-9.])(\d+(?:\.\d+)?)\s*(?:ms|milliseconds?)\b", re.IGNORECASE)
_JOINT_MENTION = re.compile(r"\bJ([1-7])\b", re.IGNORECASE)


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def _time_value(value: object, *, endpoint: bool = False) -> bool:
    return (
        type(value) in (int, float)
        and np.isfinite(value)
        and 0 <= value <= 1024
        and (endpoint or value < 1024)
    )


def schema_value_valid(prediction: object, target: dict[str, object]) -> bool:
    """Validate the typed/domain contract for the fields requested by an intent."""
    if not isinstance(prediction, dict) or set(prediction) != set(target):
        return False
    if "contact" in prediction and type(prediction["contact"]) is not bool:
        return False
    if "event_type" in prediction and prediction["event_type"] not in _EVENT_TYPES:
        return False
    if (
        "onset_ms" in prediction
        and prediction["onset_ms"] is not None
        and not _time_value(prediction["onset_ms"])
    ):
        return False
    if "strongest_joint" in prediction:
        joint = prediction["strongest_joint"]
        if joint is not None and joint not in _JOINTS:
            return False
    if "affected_joints" in prediction:
        affected = prediction["affected_joints"]
        if (
            not isinstance(affected, list)
            or any(type(joint) is not str or joint not in _JOINTS for joint in affected)
            or len(set(affected)) != len(affected)
        ):
            return False
    if (
        "evidence_start_ms" in prediction
        and prediction["evidence_start_ms"] is not None
        and not _time_value(prediction["evidence_start_ms"])
    ):
        return False
    if (
        "evidence_end_ms" in prediction
        and prediction["evidence_end_ms"] is not None
        and not _time_value(prediction["evidence_end_ms"], endpoint=True)
    ):
        return False
    if {"evidence_start_ms", "evidence_end_ms"} <= set(prediction):
        start, end = prediction["evidence_start_ms"], prediction["evidence_end_ms"]
        if (start is None) != (end is None) or (start is not None and not start < end):
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


def parse_answer(text: str) -> dict[str, object] | None:
    matches = list(_ANSWER.finditer(text))
    start = matches[-1].end() if matches else text.find("{")
    if start < 0:
        return None
    brace = text.find("{", start)
    if brace < 0:
        return None
    try:
        value, _ = json.JSONDecoder(object_pairs_hook=_unique_object).raw_decode(text[brace:])
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _rationale_consistency(
    items: list[dict[str, object]], parsed: list[dict[str, object] | None]
) -> dict[str, float | int]:
    rationales = []
    for item, prediction in zip(items, parsed):
        match = _RATIONALE.search(str(item.get("output", "")))
        rationales.append((match.group(1).strip() if match else "", prediction, item["target"]))
    present = [bool(text) for text, _, _ in rationales]
    result: dict[str, float | int] = {
        "rationale_presence": float(np.mean(present)) if present else 0.0,
    }

    onset_rows = [row for row in rationales if row[2].get("onset_ms") is not None]
    onset_supported = []
    onset_answer_consistent = []
    onset_target_consistent = []
    for text, prediction, target in onset_rows:
        values = [float(value) for value in _MS_VALUE.findall(text)]
        onset_supported.append(bool(values))
        if values and isinstance(prediction, dict) and prediction.get("onset_ms") is not None:
            onset_answer_consistent.append(
                min(abs(value - float(prediction["onset_ms"])) for value in values) <= 50
            )
        if values:
            onset_target_consistent.append(
                min(abs(value - float(target["onset_ms"])) for value in values) <= 50
            )
    if onset_rows:
        result["rationale_onset_n"] = len(onset_rows)
        result["rationale_onset_support_coverage"] = float(np.mean(onset_supported))
    if onset_answer_consistent:
        result["rationale_onset_answer_consistency_50ms"] = float(np.mean(onset_answer_consistent))
    if onset_target_consistent:
        result["rationale_onset_target_consistency_50ms"] = float(np.mean(onset_target_consistent))

    joint_rows = [row for row in rationales if row[2].get("strongest_joint") is not None]
    joint_supported = []
    joint_answer_consistent = []
    joint_target_consistent = []
    for text, prediction, target in joint_rows:
        mentions = {f"J{value}" for value in _JOINT_MENTION.findall(text)}
        joint_supported.append(bool(mentions))
        if mentions and isinstance(prediction, dict) and prediction.get("strongest_joint") is not None:
            joint_answer_consistent.append(prediction["strongest_joint"] in mentions)
        if mentions:
            joint_target_consistent.append(target["strongest_joint"] in mentions)
    if joint_rows:
        result["rationale_joint_n"] = len(joint_rows)
        result["rationale_joint_support_coverage"] = float(np.mean(joint_supported))
    if joint_answer_consistent:
        result["rationale_joint_answer_consistency"] = float(np.mean(joint_answer_consistent))
    if joint_target_consistent:
        result["rationale_joint_target_consistency"] = float(np.mean(joint_target_consistent))
    return result


def evaluate_rows(rows: Iterable[dict[str, object]]) -> dict[str, float | int]:
    items = list(rows)
    parsed = [
        item.get("prediction")
        if isinstance(item.get("prediction"), dict)
        else parse_answer(str(item.get("output", "")))
        for item in items
    ]
    metrics: dict[str, float | int] = {
        "n": len(items),
        "parse_validity": sum(prediction is not None for prediction in parsed) / len(items) if items else 0.0,
        "schema_key_exact_match": (
            float(
                np.mean(
                    [
                        isinstance(prediction, dict) and set(prediction) == set(item["target"])
                        for item, prediction in zip(items, parsed)
                    ]
                )
            )
            if items
            else 0.0
        ),
        "schema_exact_match": (
            float(
                np.mean(
                    [
                        schema_value_valid(prediction, item["target"])
                        for item, prediction in zip(items, parsed)
                    ]
                )
            )
            if items
            else 0.0
        ),
        "answer_exact_match": (
            float(
                np.mean(
                    [
                        isinstance(prediction, dict) and prediction == item["target"]
                        for item, prediction in zip(items, parsed)
                    ]
                )
            )
            if items
            else 0.0
        ),
    }
    metrics.update(_rationale_consistency(items, parsed))
    if not items:
        return metrics

    def pairs(key: str) -> tuple[list[object], list[object]]:
        selected = [
            (
                item["target"].get(key),
                prediction.get(key) if isinstance(prediction, dict) else None,
            )
            for item, prediction in zip(items, parsed)
            if key in item["target"]
        ]
        return [pair[0] for pair in selected], [pair[1] for pair in selected]

    y_true, y_pred = pairs("contact")
    if y_true:
        metrics["contact_n"] = len(y_true)
        contact_true = np.asarray([value is True for value in y_true], dtype=bool)
        contact_pred = np.asarray([value is True for value in y_pred], dtype=bool)
        metrics["contact_accuracy"] = float(
            np.mean(
                [
                    isinstance(prediction, bool) and prediction == truth
                    for truth, prediction in zip(y_true, y_pred)
                ]
            )
        )
        metrics["contact_f1"] = float(f1_score(contact_true, contact_pred, pos_label=True, zero_division=0))
    y_true, y_pred = pairs("event_type")
    if y_true:
        metrics["semantics_n"] = len(y_true)
        semantics_true = np.asarray([str(value) for value in y_true], dtype=str)
        semantics_pred = np.asarray([str(value) for value in y_pred], dtype=str)
        metrics["semantics_accuracy"] = float(accuracy_score(semantics_true, semantics_pred))
        metrics["semantics_macro_f1"] = float(
            f1_score(
                semantics_true,
                semantics_pred,
                labels=("free", "intentional", "accidental"),
                average="macro",
                zero_division=0,
            )
        )
    y_true, y_pred = pairs("strongest_joint")
    event_pairs = [(truth, pred) for truth, pred in zip(y_true, y_pred) if truth is not None]
    if event_pairs:
        metrics["strongest_joint_n"] = len(event_pairs)
        metrics["strongest_joint_accuracy"] = float(
            accuracy_score(
                np.asarray([str(pair[0]) for pair in event_pairs], dtype=str),
                np.asarray([str(pair[1]) for pair in event_pairs], dtype=str),
            )
        )
        per_joint_accuracy = []
        for joint in sorted(_JOINTS):
            joint_pairs = [pair for pair in event_pairs if pair[0] == joint]
            if joint_pairs:
                accuracy = float(np.mean([prediction == joint for _, prediction in joint_pairs]))
                metrics[f"strongest_joint/{joint}_accuracy"] = accuracy
                metrics[f"strongest_joint/{joint}_n"] = len(joint_pairs)
                per_joint_accuracy.append(accuracy)
        metrics["strongest_joint_macro_accuracy"] = float(np.mean(per_joint_accuracy))
        tail_pairs = [pair for pair in event_pairs if pair[0] in {"J5", "J6", "J7"}]
        if tail_pairs:
            metrics["strongest_joint_tail_n"] = len(tail_pairs)
            metrics["strongest_joint_tail_accuracy"] = float(
                np.mean([truth == prediction for truth, prediction in tail_pairs])
            )
    y_true, y_pred = pairs("onset_ms")
    contact_onsets = [(truth, pred) for truth, pred in zip(y_true, y_pred) if truth is not None]
    errors = []
    for truth, prediction in contact_onsets:
        try:
            errors.append(abs(float(prediction) - float(truth)))
        except (TypeError, ValueError):
            continue
    if contact_onsets:
        metrics["onset_n"] = len(contact_onsets)
        metrics["onset_coverage"] = len(errors) / len(contact_onsets)
    if errors:
        metrics["onset_mae_ms"] = float(np.mean(errors))
        metrics["onset_median_ae_ms"] = float(np.median(errors))
        metrics["onset_p90_ae_ms"] = float(np.quantile(errors, 0.9))
        for tolerance in (10, 25, 50):
            metrics[f"onset_within_{tolerance}ms"] = float(np.mean(np.asarray(errors) <= tolerance))

    y_true, y_pred = pairs("affected_joints")
    if y_true:
        metrics["affected_joints_n"] = len(y_true)
        exact = []
        set_f1 = []
        for truth, prediction in zip(y_true, y_pred):
            truth_list = list(truth) if isinstance(truth, list) else []
            prediction_list = list(prediction) if isinstance(prediction, list) else []
            exact.append(isinstance(prediction, list) and prediction_list == truth_list)
            truth_set = set(truth_list)
            prediction_set = set(prediction_list)
            if not truth_set and not prediction_set:
                set_f1.append(1.0)
            elif not truth_set or not prediction_set:
                set_f1.append(0.0)
            else:
                precision = len(truth_set & prediction_set) / len(prediction_set)
                recall = len(truth_set & prediction_set) / len(truth_set)
                set_f1.append(2 * precision * recall / (precision + recall) if precision + recall else 0.0)
        metrics["affected_joints_exact_match"] = float(np.mean(exact))
        metrics["affected_joints_set_f1"] = float(np.mean(set_f1))

    starts_true, starts_pred = pairs("evidence_start_ms")
    ends_true, ends_pred = pairs("evidence_end_ms")
    intervals = [
        (start, end, predicted_start, predicted_end)
        for start, end, predicted_start, predicted_end in zip(starts_true, ends_true, starts_pred, ends_pred)
        if start is not None and end is not None
    ]
    interval_errors = []
    interval_ious = []
    for start, end, predicted_start, predicted_end in intervals:
        try:
            start = float(start)
            end = float(end)
            predicted_start = float(predicted_start)
            predicted_end = float(predicted_end)
        except (TypeError, ValueError):
            continue
        interval_errors.append((abs(predicted_start - start), abs(predicted_end - end)))
        intersection = max(0.0, min(end, predicted_end) - max(start, predicted_start))
        union = max(end, predicted_end) - min(start, predicted_start)
        interval_ious.append(intersection / union if union > 0 else float(start == predicted_start))
    if intervals:
        metrics["evidence_interval_n"] = len(intervals)
        metrics["evidence_interval_coverage"] = len(interval_errors) / len(intervals)
    if interval_errors:
        metrics["evidence_start_mae_ms"] = float(np.mean([error[0] for error in interval_errors]))
        metrics["evidence_end_mae_ms"] = float(np.mean([error[1] for error in interval_errors]))
        metrics["evidence_interval_iou"] = float(np.mean(interval_ious))
    return metrics

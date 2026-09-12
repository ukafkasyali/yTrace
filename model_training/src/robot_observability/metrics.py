"""Strict parsing and task metrics for generative outputs."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable

import numpy as np
from sklearn.metrics import accuracy_score, f1_score

_ANSWER = re.compile(r"Answer:\s*", re.IGNORECASE)


def parse_answer(text: str) -> dict[str, object] | None:
    matches = list(_ANSWER.finditer(text))
    start = matches[-1].end() if matches else text.find("{")
    if start < 0:
        return None
    brace = text.find("{", start)
    if brace < 0:
        return None
    try:
        value, _ = json.JSONDecoder().raw_decode(text[brace:])
    except (json.JSONDecodeError, TypeError):
        return None
    return value if isinstance(value, dict) else None


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
        "schema_exact_match": (
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

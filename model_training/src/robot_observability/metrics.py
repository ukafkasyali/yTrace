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
        metrics["onset_coverage"] = len(errors) / len(contact_onsets)
    if errors:
        metrics["onset_mae_ms"] = float(np.mean(errors))
        metrics["onset_median_ae_ms"] = float(np.median(errors))
        metrics["onset_p90_ae_ms"] = float(np.quantile(errors, 0.9))
        for tolerance in (10, 25, 50):
            metrics[f"onset_within_{tolerance}ms"] = float(np.mean(np.asarray(errors) <= tolerance))
    return metrics

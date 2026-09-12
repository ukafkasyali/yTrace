"""Transparent signal-feature baseline on the identical held-out windows."""

from __future__ import annotations

import json
from pathlib import Path

import joblib
import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import f1_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from robot_observability.constants import JOINT_NAMES
from robot_observability.data.pseudolabels import top_fraction_mean
from robot_observability.metrics import evaluate_rows
from robot_observability.prepared import PreparedSplit
from robot_observability.qa import answer_payload


def extract_features(signal: np.ndarray, baseline_samples: int = 150) -> np.ndarray:
    """Handcrafted per-joint distribution, impulse, and motion features."""
    baseline = np.median(signal[:, :baseline_samples], axis=1, keepdims=True)
    residual = np.abs(signal - baseline)
    top = top_fraction_mean(residual, 0.05, axis=1)
    peak = residual.max(axis=1)
    rms = np.sqrt(np.square(residual).mean(axis=1))
    derivative_rms = np.sqrt(np.square(np.diff(signal, axis=1)).mean(axis=1))
    impulsiveness = peak / np.maximum(rms, 1e-6)
    return np.concatenate([top, peak, rms, derivative_rms, impulsiveness]).astype(np.float32)


def _contact_scores(signal: np.ndarray, baseline_samples: int = 150) -> np.ndarray:
    baseline = np.median(signal[:, :baseline_samples], axis=1, keepdims=True)
    return np.abs(signal - baseline).max(axis=0)


def contact_detection_score(signal: np.ndarray, sustain: int = 10) -> float:
    """Highest threshold sustained for at least ``sustain`` consecutive samples."""
    scores = _contact_scores(signal)
    rolling_minimum = np.asarray(
        [scores[index : index + sustain].min() for index in range(len(scores) - sustain + 1)]
    )
    return float(rolling_minimum.max())


def fit_contact_threshold(dataset: PreparedSplit, sustain: int = 10) -> float:
    peaks = np.asarray([contact_detection_score(signal, sustain) for signal, _ in dataset], dtype=np.float64)
    targets = np.asarray([bool(metadata["contact"]) for _, metadata in dataset])
    candidates = np.unique(np.quantile(peaks, np.linspace(0.05, 0.995, 300)))
    best_threshold, best_f1 = float(candidates[0]), -1.0
    for threshold in candidates:
        predictions = peaks >= threshold
        score = f1_score(targets, predictions, zero_division=0)
        if score > best_f1:
            best_threshold, best_f1 = float(threshold), float(score)
    return best_threshold


def detect_onset(
    signal: np.ndarray, threshold: float, sustain: int = 10, search_start: int = 150
) -> int | None:
    scores = _contact_scores(signal)
    above = scores >= threshold
    sustained = np.convolve(above.astype(np.int16), np.ones(sustain, dtype=np.int16), mode="valid") >= sustain
    candidates = np.flatnonzero(sustained & (np.arange(len(sustained)) >= search_start))
    return int(candidates[0]) if candidates.size else None


def evidence_from_prediction(
    signal: np.ndarray, onset: int | None, affected_thresholds: np.ndarray, post_samples: int = 250
) -> dict[str, object]:
    if onset is None:
        return {
            "strongest_joint": None,
            "affected_joints": [],
            "evidence_start_ms": None,
            "evidence_end_ms": None,
        }
    baseline = np.median(signal[:, max(0, onset - 200) : onset], axis=1, keepdims=True)
    residual = np.abs(signal[:, onset : min(signal.shape[1], onset + post_samples)] - baseline)
    raw_scores = top_fraction_mean(residual, 0.05, axis=1)
    calibrated = raw_scores / np.maximum(affected_thresholds, 1e-6)
    order = np.argsort(-calibrated)
    affected = [JOINT_NAMES[index] for index in order if calibrated[index] >= 1.0]
    if not affected:
        affected = [JOINT_NAMES[int(order[0])]]
    aggregate = (residual / np.maximum(affected_thresholds[:, None], 1e-6)).max(axis=0)
    active = np.flatnonzero(aggregate >= 1.0)
    return {
        "strongest_joint": JOINT_NAMES[int(order[0])],
        "affected_joints": affected,
        "evidence_start_ms": onset + int(active[0]) if active.size else onset,
        "evidence_end_ms": onset + int(active[-1]) + 1
        if active.size
        else min(signal.shape[1], onset + post_samples),
    }


def run_baseline(prepared_root: Path, output_root: Path) -> dict[str, float | int]:
    output_root.mkdir(parents=True, exist_ok=True)
    train = PreparedSplit(prepared_root, "train")
    test = PreparedSplit(prepared_root, "test")
    train_x = np.stack([extract_features(signal) for signal, _ in train])
    train_y = np.asarray([metadata["event_type"] for _, metadata in train])
    classifier = make_pipeline(
        StandardScaler(),
        LogisticRegression(max_iter=2000, class_weight="balanced", random_state=20260912),
    )
    classifier.fit(train_x, train_y)
    threshold = fit_contact_threshold(train)
    normalization = json.loads((prepared_root / "normalization.json").read_text(encoding="utf-8"))
    affected_thresholds = np.asarray(normalization["affected_joint_train_free_q99"], dtype=np.float32)

    rows = []
    output_path = output_root / "test_predictions.jsonl"
    with output_path.open("w", encoding="utf-8") as handle:
        for signal, metadata in test:
            semantics = str(classifier.predict(extract_features(signal)[None])[0])
            onset = detect_onset(signal, threshold) if semantics != "free" else None
            evidence = evidence_from_prediction(signal, onset, affected_thresholds)
            prediction = {
                "contact": semantics != "free",
                "event_type": semantics,
                "onset_ms": onset,
                **evidence,
            }
            row = {
                "record_id": metadata["record_id"],
                "session_id": metadata["session_id"],
                "target": answer_payload(metadata, "summary"),
                "prediction": prediction,
            }
            rows.append(row)
            handle.write(json.dumps(row, sort_keys=True) + "\n")

    metrics = evaluate_rows(rows)
    metrics["contact_threshold"] = threshold
    metrics["split_unit"] = "recording_session"
    (output_root / "metrics.json").write_text(
        json.dumps(metrics, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    joblib.dump(classifier, output_root / "semantics_classifier.joblib")
    return metrics

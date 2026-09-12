"""Deterministic evidence labels derived from torque, not physical ground truth."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from robot_observability.constants import JOINT_NAMES


@dataclass(frozen=True)
class EvidenceLabels:
    joint_scores: tuple[float, ...]
    strongest_joint: str
    affected_joints: tuple[str, ...]
    evidence_start_ms: int
    evidence_end_ms: int
    score_margin: float


def top_fraction_mean(values: np.ndarray, fraction: float = 0.05, axis: int = 0) -> np.ndarray:
    count = max(1, int(np.ceil(values.shape[axis] * fraction)))
    partitioned = np.partition(values, values.shape[axis] - count, axis=axis)
    indices = np.arange(values.shape[axis] - count, values.shape[axis])
    return np.take(partitioned, indices, axis=axis).mean(axis=axis)


def derive_evidence_labels(
    window: np.ndarray,
    onset_sample: int,
    train_joint_scale: np.ndarray,
    *,
    pre_samples: int = 200,
    post_samples: int = 250,
    affected_threshold: float | np.ndarray = 3.0,
    sustain_samples: int = 10,
) -> EvidenceLabels:
    """Calculate robust joint evidence using only pre-onset baseline values.

    The top-5%-mean is the canonical strongest-joint target.  It is resistant
    to a single-sample spike and must be described as a pseudo-label.
    """
    if window.ndim != 2 or window.shape[1] != len(JOINT_NAMES):
        raise ValueError(f"Expected [time, 7] window, got {window.shape}")
    if not 1 <= onset_sample < len(window):
        raise ValueError("onset_sample must have pre-event and post-event context")
    pre = window[max(0, onset_sample - pre_samples) : onset_sample]
    baseline = np.median(pre, axis=0)
    scale = np.maximum(np.asarray(train_joint_scale, dtype=np.float64), 1e-6)
    post_stop = min(len(window), onset_sample + post_samples)
    z = np.abs(window[onset_sample:post_stop] - baseline) / scale
    raw_scores = np.asarray(top_fraction_mean(z, fraction=0.05, axis=0), dtype=np.float64)
    thresholds = np.broadcast_to(np.asarray(affected_threshold, dtype=np.float64), raw_scores.shape)
    calibrated_scores = raw_scores / np.maximum(thresholds, 1e-6)
    order = np.argsort(-calibrated_scores)
    strongest = int(order[0])
    affected = tuple(JOINT_NAMES[i] for i in order if calibrated_scores[i] >= 1.0)
    if not affected:
        affected = (JOINT_NAMES[strongest],)

    aggregate = (z / np.maximum(thresholds, 1e-6)).max(axis=1)
    above = aggregate >= 1.0
    kernel = np.ones(sustain_samples, dtype=np.int16)
    sustained = np.convolve(above.astype(np.int16), kernel, mode="valid") >= sustain_samples
    if sustained.any():
        start_rel = int(np.flatnonzero(sustained)[0])
        end_candidates = np.flatnonzero(above[start_rel:])
        end_rel = (
            int(start_rel + end_candidates[-1] + 1) if end_candidates.size else start_rel + sustain_samples
        )
    else:
        start_rel, end_rel = 0, min(post_samples, len(aggregate))

    margin = (
        float(calibrated_scores[order[0]] - calibrated_scores[order[1]])
        if len(calibrated_scores) > 1
        else float(calibrated_scores[order[0]])
    )
    return EvidenceLabels(
        joint_scores=tuple(float(value) for value in calibrated_scores),
        strongest_joint=JOINT_NAMES[strongest],
        affected_joints=affected,
        evidence_start_ms=onset_sample + start_rel,
        evidence_end_ms=onset_sample + end_rel,
        score_margin=margin,
    )

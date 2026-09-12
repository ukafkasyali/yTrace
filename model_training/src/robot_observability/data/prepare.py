"""Create leakage-safe, memory-mapped training windows from raw sessions."""

from __future__ import annotations

import hashlib
import json
import random
from collections.abc import Callable, Iterator
from dataclasses import asdict, dataclass
from itertools import pairwise
from pathlib import Path

import numpy as np

from robot_observability.config import DataConfig
from robot_observability.constants import JOINT_NAMES
from robot_observability.data.pseudolabels import derive_evidence_labels, top_fraction_mean
from robot_observability.data.raw import RawSession, SessionRef, discover_sessions, load_session
from robot_observability.data.splits import stratified_session_split


@dataclass(frozen=True)
class WindowSpec:
    record_id: str
    session_id: str
    event_type: str
    split: str
    start_sample: int
    event_sample_absolute: int | None
    onset_sample: int | None
    crop_index: int


def _stable_rng(seed: int, *parts: object) -> random.Random:
    material = ":".join([str(seed), *(str(part) for part in parts)]).encode()
    return random.Random(int.from_bytes(hashlib.sha256(material).digest()[:8], "big"))


def _event_windows(session: RawSession, split: str, config: DataConfig) -> Iterator[WindowSpec]:
    crops = config.train_crops_per_event if split == "train" else config.eval_crops_per_event
    for event_index, marker in enumerate(session.event_samples):
        for crop_index in range(crops):
            rng = _stable_rng(config.seed, session.ref.session_id, event_index, crop_index)
            onset = rng.randint(config.event_position_min, config.event_position_max)
            start = int(marker) - onset
            stop = start + config.window_samples
            if start < 0 or stop > len(session.torque_nm):
                continue
            yield WindowSpec(
                record_id=f"{session.ref.session_id}/event-{event_index:04d}/crop-{crop_index}",
                session_id=session.ref.session_id,
                event_type=session.ref.event_type,
                split=split,
                start_sample=start,
                event_sample_absolute=int(marker),
                onset_sample=onset,
                crop_index=crop_index,
            )


def _hard_free_windows(session: RawSession, split: str, config: DataConfig) -> Iterator[WindowSpec]:
    """Select one high-motion negative between adjacent, well-separated events."""
    markers = session.event_samples
    for interval_index, (left, right) in enumerate(pairwise(markers)):
        first_start = int(left) + config.free_guard_samples
        last_start = int(right) - config.free_guard_samples - config.window_samples
        if first_start > last_start:
            continue
        candidates = np.arange(
            first_start, last_start + 1, max(64, config.window_samples // 4), dtype=np.int64
        )
        if not candidates.size:
            continue
        # High derivative energy produces a harder free-motion negative than a quiet midpoint.
        energies = []
        for start in candidates:
            window = session.torque_nm[start : start + config.window_samples]
            energies.append(float(np.square(np.diff(window, axis=0)).mean()))
        start = int(candidates[int(np.argmax(energies))])
        yield WindowSpec(
            record_id=f"{session.ref.session_id}/free-{interval_index:04d}",
            session_id=session.ref.session_id,
            event_type="free",
            split=split,
            start_sample=start,
            event_sample_absolute=None,
            onset_sample=None,
            crop_index=0,
        )


def _session_specs(session: RawSession, split: str, config: DataConfig) -> list[WindowSpec]:
    return [*_event_windows(session, split, config), *_hard_free_windows(session, split, config)]


def calculate_train_normalization(
    sessions: list[SessionRef],
    split_map: dict[str, str],
    config: DataConfig,
    *,
    loader: Callable[[SessionRef, float], RawSession] = load_session,
) -> tuple[np.ndarray, np.ndarray]:
    sampled: list[np.ndarray] = []
    for ref in sessions:
        if split_map[ref.session_id] != "train":
            continue
        values = loader(ref, config.sampling_hz).torque_nm[:: config.normalization_sample_stride]
        sampled.append(values)
    combined = np.concatenate(sampled, axis=0)
    center = np.median(combined, axis=0)
    scale = 1.4826 * np.median(np.abs(combined - center), axis=0)
    scale = np.maximum(scale, 1e-6)
    return center.astype(np.float32), scale.astype(np.float32)


def _free_joint_thresholds(
    sessions: list[SessionRef],
    split_map: dict[str, str],
    train_scale: np.ndarray,
    config: DataConfig,
    *,
    loader: Callable[[SessionRef, float], RawSession] = load_session,
) -> np.ndarray:
    free_scores: list[np.ndarray] = []
    for ref in sessions:
        if split_map[ref.session_id] != "train":
            continue
        session = loader(ref, config.sampling_hz)
        for spec in _hard_free_windows(session, "train", config):
            window = session.torque_nm[spec.start_sample : spec.start_sample + config.window_samples]
            baseline = np.median(window[: config.pre_event_samples], axis=0)
            z = np.abs(window - baseline) / train_scale
            free_scores.append(top_fraction_mean(z, fraction=0.05, axis=0))
    if not free_scores:
        raise ValueError("No train free-motion windows available for evidence calibration")
    return np.maximum(np.quantile(np.stack(free_scores), 0.99, axis=0), 3.0).astype(np.float32)


def _metadata_for_window(
    spec: WindowSpec,
    raw_window: np.ndarray,
    train_scale: np.ndarray,
    affected_thresholds: np.ndarray,
) -> dict[str, object]:
    metadata: dict[str, object] = {
        **asdict(spec),
        "sampling_hz": 1000,
        "window_samples": len(raw_window),
        "raw_mean_nm": raw_window.mean(axis=0).tolist(),
        "raw_std_nm": raw_window.std(axis=0).tolist(),
        "raw_rms_nm": np.sqrt(np.square(raw_window).mean(axis=0)).tolist(),
        "raw_max_abs_nm": np.abs(raw_window).max(axis=0).tolist(),
    }
    if spec.onset_sample is None:
        metadata.update(
            contact=False,
            strongest_joint=None,
            affected_joints=[],
            joint_scores=[0.0] * len(JOINT_NAMES),
            evidence_start_ms=None,
            evidence_end_ms=None,
            score_margin=None,
        )
        return metadata

    evidence = derive_evidence_labels(
        raw_window,
        spec.onset_sample,
        train_scale,
        affected_threshold=affected_thresholds,
    )
    metadata.update(contact=True, **asdict(evidence))
    return metadata


def _prepare_sessions(
    config: DataConfig,
    sessions: list[SessionRef],
    *,
    loader: Callable[[SessionRef, float], RawSession],
    source_receipt: dict[str, object],
) -> dict[str, object]:
    """Prepare normalized mmap arrays from validated continuous sessions."""
    output = config.prepared_root
    if output.exists() and any(output.iterdir()):
        raise FileExistsError(f"Prepared output already exists and is non-empty: {output}")
    output.mkdir(parents=True, exist_ok=True)

    if not sessions:
        raise FileNotFoundError("No source sessions were discovered")
    split_map = stratified_session_split(
        sessions,
        train_fraction=config.split.train,
        validation_fraction=config.split.validation,
        seed=config.seed,
    )
    (output / "splits.json").write_text(
        json.dumps(split_map, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    center, scale = calculate_train_normalization(sessions, split_map, config, loader=loader)
    thresholds = _free_joint_thresholds(sessions, split_map, scale, config, loader=loader)
    normalization = {
        "method": "train-session robust median/MAD; no per-window normalization",
        "center_nm": center.tolist(),
        "scale_nm": scale.tolist(),
        "affected_joint_train_free_q99": thresholds.tolist(),
        "clip": config.normalization_clip,
    }
    (output / "normalization.json").write_text(
        json.dumps(normalization, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    counts: dict[str, dict[str, int]] = {}
    refs_by_id: dict[str, SessionRef] = {ref.session_id: ref for ref in sessions}
    for split in ("train", "validation", "test"):
        specs: list[WindowSpec] = []
        loaded: dict[str, RawSession] = {}
        for session_id, assigned_split in split_map.items():
            if assigned_split != split:
                continue
            session = loader(refs_by_id[session_id], config.sampling_hz)
            loaded[session_id] = session
            specs.extend(_session_specs(session, split, config))
        split_dir = output / split
        split_dir.mkdir()
        signals = np.lib.format.open_memmap(
            split_dir / "signals.npy",
            mode="w+",
            dtype=np.float32,
            shape=(len(specs), len(JOINT_NAMES), config.window_samples),
        )
        label_counts = {"free": 0, "intentional": 0, "accidental": 0}
        with (split_dir / "records.jsonl").open("w", encoding="utf-8") as metadata_file:
            for index, spec in enumerate(specs):
                session = loaded[spec.session_id]
                raw_window = session.torque_nm[spec.start_sample : spec.start_sample + config.window_samples]
                normalized = np.clip(
                    (raw_window - center) / scale, -config.normalization_clip, config.normalization_clip
                )
                signals[index] = normalized.T
                metadata = _metadata_for_window(spec, raw_window, scale, thresholds)
                metadata["row_index"] = index
                metadata_file.write(json.dumps(metadata, sort_keys=True) + "\n")
                label_counts[spec.event_type] += 1
        signals.flush()
        counts[split] = label_counts

    summary: dict[str, object] = {
        "source_backend": source_receipt["source_backend"],
        "sessions": len(sessions),
        "session_counts": {
            split: sum(value == split for value in split_map.values())
            for split in ("train", "validation", "test")
        },
        "window_counts": counts,
        "limitations": [
            "One KUKA LWR4+ platform; device transfer is untested.",
            "Subject identifiers are unavailable; subject-disjoint evaluation cannot be certified.",
            "Joint attribution and evidence intervals are deterministic pseudo-labels, not physical impact localization.",
            "The model performs retrospective diagnosis on a complete window, not causal safety control.",
        ],
    }
    (output / "dataset_summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (output / "source_receipt.json").write_text(
        json.dumps(source_receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return summary


def prepare_dataset(config: DataConfig) -> dict[str, object]:
    """Prepare windows directly from MATLAB (legacy compatibility path)."""
    sessions = discover_sessions(config.raw_root)
    return _prepare_sessions(
        config,
        sessions,
        loader=load_session,
        source_receipt={
            "source_backend": "raw_matlab",
            "raw_root": str(config.raw_root.expanduser().resolve()),
        },
    )


def prepare_timef_dataset(config: DataConfig, version_dirs: list[Path]) -> dict[str, object]:
    """Prepare training windows only after data passes through canonical TimeNet/TimeF."""
    from robot_observability.data.timef import (
        discover_timef_sessions,
        load_timef_session,
        timef_source_receipt,
    )

    sessions = discover_timef_sessions(version_dirs)
    return _prepare_sessions(
        config,
        sessions,
        loader=load_timef_session,
        source_receipt=timef_source_receipt(version_dirs),
    )

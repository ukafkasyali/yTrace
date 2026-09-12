"""Readers for the KUKA LWR4+ MATLAB recordings.

The public corpus contains one directory per continuous recording.  We only
need the measured external torque and manual event marker files for the first
training iteration; all other raw files remain available for future features.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

import numpy as np
from scipy.io import loadmat

from robot_observability.constants import N_JOINTS

EventType = Literal["accidental", "intentional"]


class SessionRef(Protocol):
    """Minimum recording identity required by the window preparation pipeline."""

    session_id: str
    event_type: EventType


@dataclass(frozen=True)
class RawSessionRef:
    session_id: str
    event_type: EventType
    directory: Path
    torque_path: Path
    moments_path: Path


@dataclass(frozen=True)
class RawSession:
    ref: SessionRef
    timestamps_s: np.ndarray
    torque_nm: np.ndarray
    event_samples: np.ndarray


def _largest_numeric_value(mat: dict[str, object], preferred: str | None = None) -> np.ndarray:
    if preferred and preferred in mat:
        value = np.asarray(mat[preferred])
        if np.issubdtype(value.dtype, np.number):
            return value
    candidates = [
        np.asarray(value)
        for key, value in mat.items()
        if not key.startswith("__") and np.issubdtype(np.asarray(value).dtype, np.number)
    ]
    if not candidates:
        raise ValueError("MAT file contains no numeric arrays")
    return max(candidates, key=lambda value: value.size)


def discover_sessions(raw_root: Path) -> list[RawSessionRef]:
    """Find complete raw sessions without relying on archive directory depth."""
    sessions: list[RawSessionRef] = []
    for torque_path in sorted(raw_root.rglob("JK_MsrExtTrq.mat")):
        moments_path = torque_path.with_name("JK_moments.mat")
        if not moments_path.exists():
            continue
        lowered = "/".join(part.lower() for part in torque_path.parts)
        if "collision" in lowered:
            event_type: EventType = "accidental"
        elif "contact" in lowered:
            event_type = "intentional"
        else:
            raise ValueError(f"Cannot infer event type from {torque_path}")
        directory = torque_path.parent
        session_id = f"{event_type}/{directory.name}"
        sessions.append(
            RawSessionRef(
                session_id=session_id,
                event_type=event_type,
                directory=directory,
                torque_path=torque_path,
                moments_path=moments_path,
            )
        )
    duplicate_ids = {
        item.session_id for item in sessions if sum(s.session_id == item.session_id for s in sessions) > 1
    }
    if duplicate_ids:
        # Preserve stable provenance if timestamp folder names collide across archives.
        sessions = [
            RawSessionRef(
                session_id=f"{item.event_type}/{item.directory.relative_to(raw_root).as_posix()}",
                event_type=item.event_type,
                directory=item.directory,
                torque_path=item.torque_path,
                moments_path=item.moments_path,
            )
            for item in sessions
        ]
    return sessions


def load_session(ref: RawSessionRef, expected_hz: float = 1000.0) -> RawSession:
    torque_mat = loadmat(ref.torque_path)
    matrix = np.asarray(_largest_numeric_value(torque_mat, "MsrExtTrq"), dtype=np.float64)
    if matrix.ndim != 2:
        raise ValueError(f"Expected a 2D torque matrix in {ref.torque_path}, got {matrix.shape}")
    if matrix.shape[0] != N_JOINTS + 1 and matrix.shape[1] == N_JOINTS + 1:
        matrix = matrix.T
    if matrix.shape[0] != N_JOINTS + 1:
        raise ValueError(f"Expected time + seven torque rows in {ref.torque_path}, got {matrix.shape}")

    timestamps_s = matrix[0]
    torque_nm = matrix[1:].T.astype(np.float32, copy=False)
    if not np.isfinite(torque_nm).all():
        raise ValueError(f"Non-finite torque values in {ref.torque_path}")
    if len(timestamps_s) > 1:
        median_dt = float(np.median(np.diff(timestamps_s)))
        if not np.isclose(median_dt, 1.0 / expected_hz, rtol=0.02, atol=1e-6):
            raise ValueError(f"Unexpected sample interval {median_dt:g}s in {ref.torque_path}")

    moments_mat = loadmat(ref.moments_path)
    moments = _largest_numeric_value(moments_mat, "moments").reshape(-1)
    # MATLAB marker indices are one-based.  Internally every index is zero-based.
    event_samples = np.asarray(np.rint(moments), dtype=np.int64) - 1
    event_samples = np.unique(event_samples)
    if event_samples.size and (event_samples[0] < 0 or event_samples[-1] >= len(torque_nm)):
        raise ValueError(f"Out-of-range event marker in {ref.moments_path}")

    return RawSession(
        ref=ref,
        timestamps_s=timestamps_s,
        torque_nm=torque_nm,
        event_samples=event_samples,
    )

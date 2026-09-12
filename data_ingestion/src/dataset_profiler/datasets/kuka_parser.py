"""Deterministic parsing primitives for KUKA Part I MATLAB runs."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

from ..io.matlab import load_matlab


REQUIRED_BATCH01_FILES = (
    "JK_MsrExtTrq.mat",
    "JK_PosMsr.mat",
    "JK_moments.mat",
    "JsmoExp.mat",
)


@dataclass(frozen=True)
class TimedJointMatrix:
    """A decoded time row and its joint-channel rows."""

    time_seconds: np.ndarray
    channels: np.ndarray


def discover_kuka_runs(root: str | Path) -> list[Path]:
    """Discover KUKA run directories below either a batch or Part I root.

    A directory is a run only when all files needed by this connector are present. Recursive
    discovery lets the same dataset identity accept more batch directories later.
    """
    source = Path(root).resolve()
    if not source.is_dir():
        raise ValueError(f"KUKA source is not a directory: {source}")
    candidates = {path.parent for path in source.rglob(REQUIRED_BATCH01_FILES[0])}
    return sorted(
        directory
        for directory in candidates
        if all((directory / filename).is_file() for filename in REQUIRED_BATCH01_FILES)
    )


def load_matlab_variable(path: str | Path, variable: str) -> np.ndarray:
    """Decode one named MATLAB variable through the profiler's shared loader."""
    source = Path(path)
    variables = load_matlab(source).variables
    if variable not in variables:
        raise ValueError(f"{source} does not contain MATLAB variable {variable!r}")
    return np.asarray(variables[variable])


def parse_time_axis(run_dir: str | Path) -> np.ndarray:
    """Parse and validate the run's strictly increasing time axis."""
    source = Path(run_dir) / "JsmoExp.mat"
    time_axis = load_matlab_variable(source, "rt_tout").reshape(-1)
    if time_axis.size < 2:
        raise ValueError(f"{source} time axis must contain at least two samples")
    if not np.issubdtype(time_axis.dtype, np.number) or not np.all(np.isfinite(time_axis)):
        raise ValueError(f"{source} time axis must contain only finite numbers")
    if not np.all(np.diff(time_axis) > 0):
        raise ValueError(f"{source} time axis must be strictly increasing")
    return time_axis


def parse_timed_joint_matrix(
    path: str | Path,
    variable: str,
    *,
    expected_channels: int = 7,
    expected_time: np.ndarray | None = None,
) -> TimedJointMatrix:
    """Parse a time-plus-joints matrix and validate its orientation and time row."""
    source = Path(path)
    matrix = load_matlab_variable(source, variable)
    expected_rows = expected_channels + 1
    if matrix.ndim != 2 or matrix.shape[0] != expected_rows:
        raise ValueError(
            f"{source}:{variable} must have {expected_rows} rows "
            f"(time + {expected_channels} joints), got {matrix.shape}"
        )
    if not np.issubdtype(matrix.dtype, np.number) or not np.all(np.isfinite(matrix)):
        raise ValueError(f"{source}:{variable} must contain only finite numeric values")
    time_axis = np.asarray(matrix[0])
    if expected_time is not None and not np.array_equal(time_axis, expected_time):
        raise ValueError(f"{source}:{variable} time row differs from the run time axis")
    return TimedJointMatrix(time_seconds=time_axis, channels=np.asarray(matrix[1:]))


def matlab_index_to_python(index: int, *, n_samples: int | None = None) -> int:
    """Convert a MATLAB one-based sample index to a Python zero-based index."""
    if isinstance(index, bool) or not isinstance(index, (int, np.integer)):
        raise ValueError(f"MATLAB sample index must be an integer, got {index!r}")
    matlab_index = int(index)
    if matlab_index < 1:
        raise ValueError(f"MATLAB sample index must be >= 1, got {matlab_index}")
    python_index = matlab_index - 1
    if n_samples is not None and python_index >= n_samples:
        raise ValueError(
            f"MATLAB sample index {matlab_index} exceeds the {n_samples}-sample time axis"
        )
    return python_index


def parse_collision_indices(path: str | Path, *, n_samples: int | None = None) -> np.ndarray:
    """Parse and validate the one-based collision sample indices."""
    source = Path(path)
    raw = load_matlab_variable(source, "JK_moments").reshape(-1)
    if not np.issubdtype(raw.dtype, np.number) or not np.all(np.isfinite(raw)):
        raise ValueError(f"{source}:JK_moments must contain only finite numeric values")
    if not np.all(raw == np.floor(raw)):
        raise ValueError(f"{source}:JK_moments must contain integer-valued indices")
    indices = raw.astype(np.int64)
    for index in indices:
        matlab_index_to_python(index, n_samples=n_samples)
    return indices


def collision_time_seconds(time_axis: np.ndarray, matlab_index: int) -> float:
    """Return the timestamp addressed by a one-based MATLAB collision index."""
    python_index = matlab_index_to_python(matlab_index, n_samples=int(time_axis.size))
    return float(time_axis[python_index])

from __future__ import annotations

from pathlib import Path
from typing import Any
import hashlib

import numpy as np

from .io.matlab import describe_nested, load_matlab, sha256_file
from .models import ChannelStats, FileProfile, VariableProfile


def _finite_stats(array: np.ndarray) -> tuple[float | None, float | None, int, int]:
    if not np.issubdtype(array.dtype, np.number):
        return None, None, 0, 0
    nan_count = int(np.isnan(array).sum()) if np.issubdtype(array.dtype, np.inexact) else 0
    inf_count = int(np.isinf(array).sum()) if np.issubdtype(array.dtype, np.inexact) else 0
    finite = array[np.isfinite(array)] if np.issubdtype(array.dtype, np.inexact) else array
    if not finite.size:
        return None, None, nan_count, inf_count
    return float(np.min(finite)), float(np.max(finite)), nan_count, inf_count


def _json_scalar(value: Any) -> Any:
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, complex):
        return {"real": value.real, "imag": value.imag}
    if isinstance(value, float) and not np.isfinite(value):
        return "NaN" if np.isnan(value) else ("Infinity" if value > 0 else "-Infinity")
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value if isinstance(value, (str, int, float, bool)) or value is None else repr(value)


def channel_statistics(array: np.ndarray, near_constant_rtol: float = 1e-12) -> list[ChannelStats]:
    if (
        array.ndim != 2
        or array.shape[1] <= array.shape[0]
        or not np.issubdtype(array.dtype, np.number)
    ):
        return []
    results: list[ChannelStats] = []
    for index, channel in enumerate(array):
        minimum, maximum, nan_count, inf_count = _finite_stats(channel)
        finite = channel[np.isfinite(channel)] if np.issubdtype(channel.dtype, np.inexact) else channel
        mean = float(np.mean(finite)) if finite.size else None
        std = float(np.std(finite)) if finite.size else None
        span = (maximum - minimum) if minimum is not None and maximum is not None else None
        scale = max(abs(minimum or 0), abs(maximum or 0), 1.0)
        results.append(
            ChannelStats(
                index=index,
                count=int(channel.size),
                minimum=minimum,
                maximum=maximum,
                mean=mean,
                standard_deviation=std,
                nan_count=nan_count,
                inf_count=inf_count,
                constant=bool(span == 0),
                near_constant=bool(span is not None and span <= near_constant_rtol * scale),
            )
        )
    return results


def inspect_variable(name: str, value: Any, first_value_count: int = 8) -> VariableProfile:
    if not isinstance(value, np.ndarray):
        return VariableProfile(
            name=name,
            python_type=type(value).__name__,
            shape=[], dtype="n/a", ndim=0, size=1,
            first_values=[_json_scalar(value)],
            nested_structure=describe_nested(value),
        )
    minimum, maximum, nan_count, inf_count = _finite_stats(value)
    # Object-array bytes contain process-specific pointers, so only hash stable
    # value buffers. The enclosing source-file hash still covers nested values.
    content_hash = None if value.dtype == object else hashlib.sha256(value.tobytes(order="C")).hexdigest()
    nested = describe_nested(value) if value.dtype.names or value.dtype == object else None
    return VariableProfile(
        name=name,
        python_type=f"{type(value).__module__}.{type(value).__name__}",
        shape=list(value.shape), dtype=str(value.dtype), ndim=value.ndim, size=int(value.size),
        minimum=minimum, maximum=maximum, nan_count=nan_count, inf_count=inf_count,
        first_values=[_json_scalar(v) for v in value.flat[:first_value_count]],
        channel_stats=channel_statistics(value), content_sha256=content_hash,
        nested_structure=nested,
    )


def inspect_mat_file(
    path: str | Path,
    root: str | Path | None = None,
    source_run_id: str = "",
    first_value_count: int = 8,
) -> FileProfile:
    source = Path(path)
    base = Path(root) if root else source.parent
    loaded = load_matlab(source)
    return FileProfile(
        relative_path=source.relative_to(base).as_posix(),
        source_run_id=source_run_id,
        format=loaded.format,
        size_bytes=source.stat().st_size,
        sha256=sha256_file(source),
        variables=[inspect_variable(name, value, first_value_count) for name, value in sorted(loaded.variables.items())],
        loader=loaded.loader,
    )

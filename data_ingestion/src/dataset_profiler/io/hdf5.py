"""Bounded, streaming inspection helpers for generic HDF5 files."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator
import hashlib
import math

import numpy as np

try:
    import h5py
except ImportError:  # pragma: no cover - guarded by the optional hdf5 extra
    h5py = None

from ..models import ChannelStats, FileProfile, VariableProfile
from .matlab import sha256_file


_MAX_ATTRIBUTE_VALUES = 32
_TARGET_CHUNK_ELEMENTS = 1_000_000


@dataclass(frozen=True)
class Hdf5Structure:
    """Structural metadata that does not require exposing array contents."""

    root_attributes: dict[str, Any]
    groups: tuple[dict[str, Any], ...]


@dataclass
class _Moments:
    count: int = 0
    total: float = 0.0
    total_squares: float = 0.0
    minimum: float | None = None
    maximum: float | None = None
    nan_count: int = 0
    inf_count: int = 0

    def update(self, values: np.ndarray) -> None:
        if np.issubdtype(values.dtype, np.inexact):
            self.nan_count += int(np.isnan(values).sum())
            self.inf_count += int(np.isinf(values).sum())
            finite = values[np.isfinite(values)]
        else:
            finite = values
        if not finite.size:
            return
        numeric = finite.astype(np.float64, copy=False)
        local_min = float(np.min(numeric))
        local_max = float(np.max(numeric))
        self.minimum = (
            local_min if self.minimum is None else min(self.minimum, local_min)
        )
        self.maximum = (
            local_max if self.maximum is None else max(self.maximum, local_max)
        )
        self.count += int(numeric.size)
        self.total += float(np.sum(numeric, dtype=np.float64))
        self.total_squares += float(np.sum(numeric * numeric, dtype=np.float64))

    @property
    def mean(self) -> float | None:
        return self.total / self.count if self.count else None

    @property
    def standard_deviation(self) -> float | None:
        if not self.count:
            return None
        mean = self.total / self.count
        return math.sqrt(max(0.0, self.total_squares / self.count - mean * mean))


def inspect_hdf5_file(
    path: str | Path,
    root: str | Path | None = None,
    source_run_id: str = "",
    first_value_count: int = 8,
) -> FileProfile:
    """Inspect every dataset in an HDF5 file without materializing large arrays."""
    _require_h5py()
    source = Path(path)
    base = Path(root) if root else source.parent
    variables: list[VariableProfile] = []
    with h5py.File(source, "r") as handle:

        def visit(name: str, node: h5py.Group | h5py.Dataset) -> None:
            if isinstance(node, h5py.Dataset):
                variables.append(_inspect_dataset(name, node, first_value_count))

        handle.visititems(visit)
    return FileProfile(
        relative_path=source.relative_to(base).as_posix(),
        source_run_id=source_run_id,
        format="hdf5",
        size_bytes=source.stat().st_size,
        sha256=sha256_file(source),
        variables=variables,
        loader="h5py",
    )


def inspect_hdf5_structure(path: str | Path) -> Hdf5Structure:
    """Return bounded root/group attributes and group paths for one HDF5 file."""
    _require_h5py()
    groups: list[dict[str, Any]] = []
    with h5py.File(path, "r") as handle:
        root_attributes = _attributes(handle.attrs)

        def visit(name: str, node: h5py.Group | h5py.Dataset) -> None:
            if isinstance(node, h5py.Group):
                groups.append({"path": name, "attributes": _attributes(node.attrs)})

        handle.visititems(visit)
    return Hdf5Structure(root_attributes, tuple(groups))


def read_hdf5_excerpt(
    path: str | Path,
    dataset_name: str,
    start: int,
    length: int,
    channels: tuple[int, ...],
    channel_axis: int | None,
) -> tuple[np.ndarray, str, int]:
    """Load only a host-validated sample/channel window from an HDF5 dataset."""
    _require_h5py()
    with h5py.File(path, "r") as handle:
        dataset = handle[dataset_name]
        if dataset.ndim not in {1, 2} or not np.issubdtype(dataset.dtype, np.number):
            raise ValueError(
                "excerpt source is not a numeric one- or two-dimensional array"
            )
        if dataset.ndim == 1:
            available_samples = dataset.shape[0]
            if channels != (0,):
                raise ValueError("one-dimensional datasets expose one logical channel")
            excerpt = np.asarray(dataset[start : start + length]).reshape(1, -1)
        elif channel_axis == 1:
            available_samples = dataset.shape[0]
            excerpt = np.stack(
                [
                    np.asarray(dataset[start : start + length, channel])
                    for channel in channels
                ]
            )
        else:
            available_samples = dataset.shape[1]
            excerpt = np.stack(
                [
                    np.asarray(dataset[channel, start : start + length])
                    for channel in channels
                ]
            )
        if start + length > available_samples:
            raise ValueError("requested excerpt exceeds the available samples")
        return excerpt, str(dataset.dtype), available_samples


def _inspect_dataset(
    name: str, dataset: h5py.Dataset, first_value_count: int
) -> VariableProfile:
    numeric = np.issubdtype(dataset.dtype, np.number)
    overall = _Moments()
    channel_axis = _channel_axis(dataset.shape) if numeric else None
    channel_moments = (
        [_Moments() for _ in range(dataset.shape[channel_axis])]
        if channel_axis is not None
        else []
    )
    digest = hashlib.sha256()
    first_values: list[Any] = []
    for values in _chunks(dataset):
        contiguous = np.ascontiguousarray(values)
        digest.update(contiguous.tobytes(order="C"))
        if len(first_values) < first_value_count:
            remaining = first_value_count - len(first_values)
            first_values.extend(
                _json_scalar(value) for value in contiguous.flat[:remaining]
            )
        if numeric:
            overall.update(contiguous)
            if channel_axis is not None:
                for index, moments in enumerate(channel_moments):
                    moments.update(np.take(contiguous, index, axis=channel_axis))

    metadata: dict[str, Any] = {
        "hdf5_attributes": _attributes(dataset.attrs),
        "numeric_statistics": {
            "sample_count": overall.count,
            "mean": overall.mean,
            "standard_deviation": overall.standard_deviation,
        }
        if numeric
        else None,
        "structural_axes": {
            "sample_axis": _sample_axis(dataset.shape),
            "channel_axis": channel_axis,
        },
    }
    return VariableProfile(
        name=name,
        python_type="h5py.Dataset",
        shape=list(dataset.shape),
        dtype=str(dataset.dtype),
        ndim=dataset.ndim,
        size=int(dataset.size),
        minimum=overall.minimum,
        maximum=overall.maximum,
        nan_count=overall.nan_count,
        inf_count=overall.inf_count,
        first_values=first_values,
        channel_stats=[
            _channel_stats(index, moments)
            for index, moments in enumerate(channel_moments)
        ],
        content_sha256=digest.hexdigest(),
        nested_structure=metadata,
    )


def _chunks(dataset: h5py.Dataset) -> Iterator[np.ndarray]:
    if dataset.size == 0:
        return
    if dataset.ndim == 0:
        yield np.asarray(dataset[()])
        return
    trailing = math.prod(dataset.shape[1:]) or 1
    rows = max(1, _TARGET_CHUNK_ELEMENTS // trailing)
    for start in range(0, dataset.shape[0], rows):
        yield np.asarray(dataset[start : min(dataset.shape[0], start + rows)])


def _sample_axis(shape: tuple[int, ...]) -> int | None:
    if not shape:
        return None
    return max(range(len(shape)), key=lambda index: shape[index])


def _channel_axis(shape: tuple[int, ...]) -> int | None:
    if len(shape) != 2 or 0 in shape:
        return None
    sample_axis = _sample_axis(shape)
    return 1 - sample_axis if sample_axis is not None else None


def _channel_stats(index: int, moments: _Moments) -> ChannelStats:
    span = (
        moments.maximum - moments.minimum
        if moments.minimum is not None and moments.maximum is not None
        else None
    )
    scale = max(abs(moments.minimum or 0), abs(moments.maximum or 0), 1.0)
    return ChannelStats(
        index=index,
        count=moments.count + moments.nan_count + moments.inf_count,
        minimum=moments.minimum,
        maximum=moments.maximum,
        mean=moments.mean,
        standard_deviation=moments.standard_deviation,
        nan_count=moments.nan_count,
        inf_count=moments.inf_count,
        constant=bool(span == 0),
        near_constant=bool(span is not None and span <= 1e-12 * scale),
    )


def _attributes(attributes: h5py.AttributeManager) -> dict[str, Any]:
    return {
        str(name): _bounded_value(value) for name, value in sorted(attributes.items())
    }


def _bounded_value(value: Any) -> Any:
    array = np.asarray(value)
    values = [_json_scalar(item) for item in array.reshape(-1)[:_MAX_ATTRIBUTE_VALUES]]
    if array.ndim == 0:
        return values[0] if values else None
    return {
        "shape": list(array.shape),
        "dtype": str(array.dtype),
        "values": values,
        "truncated": array.size > _MAX_ATTRIBUTE_VALUES,
    }


def _json_scalar(value: Any) -> Any:
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, complex):
        return {"real": value.real, "imag": value.imag}
    if isinstance(value, float) and not np.isfinite(value):
        return "NaN" if np.isnan(value) else ("Infinity" if value > 0 else "-Infinity")
    return (
        value
        if isinstance(value, (str, int, float, bool)) or value is None
        else repr(value)
    )


def _require_h5py() -> None:
    if h5py is None:
        raise RuntimeError("HDF5 support requires the optional 'hdf5' extra")

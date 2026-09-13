from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from timenet.client import TimeNet
from timenet.dataset import TimeFDataset, TimeSeries
from timenet.engine import store_dataset
from timenet.types import (
    Annotation,
    DatasetMetadata,
    DataSource,
    Domain,
    License,
    TimeSeriesSpec,
    Version,
    ureg,
)

from .formats.arrays import NumericArrayAdapter
from .formats.tabular import FormatAdapterError, TabularAdapter
from .jobs import AssetReceipt, IngestionJob, ResourceProfile
from .mapping import MappingLayout, MappingSpec


class GenericImportError(RuntimeError):
    pass


@dataclass(frozen=True)
class NormalizedSeries:
    name: str
    unit: str
    values: np.ndarray
    time_offsets_us: np.ndarray


@dataclass(frozen=True)
class NormalizedRecord:
    record_id: str
    source_record: str
    source_rows: tuple[int, ...]
    series: tuple[NormalizedSeries, ...]
    annotations: tuple[tuple[str, object], ...]


@dataclass(frozen=True)
class GenericBuildResult:
    dataset_id: str
    dataset_version: str
    version_dir: Path
    record_count: int
    series_count: int
    value_count: int
    validation_sha256: str


class GenericTimeFBuilder:
    def __init__(
        self,
        *,
        cache_dir: Path,
        registry_root: Path,
        tabular: TabularAdapter | None = None,
        arrays: NumericArrayAdapter | None = None,
    ):
        self.cache_dir = cache_dir.resolve()
        self.registry_root = registry_root.resolve()
        self.tabular = tabular or TabularAdapter()
        self.arrays = arrays or NumericArrayAdapter()

    def build(
        self,
        job: IngestionJob,
        resource: ResourceProfile,
        mapping: MappingSpec,
        receipts: list[AssetReceipt],
    ) -> GenericBuildResult:
        if not job.dataset_license_id:
            raise GenericImportError("Dataset-file license is required")
        path = self._resource_path(resource, receipts)
        records = self.normalize(path, resource, mapping)
        dataset_id = f"trace/approved-{job.approved_source_id.removeprefix('src_')}"
        dataset = self._dataset(job, dataset_id, records)
        version_dir = self.registry_root / dataset_id / "1.0.0"
        if not version_dir.exists():
            version_dir = store_dataset(dataset, self.registry_root, values_backend="parquet")
        validation = self._validate_readback(dataset_id, records)
        return GenericBuildResult(
            dataset_id=dataset_id,
            dataset_version="1.0.0",
            version_dir=version_dir,
            record_count=len(records),
            series_count=sum(len(record.series) for record in records),
            value_count=sum(
                series.values.size for record in records for series in record.series
            ),
            validation_sha256=validation,
        )

    def _resource_path(
        self, resource: ResourceProfile, receipts: list[AssetReceipt]
    ) -> Path:
        receipt = next((item for item in receipts if item.asset_id == resource.asset_id), None)
        if receipt is None:
            raise GenericImportError("Mapped resource has no verified asset receipt")
        content = self.cache_dir / "content" / receipt.content_sha256[:2] / receipt.content_sha256
        if resource.content_sha256 == receipt.content_sha256:
            candidate = content
        else:
            candidate = (
                self.cache_dir
                / "extracted"
                / receipt.content_sha256[:2]
                / receipt.content_sha256
                / resource.logical_path
            )
        resolved = candidate.resolve(strict=True)
        allowed = (self.cache_dir / ("content" if candidate == content else "extracted")).resolve()
        if not resolved.is_relative_to(allowed) or not resolved.is_file():
            raise GenericImportError("Mapped resource escaped the verified cache")
        digest = hashlib.sha256()
        with resolved.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
        if digest.hexdigest() != resource.content_sha256 or resolved.stat().st_size != resource.size_bytes:
            raise GenericImportError("Mapped resource bytes changed after inventory")
        return resolved

    def normalize(
        self, path: Path, resource: ResourceProfile, mapping: MappingSpec
    ) -> tuple[NormalizedRecord, ...]:
        if mapping.layout is MappingLayout.NAMED_ARRAYS:
            return self._normalize_arrays(path, resource, mapping)
        return self._normalize_table(path, resource, mapping)

    def _normalize_table(
        self, path: Path, resource: ResourceProfile, mapping: MappingSpec
    ) -> tuple[NormalizedRecord, ...]:
        columns = [mapping.time_selector, *mapping.annotations]
        if mapping.record_selector:
            columns.append(mapping.record_selector)
        if mapping.layout is MappingLayout.WIDE_TABLE:
            columns.extend(channel.selector for channel in mapping.channels)
        else:
            columns.extend([mapping.channel_selector, mapping.value_selector])
        try:
            table = self.tabular.read_columns(path, resource.format, list(dict.fromkeys(columns)))
        except FormatAdapterError as exc:
            raise GenericImportError(str(exc)) from exc
        groups: dict[str, list[int]] = {}
        if mapping.record_selector:
            for index, value in enumerate(table.columns[mapping.record_selector]):
                groups.setdefault(self._record_value(value), []).append(index)
        else:
            groups["record"] = list(range(table.source_rows.size))
        records = []
        for source_record in sorted(groups):
            indices = np.asarray(groups[source_record], dtype=np.int64)
            annotations = self._annotations(table.columns, mapping.annotations, indices)
            if mapping.layout is MappingLayout.WIDE_TABLE:
                time = self._time_us(table.columns[mapping.time_selector][indices])
                series = tuple(
                    NormalizedSeries(
                        channel.name,
                        channel.unit or "",
                        self._numeric(table.columns[channel.selector][indices], channel.selector),
                        time,
                    )
                    for channel in mapping.channels
                )
            else:
                series_items = []
                channel_values = table.columns[mapping.channel_selector][indices]
                for channel in mapping.channels:
                    selected = indices[
                        np.asarray(
                            [str(value) == channel.selector for value in channel_values],
                            dtype=bool,
                        )
                    ]
                    if selected.size == 0:
                        raise GenericImportError(
                            f"Mapped channel {channel.selector!r} has no rows in record {source_record!r}"
                        )
                    series_items.append(
                        NormalizedSeries(
                            channel.name,
                            channel.unit or "",
                            self._numeric(
                                table.columns[mapping.value_selector][selected],
                                mapping.value_selector or "value",
                            ),
                            self._time_us(table.columns[mapping.time_selector][selected]),
                        )
                    )
                series = tuple(series_items)
            records.append(
                NormalizedRecord(
                    record_id=self._record_id(source_record),
                    source_record=source_record,
                    source_rows=tuple(int(value) for value in table.source_rows[indices]),
                    series=series,
                    annotations=annotations,
                )
            )
        if not records:
            raise GenericImportError("Mapped resource contains no records")
        return tuple(records)

    def _normalize_arrays(
        self, path: Path, resource: ResourceProfile, mapping: MappingSpec
    ) -> tuple[NormalizedRecord, ...]:
        names = [mapping.time_selector, mapping.signal_selector, *mapping.annotations]
        try:
            payload = self.arrays.read_arrays(path, resource.format, list(dict.fromkeys(names))).arrays
        except FormatAdapterError as exc:
            raise GenericImportError(str(exc)) from exc
        signal = np.asarray(payload[mapping.signal_selector])
        if signal.ndim != 2:
            raise GenericImportError("Generic named-array signals must be two-dimensional")
        canonical = np.moveaxis(signal, (mapping.sample_axis, mapping.channel_axis), (0, 1))
        time = self._time_us(np.asarray(payload[mapping.time_selector]).reshape(-1))
        if canonical.shape != (time.size, len(mapping.channels)):
            raise GenericImportError("Mapped array axes do not match time and channel counts")
        annotations = tuple(
            (name, self._scalar_annotation(payload[name], name)) for name in mapping.annotations
        )
        series = tuple(
            NormalizedSeries(
                channel.name,
                channel.unit or "",
                self._numeric(canonical[:, index], channel.selector),
                time,
            )
            for index, channel in enumerate(mapping.channels)
        )
        return (
            NormalizedRecord(
                record_id="record",
                source_record="record",
                source_rows=tuple(range(1, time.size + 1)),
                series=series,
                annotations=annotations,
            ),
        )

    def _dataset(
        self, job: IngestionJob, dataset_id: str, records: tuple[NormalizedRecord, ...]
    ) -> TimeFDataset:
        try:
            license_value = next(
                item for item in License if item.value.casefold() == job.dataset_license_id.casefold()
            )
        except StopIteration:
            license_value = License.OTHER
        dataset = TimeFDataset(
            metadata=DatasetMetadata(
                dataset_id=dataset_id,
                dataset_version=Version(1, 0, 0),
                name=f"Approved source {job.approved_source_id}",
                description="Deterministically imported approved time-series dataset",
                license=license_value,
                domains=(Domain.GENERAL,),
                source_url=job.source_url,
            )
        )
        source = DataSource(
            data_source_type="approved_dataset",
            name=job.approved_source_id,
            provider=job.source_kind,
        )
        for record in records:
            time_series = []
            for index, series in enumerate(record.series):
                try:
                    unit = ureg.Unit(series.unit)
                except Exception as exc:
                    raise GenericImportError(f"Invalid unit {series.unit!r}") from exc
                spec = TimeSeriesSpec(
                    spec_type=f"measured_{self._signal_key(series.name)}",
                    name=series.name,
                    unit_value=unit,
                    data_source=source,
                    dtype="float64",
                    nullable=bool(np.isnan(series.values).any()),
                )
                time_series.append(
                    TimeSeries.from_irregular(
                        series.values,
                        time_offsets_us=series.time_offsets_us,
                        spec=spec,
                        signal=series.name,
                        source_id=record.source_record,
                        time_series_id=f"{record.record_id}-series-{index + 1}",
                    )
                )
            target = dataset.add_record(time_series=tuple(time_series), record_id=record.record_id)
            target.add_annotation(
                Annotation(
                    key="source_rows",
                    value={
                        "first": min(record.source_rows),
                        "last": max(record.source_rows),
                        "count": len(record.source_rows),
                    },
                    source="approved resource mapping",
                    id=f"{record.record_id}-source-rows",
                )
            )
            for index, (key, value) in enumerate(record.annotations):
                target.add_annotation(
                    Annotation(
                        key=key,
                        value=value,
                        source="approved resource mapping",
                        id=f"{record.record_id}-annotation-{index + 1}",
                    )
                )
        return dataset

    def _validate_readback(
        self, dataset_id: str, expected: tuple[NormalizedRecord, ...]
    ) -> str:
        loaded = TimeNet(registry=self.registry_root).load(dataset_id, auto_build=False)
        records = tuple(loaded.records)
        if len(records) != len(expected):
            raise GenericImportError("TimeF read-back record count differs")
        summary = []
        by_id = {record.record_id: record for record in records}
        for wanted in expected:
            actual = by_id.get(wanted.record_id)
            if actual is None or len(actual.time_series) != len(wanted.series):
                raise GenericImportError("TimeF read-back series structure differs")
            for actual_series, wanted_series in zip(actual.time_series, wanted.series, strict=True):
                if (
                    actual_series.signal != wanted_series.name
                    or actual_series.spec.name != wanted_series.name
                    or actual_series.spec.unit_value != ureg.Unit(wanted_series.unit)
                ):
                    raise GenericImportError("TimeF read-back channel name or unit differs")
                observed = actual_series.to_numpy().reshape(-1)
                if not np.array_equal(observed, wanted_series.values, equal_nan=True):
                    raise GenericImportError("TimeF read-back values differ")
                if not np.array_equal(actual_series.time_offsets_us(), wanted_series.time_offsets_us):
                    raise GenericImportError("TimeF read-back timestamps differ")
                summary.append(
                    {
                        "recordId": wanted.record_id,
                        "series": wanted_series.name,
                        "values": int(wanted_series.values.size),
                        "valueSha256": hashlib.sha256(wanted_series.values.tobytes()).hexdigest(),
                        "timeSha256": hashlib.sha256(wanted_series.time_offsets_us.tobytes()).hexdigest(),
                    }
                )
        canonical = json.dumps(summary, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode()).hexdigest()

    @staticmethod
    def _numeric(values: np.ndarray, selector: str) -> np.ndarray:
        output = []
        for value in np.asarray(values).reshape(-1):
            if value is None or (isinstance(value, float) and math.isnan(value)):
                output.append(np.nan)
                continue
            if isinstance(value, bool):
                raise GenericImportError(f"Mapped signal {selector!r} contains booleans")
            try:
                number = float(value)
            except (TypeError, ValueError) as exc:
                raise GenericImportError(
                    f"Mapped signal {selector!r} contains a non-numeric value"
                ) from exc
            if math.isinf(number):
                raise GenericImportError(f"Mapped signal {selector!r} contains infinity")
            output.append(number)
        return np.asarray(output, dtype=np.float64)

    @classmethod
    def _time_us(cls, values: np.ndarray) -> np.ndarray:
        seconds = cls._numeric(values, "time")
        if np.isnan(seconds).any() or (seconds < 0).any():
            raise GenericImportError("Mapped time must be finite and non-negative")
        micros_float = seconds * 1_000_000.0
        micros = np.rint(micros_float).astype(np.int64)
        if not np.allclose(micros_float, micros, rtol=0.0, atol=1e-6):
            raise GenericImportError("Mapped time cannot be represented as integer microseconds")
        if micros.size == 0 or (np.diff(micros) <= 0).any():
            raise GenericImportError("Mapped time must be strictly increasing within each series")
        return micros

    @staticmethod
    def _record_value(value: object) -> str:
        if value is None or str(value).strip() == "":
            raise GenericImportError("Mapped record selector contains a missing value")
        return str(value)

    @staticmethod
    def _record_id(value: str) -> str:
        slug = re.sub(r"[^a-zA-Z0-9._-]+", "-", value).strip("-._")[:80] or "record"
        digest = hashlib.sha256(value.encode()).hexdigest()[:10]
        return f"{slug}-{digest}"

    @staticmethod
    def _signal_key(value: str) -> str:
        slug = re.sub(r"[^a-z0-9]+", "_", value.casefold()).strip("_")[:80] or "signal"
        return f"{slug}_{hashlib.sha256(value.encode()).hexdigest()[:8]}"

    @classmethod
    def _annotations(
        cls, columns: dict[str, np.ndarray], names: list[str], indices: np.ndarray
    ) -> tuple[tuple[str, object], ...]:
        return tuple(
            (name, cls._constant_annotation(columns[name][indices], name)) for name in names
        )

    @staticmethod
    def _constant_annotation(values: np.ndarray, name: str) -> object:
        normalized = [value.item() if isinstance(value, np.generic) else value for value in values]
        if not normalized:
            raise GenericImportError(f"Annotation {name!r} has no values")
        first = normalized[0]
        if any(value != first for value in normalized[1:]):
            raise GenericImportError(
                f"Annotation {name!r} varies within a record; event mappings are not inferred"
            )
        return first

    @classmethod
    def _scalar_annotation(cls, value: np.ndarray, name: str) -> object:
        array = np.asarray(value)
        if array.size != 1:
            raise GenericImportError(f"Array annotation {name!r} must be scalar")
        return cls._constant_annotation(array.reshape(-1), name)

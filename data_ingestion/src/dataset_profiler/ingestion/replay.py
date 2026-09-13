from __future__ import annotations

import math
from typing import Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field

from .catalog import ImportedDatasetCatalog, ImportedDatasetUnavailable

_CHANNEL_IDS = tuple(f"joint_{index}" for index in range(1, 8))
_OVERVIEW_POINTS = 24_000
_DETAIL_SECONDS = 5


def _to_camel(value: str) -> str:
    head, *tail = value.split("_")
    return head + "".join(part.title() for part in tail)


class ReplayModel(BaseModel):
    model_config = ConfigDict(alias_generator=_to_camel, populate_by_name=True)


class ImportedReplayChannel(ReplayModel):
    id: str
    name: str
    unit: str
    values: list[float]


class ImportedReplayEvent(ReplayModel):
    id: str
    time_seconds: float = Field(ge=0)
    kind: Literal["publisher_annotation"] = "publisher_annotation"
    label: str
    source: str


class ImportedReplayRecording(ReplayModel):
    id: str
    name: str
    duration_seconds: float = Field(gt=0)
    sample_rate_hz: int = Field(gt=0)
    display_sample_rate_hz: float = Field(gt=0)
    source_url: str
    archive: str
    channel_count: int = 7
    event_count: int = Field(ge=0)


class ImportedReplayDetail(ReplayModel):
    start_seconds: float = Field(ge=0)
    end_seconds: float = Field(gt=0)
    times: list[float]
    channels: list[ImportedReplayChannel]


class ImportedReplay(ReplayModel):
    recording: ImportedReplayRecording
    times: list[float]
    channels: list[ImportedReplayChannel]
    events: list[ImportedReplayEvent]
    detail: ImportedReplayDetail


class ImportedWindowRef(ReplayModel):
    dataset_id: str
    recording_id: str
    start_sec: float = Field(ge=0)
    end_sec: float = Field(gt=0)
    channel_ids: list[str]


class ImportedSignalSeries(ReplayModel):
    channel_id: str
    time_sec: list[float]
    values: list[float]


class ImportedSignalWindow(ReplayModel):
    window: ImportedWindowRef
    series: list[ImportedSignalSeries]
    resolution: Literal["raw", "display"]
    aggregation: str | None = None


class ImportedSignalEvent(ReplayModel):
    id: str
    recording_id: str
    start_sec: float = Field(ge=0)
    channel_ids: list[str] = Field(default_factory=list)
    label: str
    origin: Literal["publisher_annotation"] = "publisher_annotation"
    source: str


class ImportedReplayService:
    """Adapt compatible validated TimeF records to Trace's replay contracts."""

    def __init__(self, catalog: ImportedDatasetCatalog):
        self.catalog = catalog

    def replay(
        self,
        dataset_id: str,
        dataset_version: str,
        ingestion_id: str,
        record_key: str,
        source_url: str,
    ) -> ImportedReplay:
        record = self.catalog.get_record(dataset_id, dataset_version, record_key)
        series, period_us, value_count = self._torque(record)
        duration = (value_count - 1) * period_us / 1_000_000
        events = self._events(record, record_key, duration)
        detail_start = round(
            max(0.0, (events[0].time_seconds - 0.4) if events else 0.0), 3
        )
        detail_end = round(min(duration, detail_start + _DETAIL_SECONDS), 3)
        if detail_end <= detail_start:
            raise ImportedDatasetUnavailable("Imported record is too short for replay")
        overview_indices = np.linspace(
            0, value_count - 1, min(value_count, _OVERVIEW_POINTS), dtype=np.int64
        )
        detail_indices = self._indices(detail_start, detail_end, period_us, value_count)
        recording_id = self.recording_id(ingestion_id, record_key)
        return ImportedReplay(
            recording=ImportedReplayRecording(
                id=recording_id,
                name=str(record.record_id),
                duration_seconds=duration,
                sample_rate_hz=1_000,
                display_sample_rate_hz=(len(overview_indices) - 1) / duration,
                source_url=source_url,
                archive=f"TimeF {dataset_id} · {dataset_version}",
                event_count=len(events),
            ),
            times=self._times(overview_indices, period_us),
            channels=self._channels(series, overview_indices),
            events=events,
            detail=ImportedReplayDetail(
                start_seconds=detail_start,
                end_seconds=detail_end,
                times=self._times(detail_indices, period_us),
                channels=self._channels(series, detail_indices),
            ),
        )

    def signals(
        self,
        dataset_id: str,
        dataset_version: str,
        ingestion_id: str,
        record_key: str,
        *,
        start_sec: float,
        end_sec: float,
        channel_ids: list[str],
        max_points: int,
    ) -> ImportedSignalWindow:
        record = self.catalog.get_record(dataset_id, dataset_version, record_key)
        series, period_us, value_count = self._torque(record)
        duration = (value_count - 1) * period_us / 1_000_000
        if (
            not math.isfinite(start_sec)
            or not math.isfinite(end_sec)
            or start_sec < 0
            or end_sec <= start_sec
            or end_sec > duration
            or not 1 <= max_points <= 200_000
            or not channel_ids
            or len(set(channel_ids)) != len(channel_ids)
            or any(channel_id not in _CHANNEL_IDS for channel_id in channel_ids)
        ):
            raise ImportedDatasetUnavailable("Imported signal window is invalid")
        indices = self._indices(start_sec, end_sec, period_us, value_count)
        raw = len(indices) <= max_points
        if not raw:
            indices = indices[
                np.linspace(0, len(indices) - 1, max_points, dtype=np.int64)
            ]
        by_id = {str(item.signal): item for item in series}
        times = self._times(indices, period_us)
        return ImportedSignalWindow(
            window=ImportedWindowRef(
                dataset_id=dataset_id,
                recording_id=self.recording_id(ingestion_id, record_key),
                start_sec=start_sec,
                end_sec=end_sec,
                channel_ids=channel_ids,
            ),
            series=[
                ImportedSignalSeries(
                    channel_id=channel_id,
                    time_sec=times,
                    values=self._values(by_id[channel_id], indices),
                )
                for channel_id in channel_ids
            ],
            resolution="raw" if raw else "display",
            aggregation=None if raw else "Uniform display sampling; brief extrema may be missed.",
        )

    def events(
        self,
        dataset_id: str,
        dataset_version: str,
        ingestion_id: str,
        record_key: str,
    ) -> list[ImportedSignalEvent]:
        record = self.catalog.get_record(dataset_id, dataset_version, record_key)
        _, period_us, value_count = self._torque(record)
        duration = (value_count - 1) * period_us / 1_000_000
        recording_id = self.recording_id(ingestion_id, record_key)
        return [
            ImportedSignalEvent(
                id=event.id,
                recording_id=recording_id,
                start_sec=event.time_seconds,
                label=event.label,
                source=event.source,
            )
            for event in self._events(record, record_key, duration)
        ]

    @staticmethod
    def recording_id(ingestion_id: str, record_key: str) -> str:
        return f"imported:{ingestion_id}:{record_key}"

    @staticmethod
    def _torque(record):
        series = sorted(
            (
                item
                for item in record.time_series
                if item.spec.spec_type == "measured_external_joint_torque"
            ),
            key=lambda item: str(item.signal),
        )
        if (
            len(series) != 7
            or tuple(str(item.signal) for item in series) != _CHANNEL_IDS
            or len({item.n_values for item in series}) != 1
            or any(
                item.n_values <= 1
                or getattr(item.time_axis, "period_us", None) != 1_000
                for item in series
            )
        ):
            raise ImportedDatasetUnavailable(
                "This record is not compatible with seven-channel 1 kHz torque replay"
            )
        return series, 1_000, series[0].n_values

    @staticmethod
    def _indices(start_sec: float, end_sec: float, period_us: int, count: int):
        rate = 1_000_000 / period_us
        start = max(0, math.ceil(start_sec * rate - 1e-8))
        end = min(count, math.ceil(end_sec * rate - 1e-8))
        if end <= start:
            raise ImportedDatasetUnavailable("Imported signal window contains no samples")
        return np.arange(start, end, dtype=np.int64)

    @staticmethod
    def _times(indices: np.ndarray, period_us: int) -> list[float]:
        return (indices.astype(np.float64) * period_us / 1_000_000).tolist()

    @classmethod
    def _channels(cls, series, indices: np.ndarray) -> list[ImportedReplayChannel]:
        return [
            ImportedReplayChannel(
                id=str(item.signal),
                name=f"Joint {index}",
                unit="Nm",
                values=cls._values(item, indices),
            )
            for index, item in enumerate(series, start=1)
        ]

    @staticmethod
    def _values(series, indices: np.ndarray) -> list[float]:
        values = np.asarray(series.to_numpy()).reshape(-1)
        selected = values[indices]
        if not np.isfinite(selected).all():
            raise ImportedDatasetUnavailable("Imported torque contains non-finite values")
        return selected.astype(float).tolist()

    @staticmethod
    def _events(record, record_key: str, duration: float) -> list[ImportedReplayEvent]:
        events = []
        for index, annotation in enumerate(record.annotations, start=1):
            if annotation.key not in {"collision", "intentional_contact"}:
                continue
            start_us = getattr(annotation.span, "start_us", None)
            if not isinstance(start_us, int):
                continue
            timestamp = start_us / 1_000_000
            if not 0 <= timestamp <= duration:
                continue
            value = annotation.value if isinstance(annotation.value, dict) else {}
            events.append(
                ImportedReplayEvent(
                    id=f"{record_key}-{annotation.key}-{index:03d}",
                    time_seconds=timestamp,
                    label=str(value.get("label") or annotation.key.replace("_", " ")),
                    source=str(annotation.source or "TimeF publisher annotation"),
                )
            )
        return sorted(events, key=lambda event: (event.time_seconds, event.id))

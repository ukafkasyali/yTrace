from __future__ import annotations

import hashlib
import math
import re
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field
from timenet.client import TimeNet


def _to_camel(value: str) -> str:
    head, *tail = value.split("_")
    return head + "".join(part.title() for part in tail)


class ImportedRecordSummary(BaseModel):
    model_config = ConfigDict(alias_generator=_to_camel, populate_by_name=True)

    record_id: str
    record_key: str = Field(pattern=r"^[a-f0-9]{24}$")
    series_count: int = Field(ge=0)
    value_count: int = Field(ge=0)
    duration_seconds: float | None = Field(default=None, ge=0)
    signals: list[str]
    annotation_keys: list[str]
    is_replay_compatible: bool


class ImportedRecordPagination(BaseModel):
    model_config = ConfigDict(alias_generator=_to_camel, populate_by_name=True)

    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=100)
    total_items: int = Field(ge=0)
    total_pages: int = Field(ge=0)


class ImportedRecordPage(BaseModel):
    model_config = ConfigDict(alias_generator=_to_camel, populate_by_name=True)

    dataset_id: str
    dataset_version: str
    data: list[ImportedRecordSummary]
    pagination: ImportedRecordPagination


class ImportedDatasetUnavailable(RuntimeError):
    pass


class TimeNetClient(Protocol):
    def load(self, dataset_id: str, version: str | None = None, *, auto_build: bool): ...


class ImportedDatasetCatalog:
    """Read bounded record summaries from a validated ingestion registry."""

    def __init__(self, registry_root: Path, client: TimeNetClient | None = None):
        self.client = client or TimeNet(registry=registry_root)

    def list_records(
        self,
        dataset_id: str,
        dataset_version: str,
        *,
        page: int,
        page_size: int,
    ) -> ImportedRecordPage:
        dataset = self.load_dataset(dataset_id, dataset_version)
        records = dataset.records
        total_items = len(records)
        total_pages = math.ceil(total_items / page_size) if total_items else 0
        if total_pages and page > total_pages:
            raise ImportedDatasetUnavailable("Imported dataset page is out of range")
        start = (page - 1) * page_size
        summaries = [self._summary(record) for record in records[start : start + page_size]]
        return ImportedRecordPage(
            dataset_id=dataset_id,
            dataset_version=dataset_version,
            data=summaries,
            pagination=ImportedRecordPagination(
                page=page,
                page_size=page_size,
                total_items=total_items,
                total_pages=total_pages,
            ),
        )

    def load_dataset(self, dataset_id: str, dataset_version: str):
        if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._/-]{0,127}", dataset_id):
            raise ImportedDatasetUnavailable("Imported dataset identity is invalid")
        try:
            return self.client.load(
                dataset_id, version=dataset_version, auto_build=False
            )
        except Exception as exc:
            raise ImportedDatasetUnavailable(
                "Validated imported dataset is unavailable from the local registry"
            ) from exc

    def get_record(self, dataset_id: str, dataset_version: str, record_key: str):
        if not re.fullmatch(r"[a-f0-9]{24}", record_key):
            raise ImportedDatasetUnavailable("Imported record identity is invalid")
        matches = [
            record
            for record in self.load_dataset(dataset_id, dataset_version).records
            if imported_record_key(record.record_id) == record_key
        ]
        if len(matches) != 1:
            raise ImportedDatasetUnavailable("Imported record was not found")
        return matches[0]

    @staticmethod
    def _summary(record) -> ImportedRecordSummary:
        series = list(record.time_series)
        torque = [
            item
            for item in series
            if item.spec.spec_type == "measured_external_joint_torque"
        ]
        expected_signals = {f"joint_{index}" for index in range(1, 8)}
        replay_compatible = (
            len(torque) == 7
            and {str(item.signal) for item in torque} == expected_signals
            and len({item.n_values for item in torque}) == 1
            and all(
                item.n_values > 1
                and getattr(item.time_axis, "period_us", None) == 1_000
                for item in torque
            )
        )
        durations = [
            (item.n_values - 1) * item.time_axis.period_us / 1_000_000
            for item in series
            if item.n_values > 0 and getattr(item.time_axis, "period_us", None) is not None
        ]
        return ImportedRecordSummary(
            record_id=record.record_id,
            record_key=imported_record_key(record.record_id),
            series_count=len(series),
            value_count=sum(item.n_values for item in series),
            duration_seconds=max(durations) if durations else None,
            signals=sorted({str(item.signal) for item in series})[:20],
            annotation_keys=sorted({str(item.key) for item in record.annotations})[:20],
            is_replay_compatible=replay_compatible,
        )


def imported_record_key(record_id: str) -> str:
    return hashlib.sha256(record_id.encode()).hexdigest()[:24]

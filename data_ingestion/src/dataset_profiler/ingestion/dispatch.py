from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

from timenet.client import TimeNet

from dataset_profiler.timenet import build_kuka_timef_dataset

from .generic import GenericBuildResult
from .jobs import AssetReceipt, IngestionJob


class SpecializedDispatchError(RuntimeError):
    pass


@dataclass(frozen=True)
class SpecializedSource:
    canonical_url: str
    source_revision: str
    dataset_license_id: str
    archive_name: str
    dataset_id: str
    event_key: str


class SpecializedDispatcher:
    SOURCES: ClassVar[dict[str, SpecializedSource]] = {
        "https://zenodo.org/records/21927431": SpecializedSource(
            canonical_url="https://zenodo.org/records/21927431",
            source_revision="21927431.r4",
            dataset_license_id="cc-by-4.0",
            archive_name="collision-batches-01-42.tar.zst",
            dataset_id="kuka/collision-part1",
            event_key="collision",
        ),
        "https://zenodo.org/records/21941203": SpecializedSource(
            canonical_url="https://zenodo.org/records/21941203",
            source_revision="21941203.r4",
            dataset_license_id="cc-by-4.0",
            archive_name="contact-batches-01-48.tar.zst",
            dataset_id="kuka/contact-part2",
            event_key="intentional_contact",
        ),
    }

    def __init__(self, *, cache_dir: Path, registry_root: Path):
        self.cache_dir = cache_dir.resolve()
        self.registry_root = registry_root.resolve()

    def resolve(self, job: IngestionJob) -> SpecializedSource | None:
        source = self.SOURCES.get(job.source_url)
        if source is None:
            return None
        if (
            job.source_kind.casefold() != "zenodo"
            or job.source_revision != source.source_revision
            or not job.dataset_license_id
            or job.dataset_license_id.casefold() != source.dataset_license_id
        ):
            raise SpecializedDispatchError(
                "Known KUKA source identity has a revision, provider, or dataset-license mismatch"
            )
        return source

    def build(
        self,
        job: IngestionJob,
        source: SpecializedSource,
        receipts: list[AssetReceipt],
    ) -> GenericBuildResult:
        archive = next(
            (
                item
                for item in receipts
                if item.provider_locator.endswith(f":{source.archive_name}")
            ),
            None,
        )
        if archive is None:
            raise SpecializedDispatchError(
                "Known KUKA source requires its exact approved data archive"
            )
        source_root = (
            self.cache_dir
            / "extracted"
            / archive.content_sha256[:2]
            / archive.content_sha256
        ).resolve(strict=True)
        extracted = (self.cache_dir / "extracted").resolve()
        if not source_root.is_relative_to(extracted) or not source_root.is_dir():
            raise SpecializedDispatchError("KUKA source root escaped the verified cache")
        version_dir = self.registry_root / source.dataset_id / "1.0.0"
        if not version_dir.exists():
            version_dir = build_kuka_timef_dataset(
                source.dataset_id,
                source_root,
                self.registry_root,
            )
        loaded = TimeNet(registry=self.registry_root).load(source.dataset_id, auto_build=False)
        summary = []
        value_count = 0
        for record in loaded.records:
            torque = [
                item
                for item in record.time_series
                if item.spec.spec_type == "measured_external_joint_torque"
            ]
            position = [
                item
                for item in record.time_series
                if item.spec.spec_type == "measured_joint_position"
            ]
            if len(torque) != 7 or len(position) != 7:
                raise SpecializedDispatchError("KUKA read-back did not retain fourteen series")
            if any(item.time_axis.period_us != 1_000 for item in record.time_series):
                raise SpecializedDispatchError("KUKA read-back did not retain the 1 kHz axis")
            if not any(item.key == source.event_key for item in record.annotations):
                raise SpecializedDispatchError(
                    "KUKA read-back did not retain publisher event annotations"
                )
            for item in record.time_series:
                values = item.to_numpy().reshape(-1)
                value_count += values.size
                summary.append(
                    {
                        "recordId": record.record_id,
                        "timeSeriesId": item.time_series_id,
                        "valueSha256": hashlib.sha256(values.tobytes()).hexdigest(),
                        "timeAxis": {
                            "kind": "regular",
                            "periodUs": int(item.time_axis.period_us),
                            "values": item.n_values,
                        },
                    }
                )
        if not summary:
            raise SpecializedDispatchError("KUKA read-back contains no records")
        validation = hashlib.sha256(
            json.dumps(summary, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        return GenericBuildResult(
            dataset_id=source.dataset_id,
            dataset_version="1.0.0",
            version_dir=version_dir,
            record_count=len(loaded.records),
            series_count=sum(len(record.time_series) for record in loaded.records),
            value_count=value_count,
            validation_sha256=validation,
        )

    @staticmethod
    def receipt_mapping(source: SpecializedSource) -> dict:
        return {
            "schemaVersion": "1.0",
            "layout": "SPECIALIZED_CONNECTOR",
            "connector": "dataset_profiler.timenet.build_kuka_timef_dataset",
            "datasetId": source.dataset_id,
            "sourceRevision": source.source_revision,
            "eventKey": source.event_key,
        }

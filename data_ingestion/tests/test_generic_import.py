from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from dataset_profiler.ingestion.generic import GenericTimeFBuilder
from dataset_profiler.ingestion.jobs import (
    AssetReceipt,
    IngestionJobStore,
    IngestionState,
    ResourceFormat,
    ResourceProfile,
)
from dataset_profiler.ingestion.mapping import (
    ChannelSpec,
    MappingLayout,
    MappingService,
    MappingSpec,
)
from dataset_profiler.ingestion.worker import GenericImportWorker


class GenericImportWorkerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.store = IngestionJobStore(self.root / "ingestions.sqlite3")

    def tearDown(self) -> None:
        self.store.close()
        self.temporary.cleanup()

    def test_wide_table_builds_validated_timef_once_with_reproducible_receipt(self) -> None:
        job, _ = self.store.get_or_create(
            approved_source_id="src_0123456789abcdef01234567",
            manifest_sha256="a" * 64,
            source_url="https://github.com/example/dataset",
            source_kind="github",
            source_revision="b" * 40,
            asset_ids=["asset_0123456789abcdef"],
            state=IngestionState.QUEUED,
            message="queued",
            dataset_license_id="MIT",
        )
        payload = (
            b"run,time,joint_1,joint_2,label\n"
            b"a,0.0,1.0,2.0,free\n"
            b"a,0.001,1.5,2.5,free\n"
            b"b,0.0,3.0,4.0,contact\n"
            b"b,0.002,3.5,4.5,contact\n"
        )
        digest = hashlib.sha256(payload).hexdigest()
        content = self.root / "cache" / "content" / digest[:2] / digest
        content.parent.mkdir(parents=True)
        content.write_bytes(payload)
        self.store.record_receipt(
            AssetReceipt(
                ingestion_id=job.ingestion_id,
                asset_id="asset_0123456789abcdef",
                provider_locator="owner/repo:file.csv@" + "b" * 40,
                expected_size_bytes=len(payload),
                observed_size_bytes=len(payload),
                content_sha256=digest,
                content_key=f"sha256/{digest[:2]}/{digest}",
                acquired_at=datetime.now(UTC),
            )
        )
        profile = self.store.record_resource(
            ResourceProfile(
                resource_id="res_0123456789abcdef01234567",
                ingestion_id=job.ingestion_id,
                asset_id="asset_0123456789abcdef",
                logical_path="file.csv",
                size_bytes=len(payload),
                content_sha256=digest,
                format=ResourceFormat.CSV,
                details={
                    "columns": [
                        {"name": name, "dtype": "string" if name in {"run", "label"} else "float64"}
                        for name in ["run", "time", "joint_1", "joint_2", "label"]
                    ]
                },
                inspected_at=datetime.now(UTC),
            )
        )
        waiting = self.store.set_state(job.ingestion_id, IngestionState.MAPPING, "map")
        mapping = MappingSpec(
            job_revision=waiting.job_revision,
            resource_id=profile.resource_id,
            resource_sha256=digest,
            layout=MappingLayout.WIDE_TABLE,
            record_selector="run",
            time_selector="time",
            channels=[
                ChannelSpec(selector="joint_1", name="joint_1", unit="newton * meter"),
                ChannelSpec(selector="joint_2", name="joint_2", unit="newton * meter"),
            ],
            annotations=["label"],
        )
        MappingService(self.store).confirm(job.ingestion_id, mapping)
        worker = GenericImportWorker(
            self.store,
            GenericTimeFBuilder(
                cache_dir=self.root / "cache",
                registry_root=self.root / "timef",
            ),
        )

        completed = worker.run_once()

        self.assertEqual(completed.state, IngestionState.READY)
        self.assertIsNone(worker.run_once())
        receipt = self.store.get_final_receipt(job.ingestion_id)
        self.assertIsNotNone(receipt)
        self.assertEqual(receipt.validation["recordCount"], 2)
        self.assertEqual(receipt.validation["seriesCount"], 4)
        self.assertEqual(receipt.validation["valueCount"], 8)
        content = receipt.model_dump(mode="json", by_alias=True, exclude={"receipt_sha256"})
        expected_hash = hashlib.sha256(
            json.dumps(content, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        self.assertEqual(receipt.receipt_sha256, expected_hash)
        self.assertNotIn(str(self.root), receipt.model_dump_json())

    def test_long_table_preserves_per_channel_irregular_time(self) -> None:
        path = self.root / "long.csv"
        path.write_text(
            "time,channel,value\n0.0,a,1\n0.0,b,2\n0.001,a,3\n0.002,b,4\n",
            encoding="utf-8",
        )
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        profile = ResourceProfile(
            resource_id="res_0123456789abcdef01234567",
            ingestion_id="01234567-89ab-cdef-0123-456789abcdef",
            asset_id="asset_0123456789abcdef",
            logical_path="long.csv",
            size_bytes=path.stat().st_size,
            content_sha256=digest,
            format=ResourceFormat.CSV,
            details={},
            inspected_at=datetime.now(UTC),
        )
        mapping = MappingSpec(
            job_revision=1,
            resource_id=profile.resource_id,
            resource_sha256=digest,
            layout=MappingLayout.LONG_TABLE,
            time_selector="time",
            channel_selector="channel",
            value_selector="value",
            channels=[
                ChannelSpec(selector="a", name="a", unit="volt"),
                ChannelSpec(selector="b", name="b", unit="volt"),
            ],
        )
        records = GenericTimeFBuilder(
            cache_dir=self.root / "cache", registry_root=self.root / "timef"
        ).normalize(path, profile, mapping)

        self.assertEqual(records[0].series[0].time_offsets_us.tolist(), [0, 1_000])
        self.assertEqual(records[0].series[1].time_offsets_us.tolist(), [0, 2_000])
        self.assertEqual(records[0].series[1].values.tolist(), [2.0, 4.0])
        result = self._direct_build(path, profile, mapping, 2)
        self.assertEqual((result.record_count, result.series_count, result.value_count), (1, 2, 4))
        repeated = self._direct_build(path, profile, mapping, 2)
        self.assertEqual(repeated.validation_sha256, result.validation_sha256)

    def test_wide_table_accepts_iso_timestamps_as_relative_microseconds(self) -> None:
        path = self.root / "iso.csv"
        path.write_text(
            "timestamp,value\n"
            "2014-02-14 14:27:00,51.846\n"
            "2014-02-14 14:32:00,44.508\n"
            "2014-02-14 14:37:00,41.244\n",
            encoding="utf-8",
        )
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        profile = ResourceProfile(
            resource_id="res_0123456789abcdef01234567",
            ingestion_id="01234567-89ab-cdef-0123-456789abcdef",
            asset_id="asset_0123456789abcdef",
            logical_path="iso.csv",
            size_bytes=path.stat().st_size,
            content_sha256=digest,
            format=ResourceFormat.CSV,
            details={},
            inspected_at=datetime.now(UTC),
        )
        mapping = MappingSpec(
            job_revision=1,
            resource_id=profile.resource_id,
            resource_sha256=digest,
            layout=MappingLayout.WIDE_TABLE,
            time_selector="timestamp",
            channels=[ChannelSpec(selector="value", name="value", unit="percent")],
        )

        records = GenericTimeFBuilder(
            cache_dir=self.root / "cache", registry_root=self.root / "timef"
        ).normalize(path, profile, mapping)

        self.assertEqual(
            records[0].series[0].time_offsets_us.tolist(),
            [0, 300_000_000, 600_000_000],
        )

    def test_named_arrays_honor_explicit_sample_and_channel_axes(self) -> None:
        path = self.root / "signals.npz"
        np.savez(
            path,
            time=np.asarray([0.0, 0.001, 0.003]),
            signals=np.asarray([[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]]),
        )
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        profile = ResourceProfile(
            resource_id="res_0123456789abcdef01234567",
            ingestion_id="01234567-89ab-cdef-0123-456789abcdef",
            asset_id="asset_0123456789abcdef",
            logical_path="signals.npz",
            size_bytes=path.stat().st_size,
            content_sha256=digest,
            format=ResourceFormat.NPZ,
            details={},
            inspected_at=datetime.now(UTC),
        )
        mapping = MappingSpec(
            job_revision=1,
            resource_id=profile.resource_id,
            resource_sha256=digest,
            layout=MappingLayout.NAMED_ARRAYS,
            time_selector="time",
            signal_selector="signals",
            sample_axis=1,
            channel_axis=0,
            channels=[
                ChannelSpec(selector="0", name="first", unit="meter"),
                ChannelSpec(selector="1", name="second", unit="meter"),
            ],
        )
        records = GenericTimeFBuilder(
            cache_dir=self.root / "cache", registry_root=self.root / "timef"
        ).normalize(path, profile, mapping)

        self.assertEqual(records[0].series[0].values.tolist(), [1.0, 2.0, 3.0])
        self.assertEqual(records[0].series[1].values.tolist(), [4.0, 5.0, 6.0])
        self.assertEqual(records[0].series[0].time_offsets_us.tolist(), [0, 1_000, 3_000])
        result = self._direct_build(path, profile, mapping, 3)
        self.assertEqual((result.record_count, result.series_count, result.value_count), (1, 2, 6))

    def _direct_build(
        self,
        source: Path,
        profile: ResourceProfile,
        mapping: MappingSpec,
        index: int,
    ):
        payload = source.read_bytes()
        digest = hashlib.sha256(payload).hexdigest()
        content = self.root / "cache" / "content" / digest[:2] / digest
        content.parent.mkdir(parents=True, exist_ok=True)
        content.write_bytes(payload)
        job = self.store.get_or_create(
            approved_source_id=f"src_{index:024x}",
            manifest_sha256=f"{index:064x}",
            source_url=f"https://github.com/example/dataset-{index}",
            source_kind="github",
            source_revision=f"{index:040x}",
            asset_ids=["asset_0123456789abcdef"],
            state=IngestionState.VALIDATING,
            message="validating",
            dataset_license_id="MIT",
        )[0]
        receipt = AssetReceipt(
            ingestion_id=job.ingestion_id,
            asset_id="asset_0123456789abcdef",
            provider_locator=f"example:{index}",
            expected_size_bytes=len(payload),
            observed_size_bytes=len(payload),
            content_sha256=digest,
            content_key=f"sha256/{digest[:2]}/{digest}",
            acquired_at=datetime.now(UTC),
        )
        bound_profile = profile.model_copy(
            update={
                "ingestion_id": job.ingestion_id,
                "content_sha256": digest,
                "size_bytes": len(payload),
            }
        )
        bound_mapping = mapping.model_copy(update={"resource_sha256": digest})
        return GenericTimeFBuilder(
            cache_dir=self.root / "cache",
            registry_root=self.root / "timef",
        ).build(job, bound_profile, bound_mapping, [receipt])


if __name__ == "__main__":
    unittest.main()

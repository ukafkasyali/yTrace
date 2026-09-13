from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
from dataset_profiler.ingestion.dispatch import (
    SpecializedDispatcher,
    SpecializedDispatchError,
)
from dataset_profiler.ingestion.jobs import AssetReceipt, IngestionJob, IngestionState
from scipy.io import savemat
from timenet.client import TimeNet


class SpecializedDispatcherTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.dispatcher = SpecializedDispatcher(
            cache_dir=self.root / "cache",
            registry_root=self.root / "timef",
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_exact_part_identities_use_distinct_specialized_connectors(self) -> None:
        cases = [
            (
                "https://zenodo.org/records/21927431",
                "21927431.r4",
                "collision-batches-01-42.tar.zst",
                "kuka/collision-part1",
            ),
            (
                "https://zenodo.org/records/21941203",
                "21941203.r4",
                "contact-batches-01-48.tar.zst",
                "kuka/contact-part2",
            ),
        ]
        for index, (url, revision, archive_name, expected_dataset) in enumerate(cases):
            with self.subTest(dataset=expected_dataset):
                job = self._job(index, url, revision)
                source = self.dispatcher.resolve(job)
                digest = f"{index + 1:064x}"
                source_root = (
                    self.root / "cache" / "extracted" / digest[:2] / digest
                )
                self._write_run(source_root, "batch-01")
                receipt = AssetReceipt(
                    ingestion_id=job.ingestion_id,
                    asset_id="asset_0123456789abcdef",
                    provider_locator=f"zenodo:{revision.split('.')[0]}:{archive_name}",
                    expected_size_bytes=1,
                    observed_size_bytes=1,
                    content_sha256=digest,
                    content_key=f"sha256/{digest[:2]}/{digest}",
                    acquired_at=datetime.now(UTC),
                )

                result = self.dispatcher.build(job, source, [receipt])
                preloaded = self.dispatcher.load_prebuilt(source)

                self.assertEqual(result.dataset_id, expected_dataset)
                self.assertEqual(result.record_count, 1)
                self.assertEqual(result.series_count, 14)
                self.assertEqual(result.value_count, 70)
                self.assertEqual(preloaded, result)

    def test_known_url_never_falls_back_when_revision_or_license_differs(self) -> None:
        bad_revision = self._job(
            0,
            "https://zenodo.org/records/21927431",
            "21927431.r5",
        )
        with self.assertRaises(SpecializedDispatchError):
            self.dispatcher.resolve(bad_revision)

        bad_license = bad_revision.model_copy(
            update={"source_revision": "21927431.r4", "dataset_license_id": "MIT"}
        )
        with self.assertRaises(SpecializedDispatchError):
            self.dispatcher.resolve(bad_license)

    def test_preloaded_artifact_must_match_the_approved_source_identity(self) -> None:
        job = self._job(0, "https://zenodo.org/records/21927431", "21927431.r4")
        source = self.dispatcher.resolve(job)
        version_dir = self.root / "timef" / source.dataset_id / "1.0.0"
        version_dir.mkdir(parents=True)
        (version_dir / "manifest.json").write_text(
            json.dumps(
                {
                    "dataset_id": source.dataset_id,
                    "metadata": {
                        "dataset_id": source.dataset_id,
                        "dataset_version": "1.0.0",
                        "source_url": "https://zenodo.org/records/21941203",
                        "license": "CC-BY-4.0",
                    },
                }
            ),
            encoding="utf-8",
        )

        with self.assertRaisesRegex(
            SpecializedDispatchError,
            "does not match the approved source",
        ):
            self.dispatcher.load_prebuilt(source)

    def test_separate_batch_archives_keep_unique_batch_provenance(self) -> None:
        job = self._job(0, "https://zenodo.org/records/21927431", "21927431.r4")
        source = replace(self.dispatcher.resolve(job), batch_count=2)
        receipts = []
        for batch in (1, 2):
            digest = f"{batch + 10:064x}"
            source_root = self.root / "cache" / "extracted" / digest[:2] / digest
            self._write_run(source_root)
            receipts.append(
                AssetReceipt(
                    ingestion_id=job.ingestion_id,
                    asset_id=f"asset_{batch:016x}",
                    provider_locator=(
                        f"zenodo:21927431:collision-batch-{batch:02d}.tar.zst"
                    ),
                    expected_size_bytes=1,
                    observed_size_bytes=1,
                    content_sha256=digest,
                    content_key=f"sha256/{digest[:2]}/{digest}",
                    acquired_at=datetime.now(UTC),
                )
            )

        result = self.dispatcher.build(job, source, receipts)

        self.assertEqual(result.record_count, 2)
        loaded = TimeNet(registry=self.root / "timef").load(
            source.dataset_id, auto_build=False
        )
        source_ids = {
            annotation.value
            for record in loaded.records
            for annotation in record.annotations
            if annotation.key == "source_run_id"
        }
        self.assertEqual(
            source_ids,
            {"batch-01/03-15-12-53", "batch-02/03-15-12-53"},
        )

    def test_separate_batch_archives_require_the_complete_approved_part(self) -> None:
        job = self._job(0, "https://zenodo.org/records/21927431", "21927431.r4")
        source = replace(self.dispatcher.resolve(job), batch_count=2)
        digest = f"{11:064x}"
        source_root = self.root / "cache" / "extracted" / digest[:2] / digest
        self._write_run(source_root)
        receipt = AssetReceipt(
            ingestion_id=job.ingestion_id,
            asset_id="asset_0000000000000001",
            provider_locator="zenodo:21927431:collision-batch-01.tar.zst",
            expected_size_bytes=1,
            observed_size_bytes=1,
            content_sha256=digest,
            content_key=f"sha256/{digest[:2]}/{digest}",
            acquired_at=datetime.now(UTC),
        )

        with self.assertRaisesRegex(SpecializedDispatchError, "missing 02"):
            self.dispatcher.build(job, source, [receipt])

    @staticmethod
    def _job(index: int, url: str, revision: str) -> IngestionJob:
        return IngestionJob(
            ingestion_id=f"00000000-0000-0000-0000-{index + 1:012d}",
            approved_source_id=f"src_{index + 1:024x}",
            manifest_sha256="a" * 64,
            source_url=url,
            source_kind="ZENODO",
            source_revision=revision,
            dataset_license_id="cc-by-4.0",
            asset_ids=["asset_0123456789abcdef"],
            job_revision=1,
            state=IngestionState.VALIDATING,
            message="validating",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )

    @staticmethod
    def _write_run(root: Path, batch: str | None = None) -> None:
        run = root / batch / "03-15-12-53" if batch else root / "03-15-12-53"
        run.mkdir(parents=True)
        time_axis = np.arange(5, dtype=np.float64) / 1_000
        savemat(run / "JsmoExp.mat", {"rt_tout": time_axis[:, None]})
        savemat(
            run / "JK_MsrExtTrq.mat",
            {"MsrExtTrq": np.vstack([time_axis, np.arange(35).reshape(7, 5)])},
        )
        savemat(
            run / "JK_PosMsr.mat",
            {"PosMsr": np.vstack([time_axis, np.arange(100, 135).reshape(7, 5)])},
        )
        savemat(run / "JK_moments.mat", {"JK_moments": np.array([[1], [3], [5]])})


if __name__ == "__main__":
    unittest.main()

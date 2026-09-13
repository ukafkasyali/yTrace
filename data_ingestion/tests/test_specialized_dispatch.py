from __future__ import annotations

import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
from scipy.io import savemat

from dataset_profiler.ingestion.dispatch import (
    SpecializedDispatcher,
    SpecializedDispatchError,
)
from dataset_profiler.ingestion.jobs import AssetReceipt, IngestionJob, IngestionState


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

                self.assertEqual(result.dataset_id, expected_dataset)
                self.assertEqual(result.record_count, 1)
                self.assertEqual(result.series_count, 14)
                self.assertEqual(result.value_count, 70)

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
    def _write_run(root: Path, batch: str) -> None:
        run = root / batch / "03-15-12-53"
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

import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from dataset_profiler.ingestion import (
    IngestionJobConflict,
    IngestionJobStore,
    IngestionState,
    ManifestContractError,
    parse_manifest,
)

CONTRACT_FIXTURE = (
    Path(__file__).resolve().parents[2]
    / "docs"
    / "contracts"
    / "approved-source-manifest-v1.1.json"
)


class ManifestContractTests(unittest.TestCase):
    def fixture(self) -> dict:
        return json.loads(CONTRACT_FIXTURE.read_text(encoding="utf-8"))

    def test_golden_manifest_is_accepted_without_sourcing_import(self) -> None:
        manifest = parse_manifest(self.fixture())

        self.assertEqual(manifest.source_revision, "123.r1")
        self.assertEqual([asset.name for asset in manifest.data_assets], ["signals.csv"])
        self.assertEqual(manifest.dataset_license_id, "cc-by-4.0")

    def test_legacy_or_provider_mismatched_manifest_fails_closed(self) -> None:
        legacy = self.fixture()
        legacy["schemaVersion"] = "1.0"
        with self.assertRaises(ManifestContractError):
            parse_manifest(legacy)

        mismatched = self.fixture()
        mismatched["assets"][0]["downloadUrl"] = "https://example.test/signals.csv"
        with self.assertRaises(ManifestContractError):
            parse_manifest(mismatched)

        credentialed = self.fixture()
        credentialed["canonicalUrl"] = "https://user:redacted@zenodo.org/records/123"
        with self.assertRaises(ManifestContractError):
            parse_manifest(credentialed)

    def test_manifest_without_data_assets_fails_closed(self) -> None:
        payload = self.fixture()
        payload["assets"] = [payload["assets"][1]]

        with self.assertRaises(ManifestContractError):
            parse_manifest(payload)


class IngestionJobStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.store = IngestionJobStore(Path(self.temporary.name) / "ingestions.sqlite3")
        self.arguments = {
            "approved_source_id": "src_0123456789abcdef01234567",
            "manifest_sha256": "a" * 64,
            "source_url": "https://zenodo.org/records/123",
            "source_kind": "ZENODO",
            "source_revision": "123.r1",
            "asset_ids": ["asset_0123456789abcdef"],
            "state": IngestionState.QUEUED,
            "message": "Queued for acquisition",
        }

    def tearDown(self) -> None:
        self.store.close()
        self.temporary.cleanup()

    def test_one_job_is_reused_for_matching_source_and_assets(self) -> None:
        first, created = self.store.get_or_create(**self.arguments)
        repeated, repeated_created = self.store.get_or_create(**self.arguments)

        self.assertTrue(created)
        self.assertFalse(repeated_created)
        self.assertEqual(first.ingestion_id, repeated.ingestion_id)
        self.assertEqual(self.store.get(first.ingestion_id), first)

    def test_conflicting_asset_selection_does_not_create_another_job(self) -> None:
        first, _ = self.store.get_or_create(**self.arguments)

        with self.assertRaises(IngestionJobConflict):
            self.store.get_or_create(
                **(self.arguments | {"asset_ids": ["asset_fedcba9876543210"]})
            )

        self.assertEqual(
            self.store.find_by_source(self.arguments["approved_source_id"]),
            first,
        )

    def test_concurrent_matching_requests_share_one_job(self) -> None:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self.store.get_or_create(**self.arguments), range(2)))

        self.assertEqual(results[0][0].ingestion_id, results[1][0].ingestion_id)
        self.assertEqual(sorted(created for _, created in results), [False, True])


if __name__ == "__main__":
    unittest.main()

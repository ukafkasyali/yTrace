import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from dataset_profiler.ingestion import (
    CreateIngestion,
    IngestionJobConflict,
    IngestionJobStore,
    IngestionService,
    IngestionState,
    ManifestContractError,
    parse_manifest,
)
from dataset_profiler.ingestion.api import create_app
from dataset_profiler.ingestion.service import (
    ApprovedSourceResolutionError,
    HttpApprovedSourceResolver,
    ResolvedApprovedSource,
    canonical_payload_sha256,
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


class FakeResolver:
    def __init__(self, payload: dict):
        self.payload = payload
        self.calls = 0

    def resolve(self, approved_source_id: str) -> ResolvedApprovedSource:
        self.calls += 1
        return ResolvedApprovedSource(
            approved_source_id=approved_source_id,
            manifest_sha256=canonical_payload_sha256(self.payload),
            manifest=parse_manifest(self.payload),
        )

    def close(self) -> None:
        pass


class FailingResolver:
    def resolve(self, approved_source_id: str) -> ResolvedApprovedSource:
        raise ApprovedSourceResolutionError("Approved source service is unavailable")

    def close(self) -> None:
        pass


class IngestionServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.payload = json.loads(CONTRACT_FIXTURE.read_text(encoding="utf-8"))
        self.resolver = FakeResolver(self.payload)
        self.service = IngestionService(Path(self.temporary.name), self.resolver)
        self.request = CreateIngestion(approved_source_id="src_0123456789abcdef01234567")

    def tearDown(self) -> None:
        self.service.close()
        self.temporary.cleanup()

    def test_matching_retry_reuses_job_without_resolving_again(self) -> None:
        first, created = self.service.create(self.request)
        repeated, repeated_created = self.service.create(self.request)

        self.assertTrue(created)
        self.assertFalse(repeated_created)
        self.assertEqual(first.ingestion_id, repeated.ingestion_id)
        self.assertEqual(first.asset_ids, ["asset_0123456789abcdef"])
        self.assertEqual(self.resolver.calls, 1)

    def test_unapproved_or_documentation_asset_selection_conflicts(self) -> None:
        with self.assertRaises(IngestionJobConflict):
            self.service.create(
                CreateIngestion(
                    approved_source_id=self.request.approved_source_id,
                    asset_ids=["asset_fedcba9876543210"],
                )
            )
        self.assertIsNone(self.service.jobs.find_by_source(self.request.approved_source_id))

    def test_missing_dataset_license_pauses_one_job_for_input(self) -> None:
        self.payload["datasetLicenseId"] = None
        self.payload["licenseId"] = "MIT"
        self.payload["codeLicenseId"] = "MIT"

        job, _ = self.service.create(self.request)

        self.assertEqual(job.state, IngestionState.NEEDS_INPUT)
        self.assertIn("Dataset-file license", job.message)


class HttpResolverTests(unittest.TestCase):
    def test_resolver_verifies_catalog_manifest_hash(self) -> None:
        payload = json.loads(CONTRACT_FIXTURE.read_text(encoding="utf-8"))
        approved_source_id = "src_0123456789abcdef01234567"
        expected_hash = canonical_payload_sha256(payload)

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/manifest"):
                return httpx.Response(200, json=payload)
            return httpx.Response(
                200,
                json={
                    "approvedSourceId": approved_source_id,
                    "latestManifestSha256": expected_hash,
                    "isAcquisitionReady": True,
                },
            )

        resolver = HttpApprovedSourceResolver(
            "http://127.0.0.1:8000",
            httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False),
        )

        resolved = resolver.resolve(approved_source_id)

        self.assertEqual(resolved.manifest_sha256, expected_hash)
        self.assertEqual(resolved.manifest.source_revision, "123.r1")
        resolver.close()

    def test_resolver_rejects_manifest_hash_mismatch(self) -> None:
        payload = json.loads(CONTRACT_FIXTURE.read_text(encoding="utf-8"))

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/manifest"):
                return httpx.Response(200, json=payload)
            return httpx.Response(
                200,
                json={
                    "approvedSourceId": "src_0123456789abcdef01234567",
                    "latestManifestSha256": "0" * 64,
                    "isAcquisitionReady": True,
                },
            )

        resolver = HttpApprovedSourceResolver(
            "http://127.0.0.1:8000",
            httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False),
        )

        with self.assertRaisesRegex(ValueError, "integrity"):
            resolver.resolve("src_0123456789abcdef01234567")
        resolver.close()


class IngestionApiTests(unittest.TestCase):
    def test_create_and_get_return_the_same_persisted_job(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            payload = json.loads(CONTRACT_FIXTURE.read_text(encoding="utf-8"))
            service = IngestionService(Path(temporary), FakeResolver(payload))
            with TestClient(create_app(service)) as client:
                created = client.post(
                    "/api/ingestions",
                    json={"approvedSourceId": "src_0123456789abcdef01234567"},
                )
                repeated = client.post(
                    "/api/ingestions",
                    json={"approvedSourceId": "src_0123456789abcdef01234567"},
                )
                fetched = client.get(f"/api/ingestions/{created.json()['ingestionId']}")

                self.assertEqual(created.status_code, 202)
                self.assertEqual(repeated.json()["ingestionId"], created.json()["ingestionId"])
                self.assertEqual(fetched.json(), created.json())
                self.assertEqual(created.json()["state"], "queued")
            service.close()

    def test_conflicting_asset_selection_returns_409(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            payload = json.loads(CONTRACT_FIXTURE.read_text(encoding="utf-8"))
            service = IngestionService(Path(temporary), FakeResolver(payload))
            with TestClient(create_app(service)) as client:
                client.post(
                    "/api/ingestions",
                    json={"approvedSourceId": "src_0123456789abcdef01234567"},
                )
                conflict = client.post(
                    "/api/ingestions",
                    json={
                        "approvedSourceId": "src_0123456789abcdef01234567",
                        "assetIds": ["asset_fedcba9876543210"],
                    },
                )

                self.assertEqual(conflict.status_code, 409)
                self.assertEqual(conflict.json()["error"]["code"], "INGESTION_CONFLICT")
            service.close()

    def test_resolver_failure_returns_safe_422(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            service = IngestionService(Path(temporary), FailingResolver())
            with TestClient(create_app(service)) as client:
                response = client.post(
                    "/api/ingestions",
                    json={"approvedSourceId": "src_0123456789abcdef01234567"},
                )

                self.assertEqual(response.status_code, 422)
                self.assertEqual(
                    response.json()["error"]["code"],
                    "APPROVED_SOURCE_UNAVAILABLE",
                )
            service.close()

    def test_invalid_create_uses_shared_error_envelope(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            payload = json.loads(CONTRACT_FIXTURE.read_text(encoding="utf-8"))
            service = IngestionService(Path(temporary), FakeResolver(payload))
            with TestClient(create_app(service)) as client:
                response = client.post("/api/ingestions", json={"approvedSourceId": "bad"})

                self.assertEqual(response.status_code, 422)
                self.assertEqual(response.json()["error"]["code"], "INVALID_REQUEST")
            service.close()


if __name__ == "__main__":
    unittest.main()

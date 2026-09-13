import hashlib
import io
import json
import tempfile
import unittest
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

from dataset_profiler.ingestion import (
    AcquiredAsset,
    AcquisitionError,
    AcquisitionWorker,
    AssetReceipt,
    CreateIngestion,
    IngestionJobConflict,
    IngestionJobStore,
    IngestionService,
    IngestionState,
    ManifestContractError,
    ResourceFormat,
    ResourceInventory,
    SafeArchiveExtractor,
    ZenodoAcquirer,
    parse_manifest,
)
from dataset_profiler.ingestion.api import create_app
from dataset_profiler.ingestion.catalog import (
    ImportedRecordPage,
    ImportedRecordPagination,
    ImportedRecordSummary,
)
from dataset_profiler.ingestion.contracts import ManifestAsset, SourceKind
from dataset_profiler.ingestion.jobs import FinalReceipt
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


class ZenodoAcquirerTests(unittest.TestCase):
    def asset(self, content: bytes, checksum: str | None = None) -> ManifestAsset:
        return ManifestAsset.model_validate(
            {
                "assetId": "asset_0123456789abcdef",
                "name": "signals.csv",
                "role": "DATA",
                "sizeBytes": len(content),
                "providerLocator": "zenodo:123:signals.csv",
                "downloadUrl": "https://zenodo.org/api/files/123/signals.csv",
                "sourceChecksum": (
                    {"algorithm": "md5", "value": checksum} if checksum else None
                ),
            }
        )

    def test_verified_content_is_promoted_and_reused_by_sha256(self) -> None:
        content = b"robot-signal-data"
        checksum = hashlib.md5(content, usedforsecurity=False).hexdigest()
        requests = 0

        def handler(_: httpx.Request) -> httpx.Response:
            nonlocal requests
            requests += 1
            return httpx.Response(200, content=content)

        with tempfile.TemporaryDirectory() as temporary:
            acquirer = ZenodoAcquirer(
                Path(temporary),
                client=httpx.Client(transport=httpx.MockTransport(handler)),
                validate_dns=False,
            )
            first = acquirer.acquire(SourceKind.ZENODO, self.asset(content, checksum))
            second = acquirer.acquire(SourceKind.ZENODO, self.asset(content, checksum))

            self.assertEqual(first.content_sha256, hashlib.sha256(content).hexdigest())
            self.assertEqual(first.content_path, second.content_path)
            self.assertEqual(first.content_path.read_bytes(), content)
            self.assertEqual(requests, 2)
            self.assertEqual(list((Path(temporary) / "staging").iterdir()), [])
            acquirer.close()

    def test_truncation_checksum_redirect_and_cross_host_fail_closed(self) -> None:
        content = b"robot-signal-data"
        cases = [
            httpx.Response(200, content=content[:-1], headers={"Content-Length": str(len(content))}),
            httpx.Response(200, content=content),
            httpx.Response(302, headers={"Location": "https://zenodo.org/other"}),
        ]
        assets = [
            self.asset(content),
            self.asset(content, "0" * 32),
            self.asset(content),
        ]
        with tempfile.TemporaryDirectory() as temporary:
            for response, asset in zip(cases, assets, strict=True):
                acquirer = ZenodoAcquirer(
                    Path(temporary),
                    client=httpx.Client(
                        transport=httpx.MockTransport(lambda _, value=response: value)
                    ),
                    validate_dns=False,
                )
                with self.assertRaises(AcquisitionError):
                    acquirer.acquire(SourceKind.ZENODO, asset)
                acquirer.close()

            cross_host = self.asset(content).model_copy(
                update={"download_url": "https://api.github.com/repos/org/repo"}
            )
            acquirer = ZenodoAcquirer(Path(temporary), validate_dns=False)
            with self.assertRaisesRegex(AcquisitionError, "allowlisted"):
                acquirer.acquire(SourceKind.ZENODO, cross_host)
            acquirer.close()

    def test_dns_timeout_and_configured_size_fail_closed(self) -> None:
        content = b"robot-signal-data"
        asset = self.asset(content)
        with tempfile.TemporaryDirectory() as temporary:
            oversized = ZenodoAcquirer(
                Path(temporary),
                max_asset_bytes=len(content) - 1,
                client=httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200))),
                validate_dns=False,
            )
            with self.assertRaisesRegex(AcquisitionError, "configured download limit"):
                oversized.acquire(SourceKind.ZENODO, asset)
            oversized.close()

            def timeout_handler(request: httpx.Request) -> httpx.Response:
                raise httpx.ReadTimeout("timeout", request=request)

            timeout = ZenodoAcquirer(
                Path(temporary),
                client=httpx.Client(transport=httpx.MockTransport(timeout_handler)),
                validate_dns=False,
            )
            with self.assertRaisesRegex(AcquisitionError, "download failed"):
                timeout.acquire(SourceKind.ZENODO, asset)
            timeout.close()

            dns = ZenodoAcquirer(Path(temporary), validate_dns=True)
            with patch(
                "dataset_profiler.ingestion.acquisition.socket.getaddrinfo",
                return_value=[(2, 1, 6, "", ("127.0.0.1", 443))],
            ), self.assertRaisesRegex(AcquisitionError, "non-public"):
                dns.acquire(SourceKind.ZENODO, asset)
            dns.close()


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

    def test_claim_and_asset_receipt_survive_restart(self) -> None:
        job, _ = self.store.get_or_create(**self.arguments)
        claimed = self.store.claim_next_acquisition()
        assert claimed is not None
        self.assertEqual(claimed.ingestion_id, job.ingestion_id)
        self.assertEqual(claimed.state, IngestionState.ACQUIRING)
        self.assertIsNone(self.store.claim_next_acquisition())

        receipt = AssetReceipt(
            ingestion_id=job.ingestion_id,
            asset_id="asset_0123456789abcdef",
            provider_locator="zenodo:123:signals.csv",
            expected_size_bytes=17,
            source_checksum_algorithm="md5",
            source_checksum_value="a" * 32,
            observed_size_bytes=17,
            content_sha256="b" * 64,
            content_key=f"sha256/bb/{'b' * 64}",
            acquired_at=datetime.now(UTC),
        )
        self.store.record_receipt(receipt)
        self.store.close()
        self.store = IngestionJobStore(Path(self.temporary.name) / "ingestions.sqlite3")

        self.assertEqual(self.store.list_receipts(job.ingestion_id), [receipt])

    def test_receipt_cannot_be_replaced(self) -> None:
        job, _ = self.store.get_or_create(**self.arguments)
        receipt = AssetReceipt(
            ingestion_id=job.ingestion_id,
            asset_id="asset_0123456789abcdef",
            provider_locator="zenodo:123:signals.csv",
            expected_size_bytes=17,
            observed_size_bytes=17,
            content_sha256="b" * 64,
            content_key=f"sha256/bb/{'b' * 64}",
            acquired_at=datetime.now(UTC),
        )
        self.store.record_receipt(receipt)

        with self.assertRaises(IngestionJobConflict):
            self.store.record_receipt(receipt.model_copy(update={"content_sha256": "c" * 64}))


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


class FakeAcquirer:
    def __init__(self, contents: dict[str, bytes], cache_dir: Path):
        self.contents = contents
        self.cache_dir = cache_dir
        self.calls: list[str] = []
        self.cached: set[str] = set()
        self.fail_once: set[str] = set()

    def acquire(self, manifest, asset: ManifestAsset) -> AcquiredAsset:
        self.calls.append(asset.asset_id)
        if asset.asset_id in self.fail_once:
            self.fail_once.remove(asset.asset_id)
            raise AcquisitionError("synthetic acquisition failure")
        content = self.contents[asset.asset_id]
        digest = hashlib.sha256(content).hexdigest()
        self.cached.add(digest)
        path = self.verified_content_path(digest)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return AcquiredAsset(asset.asset_id, len(content), digest, path)

    def has_verified_content(self, content_sha256: str, size_bytes: int) -> bool:
        path = self.verified_content_path(content_sha256)
        return content_sha256 in self.cached and path.is_file() and path.stat().st_size == size_bytes

    def verified_content_path(self, content_sha256: str) -> Path:
        return self.cache_dir / "content" / content_sha256[:2] / content_sha256


class AcquisitionWorkerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.payload = json.loads(CONTRACT_FIXTURE.read_text(encoding="utf-8"))
        content = b"timestamp,joint_1\n0.0,1.0\n"
        self.payload["assets"][0]["sizeBytes"] = len(content)
        self.resolver = FakeResolver(self.payload)
        self.service = IngestionService(Path(self.temporary.name), self.resolver)
        self.job, _ = self.service.create(
            CreateIngestion(approved_source_id="src_0123456789abcdef01234567")
        )
        self.acquirer = FakeAcquirer(
            {"asset_0123456789abcdef": content},
            Path(self.temporary.name) / "cache",
        )
        self.worker = AcquisitionWorker(self.service.jobs, self.resolver, self.acquirer)

    def tearDown(self) -> None:
        self.service.close()
        self.temporary.cleanup()

    def test_worker_verifies_assets_and_persists_public_receipt(self) -> None:
        result = self.worker.run_once()

        assert result is not None
        self.assertEqual(result.state, IngestionState.INSPECTING)
        receipt = self.service.jobs.list_receipts(result.ingestion_id)[0]
        self.assertEqual(receipt.provider_locator, "zenodo:123:signals.csv")
        self.assertEqual(receipt.observed_size_bytes, len(self.acquirer.contents[receipt.asset_id]))
        self.assertNotIn(str(Path(self.temporary.name)), receipt.model_dump_json())

    def test_retry_reuses_persisted_verified_content(self) -> None:
        second_id = "asset_1111111111111111"
        second_content = b"second asset"
        self.payload["assets"].append(
            self.payload["assets"][0]
            | {
                "assetId": second_id,
                "name": "second.csv",
                "sizeBytes": len(second_content),
                "providerLocator": "zenodo:123:second.csv",
                "downloadUrl": "https://zenodo.org/api/files/123/second.csv",
                "sourceChecksum": None,
            }
        )
        self.acquirer.contents[second_id] = second_content
        self.acquirer.fail_once.add(second_id)
        resolved = self.resolver.resolve(self.job.approved_source_id)
        self.service.jobs.connection.execute(
            "UPDATE ingestion_jobs SET manifest_sha256 = ?, asset_ids_json = ? "
            "WHERE ingestion_id = ?",
            (
                resolved.manifest_sha256,
                json.dumps(sorted(["asset_0123456789abcdef", second_id])),
                self.job.ingestion_id,
            ),
        )

        failed = self.worker.run_once()
        retried, created = self.service.create(
            CreateIngestion(approved_source_id=self.job.approved_source_id)
        )
        completed = self.worker.run_once()

        assert failed is not None and completed is not None
        self.assertEqual(failed.state, IngestionState.FAILED)
        self.assertFalse(created)
        self.assertEqual(retried.ingestion_id, failed.ingestion_id)
        self.assertEqual(completed.state, IngestionState.INSPECTING)
        self.assertEqual(self.acquirer.calls.count("asset_0123456789abcdef"), 1)
        self.assertEqual(len(self.service.jobs.list_receipts(self.job.ingestion_id)), 2)

    def test_manifest_drift_fails_without_fetching(self) -> None:
        self.payload["name"] = "changed after job creation"

        result = self.worker.run_once()

        assert result is not None
        self.assertEqual(result.state, IngestionState.FAILED)
        self.assertEqual(self.acquirer.calls, [])

    def test_interrupted_acquisition_is_requeued_explicitly(self) -> None:
        claimed = self.service.jobs.claim_next_acquisition()
        assert claimed is not None

        self.assertEqual(self.worker.recover_interrupted(), 1)
        self.assertEqual(
            self.service.jobs.get(claimed.ingestion_id).state,
            IngestionState.QUEUED,
        )

    def test_worker_extracts_archive_before_inventory(self) -> None:
        archive_buffer = io.BytesIO()
        with zipfile.ZipFile(archive_buffer, "w") as archive:
            archive.writestr("nested/signals.csv", "time,joint\n0,1\n")
        content = archive_buffer.getvalue()
        payload = json.loads(CONTRACT_FIXTURE.read_text(encoding="utf-8"))
        payload["assets"][0].update(
            {
                "name": "signals.zip",
                "sizeBytes": len(content),
                "providerLocator": "zenodo:123:signals.zip",
                "downloadUrl": "https://zenodo.org/api/files/123/signals.zip",
                "sourceChecksum": {
                    "algorithm": "md5",
                    "value": hashlib.md5(content, usedforsecurity=False).hexdigest(),
                },
            }
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            resolver = FakeResolver(payload)
            service = IngestionService(root, resolver)
            job, _ = service.create(
                CreateIngestion(approved_source_id="src_0123456789abcdef01234567")
            )
            acquirer = FakeAcquirer({"asset_0123456789abcdef": content}, root / "cache")
            worker = AcquisitionWorker(
                service.jobs,
                resolver,
                acquirer,
                extractor=SafeArchiveExtractor(root / "cache"),
                inventory=ResourceInventory(),
            )

            result = worker.run_once()

            assert result is not None
            self.assertEqual(result.state, IngestionState.MAPPING)
            receipt = service.jobs.list_receipts(job.ingestion_id)[0]
            extracted = (
                root
                / "cache"
                / "extracted"
                / receipt.content_sha256[:2]
                / receipt.content_sha256
                / "nested"
                / "signals.csv"
            )
            self.assertEqual(extracted.read_text(), "time,joint\n0,1\n")
            resources = service.jobs.list_resources(job.ingestion_id)
            self.assertEqual(len(resources), 1)
            self.assertEqual(resources[0].format, ResourceFormat.CSV)
            self.assertEqual(resources[0].logical_path, "nested/signals.csv")
            service.close()


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

    def test_matching_retry_requeues_the_same_failed_job(self) -> None:
        first, _ = self.service.create(self.request)
        self.service.jobs.set_state(first.ingestion_id, IngestionState.FAILED, "failed")

        retried, created = self.service.create(self.request)

        self.assertFalse(created)
        self.assertEqual(retried.ingestion_id, first.ingestion_id)
        self.assertEqual(retried.state, IngestionState.QUEUED)
        self.assertEqual(self.resolver.calls, 1)


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
                by_source = client.get(
                    "/api/ingestions/by-source/src_0123456789abcdef01234567"
                )
                assets = client.get(
                    f"/api/ingestions/{created.json()['ingestionId']}/assets"
                )
                resources = client.get(
                    f"/api/ingestions/{created.json()['ingestionId']}/resources"
                )
                receipt = client.get(
                    f"/api/ingestions/{created.json()['ingestionId']}/receipt"
                )

                self.assertEqual(created.status_code, 202)
                self.assertEqual(repeated.json()["ingestionId"], created.json()["ingestionId"])
                self.assertEqual(fetched.json(), created.json())
                self.assertEqual(by_source.json(), created.json())
                self.assertEqual(created.json()["state"], "queued")
                self.assertEqual(assets.status_code, 200)
                self.assertEqual(assets.json(), [])
                self.assertEqual(resources.status_code, 200)
                self.assertEqual(resources.json(), [])
                self.assertEqual(receipt.status_code, 200)
                self.assertIsNone(receipt.json())
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

    def test_validated_ingestion_exposes_paginated_record_summaries(self) -> None:
        class FakeCatalog:
            request = None

            def list_records(self, dataset_id, dataset_version, *, page, page_size):
                self.request = (dataset_id, dataset_version, page, page_size)
                return ImportedRecordPage(
                    dataset_id=dataset_id,
                    dataset_version=dataset_version,
                    data=[
                        ImportedRecordSummary(
                            record_id="batch-01/run-01",
                            series_count=14,
                            value_count=140,
                            duration_seconds=0.009,
                            signals=["joint_1", "joint_2"],
                            annotation_keys=["collision"],
                        )
                    ],
                    pagination=ImportedRecordPagination(
                        page=page,
                        page_size=page_size,
                        total_items=206,
                        total_pages=206,
                    ),
                )

        with tempfile.TemporaryDirectory() as temporary:
            payload = json.loads(CONTRACT_FIXTURE.read_text(encoding="utf-8"))
            service = IngestionService(Path(temporary), FakeResolver(payload))
            job, _ = service.create(
                CreateIngestion(approved_source_id="src_0123456789abcdef01234567")
            )
            receipt = FinalReceipt.model_validate(
                {
                    "receiptSha256": "a" * 64,
                    "ingestionId": job.ingestion_id,
                    "approvedSourceId": job.approved_source_id,
                    "manifestSha256": job.manifest_sha256,
                    "sourceUrl": job.source_url,
                    "sourceKind": job.source_kind,
                    "sourceRevision": job.source_revision,
                    "datasetLicenseId": job.dataset_license_id,
                    "assets": [],
                    "resource": {},
                    "mappingSha256": "b" * 64,
                    "mapping": {},
                    "output": {
                        "datasetId": "kuka/collision-part1",
                        "datasetVersion": "1.0.0",
                    },
                    "validation": {},
                }
            )
            service.jobs.record_final_receipt(receipt)
            catalog = FakeCatalog()
            with TestClient(create_app(service, catalog=catalog)) as client:
                response = client.get(
                    f"/api/ingestions/{job.ingestion_id}/records?page=2&pageSize=1"
                )

            self.assertEqual(response.status_code, 200)
            self.assertEqual(
                catalog.request, ("kuka/collision-part1", "1.0.0", 2, 1)
            )
            self.assertEqual(response.json()["data"][0]["recordId"], "batch-01/run-01")
            self.assertEqual(response.json()["pagination"]["totalItems"], 206)
            service.close()


if __name__ == "__main__":
    unittest.main()

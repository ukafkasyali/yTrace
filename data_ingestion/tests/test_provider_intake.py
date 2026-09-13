from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

import httpx
import pyarrow as pa
import pyarrow.parquet as pq

from dataset_profiler.ingestion.archive import SafeArchiveExtractor
from dataset_profiler.ingestion.contracts import ApprovedManifest
from dataset_profiler.ingestion.inventory import ResourceInventory
from dataset_profiler.ingestion.jobs import IngestionState, ResourceFormat
from dataset_profiler.ingestion.providers import ProviderAcquirer
from dataset_profiler.ingestion.service import (
    CreateIngestion,
    IngestionService,
    ResolvedApprovedSource,
)
from dataset_profiler.ingestion.worker import AcquisitionWorker


class Resolver:
    def __init__(self, manifest: ApprovedManifest):
        self.manifest = manifest

    def resolve(self, approved_source_id: str) -> ResolvedApprovedSource:
        return ResolvedApprovedSource(
            approved_source_id=approved_source_id,
            manifest_sha256="a" * 64,
            manifest=self.manifest,
        )

    def close(self) -> None:
        pass


class ProviderIntakeTests(unittest.TestCase):
    def test_all_provider_fixtures_reach_bounded_inventory(self) -> None:
        csv_content = b"time,joint\n0.0,1.0\n0.001,2.0\n"
        sink = pa.BufferOutputStream()
        pq.write_table(pa.table({"time": [0.0, 0.001], "joint": [1.0, 2.0]}), sink)
        parquet_content = sink.getvalue().to_pybytes()
        cases = [
            self._zenodo(csv_content),
            self._github(csv_content),
            self._hugging_face(parquet_content),
        ]
        for index, (manifest, handler, expected_format) in enumerate(cases, start=1):
            with (
                self.subTest(provider=manifest.source_kind),
                tempfile.TemporaryDirectory() as temporary,
            ):
                root = Path(temporary)
                resolver = Resolver(manifest)
                service = IngestionService(root, resolver)
                source_id = f"src_{index:024x}"
                job, _ = service.create(CreateIngestion(approved_source_id=source_id))
                acquirer = ProviderAcquirer(
                    root / "cache",
                    client=httpx.Client(transport=httpx.MockTransport(handler)),
                    validate_dns=False,
                )
                worker = AcquisitionWorker(
                    service.jobs,
                    resolver,
                    acquirer,
                    extractor=SafeArchiveExtractor(root / "cache"),
                    inventory=ResourceInventory(),
                )

                completed = worker.run_once()

                self.assertEqual(completed.state, IngestionState.MAPPING)
                resources = service.jobs.list_resources(job.ingestion_id)
                self.assertEqual([item.format for item in resources], [expected_format])
                acquirer.close()
                service.close()

    @staticmethod
    def _base(kind: str, url: str, revision: str, asset: dict) -> ApprovedManifest:
        return ApprovedManifest.model_validate({
            "schemaVersion": "1.1",
            "runId": "11111111-1111-4111-8111-111111111111",
            "candidateId": "ds_0123456789ab",
            "name": f"{kind} fixture",
            "canonicalUrl": url,
            "revision": revision,
            "sourceKind": kind,
            "sourceRevision": revision,
            "assets": [asset],
            "licenseId": "cc-by-4.0",
            "datasetLicenseId": "cc-by-4.0",
            "codeLicenseId": None,
            "labels": [],
            "fileExtensions": [Path(asset["name"]).suffix],
            "totalSizeBytes": asset["sizeBytes"],
            "evidenceIds": [],
            "limitations": [],
            "approvedAt": "2026-09-13T10:00:00Z",
        })

    @classmethod
    def _zenodo(cls, content: bytes):
        asset = {
            "assetId": "asset_0123456789abcdef",
            "name": "signals.csv",
            "role": "DATA",
            "sizeBytes": len(content),
            "providerLocator": "zenodo:123:signals.csv",
            "downloadUrl": "https://zenodo.org/api/files/123/signals.csv",
            "sourceChecksum": {
                "algorithm": "md5",
                "value": hashlib.md5(content, usedforsecurity=False).hexdigest(),
            },
        }
        manifest = cls._base("ZENODO", "https://zenodo.org/records/123", "123.r1", asset)
        return manifest, lambda _: httpx.Response(200, content=content), ResourceFormat.CSV

    @classmethod
    def _github(cls, content: bytes):
        blob = hashlib.sha1(
            f"blob {len(content)}\0".encode() + content,
            usedforsecurity=False,
        ).hexdigest()
        revision = "b" * 40
        asset = {
            "assetId": "asset_0123456789abcdef",
            "name": "signals.csv",
            "role": "DATA",
            "sizeBytes": len(content),
            "providerLocator": f"github:org/repo:blob:{blob}",
            "downloadUrl": f"https://api.github.com/repos/org/repo/git/blobs/{blob}",
            "sourceChecksum": None,
        }
        manifest = cls._base("GITHUB", "https://github.com/org/repo", revision, asset)

        def handler(request: httpx.Request) -> httpx.Response:
            if "/contents/" in request.url.path:
                return httpx.Response(200, json={"type": "file", "sha": blob, "size": len(content)})
            return httpx.Response(200, content=content)

        return manifest, handler, ResourceFormat.CSV

    @classmethod
    def _hugging_face(cls, content: bytes):
        revision = "c" * 40
        asset = {
            "assetId": "asset_0123456789abcdef",
            "name": "signals.parquet",
            "role": "DATA",
            "sizeBytes": len(content),
            "providerLocator": f"huggingface:datasets/org/repo:{revision}:signals.parquet",
            "downloadUrl": f"https://huggingface.co/datasets/org/repo/resolve/{revision}/signals.parquet",
            "sourceChecksum": {"algorithm": "sha256", "value": hashlib.sha256(content).hexdigest()},
        }
        manifest = cls._base(
            "HUGGING_FACE",
            "https://huggingface.co/datasets/org/repo",
            revision,
            asset,
        )
        return (
            manifest,
            lambda _: httpx.Response(200, content=content, headers={"X-Repo-Commit": revision}),
            ResourceFormat.PARQUET,
        )


if __name__ == "__main__":
    unittest.main()

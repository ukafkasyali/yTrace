import hashlib
import tempfile
import unittest
from pathlib import Path

import httpx

from dataset_profiler.ingestion import (
    AcquisitionError,
    ProviderAcquirer,
    parse_manifest,
)


def github_manifest(content: bytes) -> dict:
    blob = hashlib.sha1(
        f"blob {len(content)}\0".encode() + content,
        usedforsecurity=False,
    ).hexdigest()
    commit = "a" * 40
    return {
        "schemaVersion": "1.1",
        "runId": "11111111-1111-4111-8111-111111111111",
        "candidateId": "ds_0123456789ab",
        "name": "GitHub signals",
        "canonicalUrl": "https://github.com/org/signals",
        "revision": f"GITHUB:{commit}",
        "sourceKind": "GITHUB",
        "sourceRevision": commit,
        "assets": [
            {
                "assetId": "asset_0123456789abcdef",
                "name": "data/signals.csv",
                "role": "DATA",
                "sizeBytes": len(content),
                "providerLocator": f"github:org/signals:blob:{blob}",
                "downloadUrl": f"https://api.github.com/repos/org/signals/git/blobs/{blob}",
                "sourceChecksum": None,
            }
        ],
        "licenseId": "cc-by-4.0",
        "datasetLicenseId": "cc-by-4.0",
        "codeLicenseId": "MIT",
        "labels": [],
        "sampleRateHz": None,
        "fileExtensions": [".csv"],
        "totalSizeBytes": len(content),
        "evidenceIds": [],
        "limitations": [],
        "approvedAt": "2026-09-13T10:00:00Z",
    }


def hugging_face_manifest(content: bytes) -> dict:
    revision = "b" * 40
    digest = hashlib.sha256(content).hexdigest()
    return {
        "schemaVersion": "1.1",
        "runId": "11111111-1111-4111-8111-111111111111",
        "candidateId": "ds_0123456789ab",
        "name": "HF signals",
        "canonicalUrl": "https://huggingface.co/datasets/org/signals",
        "revision": f"HUGGING_FACE:{revision}",
        "sourceKind": "HUGGING_FACE",
        "sourceRevision": revision,
        "assets": [
            {
                "assetId": "asset_0123456789abcdef",
                "name": "data/train.parquet",
                "role": "DATA",
                "sizeBytes": len(content),
                "providerLocator": (
                    f"huggingface:datasets/org/signals:{revision}:data/train.parquet"
                ),
                "downloadUrl": (
                    "https://huggingface.co/datasets/org/signals/resolve/"
                    f"{revision}/data/train.parquet"
                ),
                "sourceChecksum": {"algorithm": "sha256", "value": digest},
            }
        ],
        "licenseId": "cc-by-4.0",
        "datasetLicenseId": "cc-by-4.0",
        "codeLicenseId": None,
        "labels": [],
        "sampleRateHz": None,
        "fileExtensions": [".parquet"],
        "totalSizeBytes": len(content),
        "evidenceIds": [],
        "limitations": [],
        "approvedAt": "2026-09-13T10:00:00Z",
    }


class ProviderAcquirerTests(unittest.TestCase):
    def test_github_commit_metadata_and_blob_bytes_are_verified(self) -> None:
        content = b"time,joint\n0,1\n"
        manifest = parse_manifest(github_manifest(content))
        asset = manifest.data_assets[0]
        blob = asset.provider_locator.rsplit(":", 1)[1]

        def handler(request: httpx.Request) -> httpx.Response:
            if "/contents/" in request.url.path:
                self.assertEqual(request.url.params["ref"], manifest.source_revision)
                return httpx.Response(
                    200,
                    json={"type": "file", "sha": blob, "size": len(content)},
                )
            return httpx.Response(200, content=content)

        with tempfile.TemporaryDirectory() as temporary:
            acquirer = ProviderAcquirer(
                Path(temporary),
                client=httpx.Client(transport=httpx.MockTransport(handler)),
                validate_dns=False,
            )
            result = acquirer.acquire(manifest, asset)

            self.assertEqual(result.content_sha256, hashlib.sha256(content).hexdigest())
            self.assertEqual(result.content_path.read_bytes(), content)
            acquirer.close()

    def test_github_rejects_blob_not_reachable_from_approved_commit(self) -> None:
        content = b"time,joint\n0,1\n"
        manifest = parse_manifest(github_manifest(content))
        asset = manifest.data_assets[0]
        client = httpx.Client(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(
                    200,
                    json={"type": "file", "sha": "0" * 40, "size": len(content)},
                )
            )
        )
        with tempfile.TemporaryDirectory() as temporary:
            acquirer = ProviderAcquirer(Path(temporary), client=client, validate_dns=False)
            with self.assertRaisesRegex(AcquisitionError, "does not contain"):
                acquirer.acquire(manifest, asset)
            acquirer.close()

    def test_hugging_face_revision_lfs_identity_and_pointer_are_verified(self) -> None:
        content = b"PAR1 synthetic parquet bytes"
        manifest = parse_manifest(hugging_face_manifest(content))
        asset = manifest.data_assets[0]

        def success(_: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                content=content,
                headers={"X-Repo-Commit": manifest.source_revision},
            )

        with tempfile.TemporaryDirectory() as temporary:
            acquirer = ProviderAcquirer(
                Path(temporary),
                client=httpx.Client(transport=httpx.MockTransport(success)),
                validate_dns=False,
            )
            result = acquirer.acquire(manifest, asset)
            self.assertEqual(result.content_sha256, hashlib.sha256(content).hexdigest())
            acquirer.close()

        pointer = b"version https://git-lfs.github.com/spec/v1\n"
        pointer_payload = hugging_face_manifest(pointer)
        pointer_manifest = parse_manifest(pointer_payload)
        with tempfile.TemporaryDirectory() as temporary:
            acquirer = ProviderAcquirer(
                Path(temporary),
                client=httpx.Client(
                    transport=httpx.MockTransport(
                        lambda _: httpx.Response(
                            200,
                            content=pointer,
                            headers={"X-Repo-Commit": pointer_manifest.source_revision},
                        )
                    )
                ),
                validate_dns=False,
            )
            with self.assertRaisesRegex(AcquisitionError, "LFS pointer"):
                acquirer.acquire(pointer_manifest, pointer_manifest.data_assets[0])
            acquirer.close()

    def test_hugging_face_revision_drift_fails_closed(self) -> None:
        content = b"PAR1 synthetic parquet bytes"
        manifest = parse_manifest(hugging_face_manifest(content))
        with tempfile.TemporaryDirectory() as temporary:
            acquirer = ProviderAcquirer(
                Path(temporary),
                client=httpx.Client(
                    transport=httpx.MockTransport(
                        lambda _: httpx.Response(
                            200,
                            content=content,
                            headers={"X-Repo-Commit": "c" * 40},
                        )
                    )
                ),
                validate_dns=False,
            )
            with self.assertRaisesRegex(AcquisitionError, "another dataset revision"):
                acquirer.acquire(manifest, manifest.data_assets[0])
            acquirer.close()


if __name__ == "__main__":
    unittest.main()

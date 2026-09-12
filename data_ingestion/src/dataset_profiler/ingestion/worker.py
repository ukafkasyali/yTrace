from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from .acquisition import AcquiredAsset, AcquisitionError
from .archive import ArchiveError, SafeArchiveExtractor
from .contracts import ApprovedManifest, ManifestAsset
from .inventory import InventoryError, ResourceInventory
from .jobs import (
    AssetReceipt,
    IngestionJob,
    IngestionJobConflict,
    IngestionJobStore,
    IngestionState,
    ResourceFormat,
)
from .service import (
    ApprovedSourceResolutionError,
    ApprovedSourceResolver,
    ResolvedApprovedSource,
)


class AssetAcquirer(Protocol):
    def acquire(self, manifest: ApprovedManifest, asset: ManifestAsset) -> AcquiredAsset: ...

    def has_verified_content(self, content_sha256: str, size_bytes: int) -> bool: ...

    def verified_content_path(self, content_sha256: str) -> Path: ...


class AcquisitionWorker:
    def __init__(
        self,
        jobs: IngestionJobStore,
        resolver: ApprovedSourceResolver,
        acquirer: AssetAcquirer,
        extractor: SafeArchiveExtractor | None = None,
        inventory: ResourceInventory | None = None,
    ):
        self.jobs = jobs
        self.resolver = resolver
        self.acquirer = acquirer
        self.extractor = extractor
        self.inventory = inventory

    def recover_interrupted(self) -> int:
        return self.jobs.requeue_interrupted_acquisitions()

    def run_once(self) -> IngestionJob | None:
        job = self.jobs.claim_next_acquisition()
        if job is None:
            return None
        try:
            resolved = self.resolver.resolve(job.approved_source_id)
            self._verify_job_manifest(job, resolved)
            assets = {asset.asset_id: asset for asset in resolved.manifest.data_assets}
            if set(job.asset_ids) - assets.keys():
                raise AcquisitionError("Approved asset selection no longer matches the manifest")
            receipts = {
                receipt.asset_id: receipt for receipt in self.jobs.list_receipts(job.ingestion_id)
            }
            for asset_id in job.asset_ids:
                asset = assets[asset_id]
                existing = receipts.get(asset_id)
                if existing is not None:
                    self._verify_receipt_claims(existing, asset)
                    if self.acquirer.has_verified_content(
                        existing.content_sha256, existing.observed_size_bytes
                    ):
                        continue
                acquired = self.acquirer.acquire(resolved.manifest, asset)
                if acquired.asset_id != asset.asset_id or acquired.size_bytes != asset.size_bytes:
                    raise AcquisitionError("Acquirer returned content for another approved asset")
                self.jobs.record_receipt(self._receipt(job, asset, acquired))
            resource_paths: list[tuple[str, str, Path]] = []
            if self.extractor is not None:
                receipts = {
                    receipt.asset_id: receipt
                    for receipt in self.jobs.list_receipts(job.ingestion_id)
                }
                for asset_id in job.asset_ids:
                    asset = assets[asset_id]
                    if self._is_archive(asset.name):
                        receipt = receipts[asset_id]
                        extraction = self.extractor.extract(
                            self.acquirer.verified_content_path(receipt.content_sha256),
                            receipt.content_sha256,
                        )
                        root = (
                            self.extractor.extracted_dir
                            / receipt.content_sha256[:2]
                            / receipt.content_sha256
                        )
                        resource_paths.extend(
                            (asset_id, file.relative_path, root / file.relative_path)
                            for file in extraction.files
                        )
                    else:
                        receipt = receipts[asset_id]
                        resource_paths.append(
                            (
                                asset_id,
                                asset.name,
                                self.acquirer.verified_content_path(receipt.content_sha256),
                            )
                        )
            if self.inventory is not None:
                self.jobs.set_state(
                    job.ingestion_id,
                    IngestionState.INSPECTING,
                    "Inspecting verified dataset resources",
                )
                profiles = [
                    self.jobs.record_resource(
                        self.inventory.inspect(
                            ingestion_id=job.ingestion_id,
                            asset_id=asset_id,
                            logical_path=logical_path,
                            path=path,
                        )
                    )
                    for asset_id, logical_path, path in resource_paths
                ]
                supported = [
                    profile for profile in profiles if profile.format is not ResourceFormat.UNSUPPORTED
                ]
                if not supported:
                    return self.jobs.set_state(
                        job.ingestion_id,
                        IngestionState.UNSUPPORTED_FORMAT,
                        "No supported time-series resources were found",
                    )
                return self.jobs.set_state(
                    job.ingestion_id,
                    IngestionState.MAPPING,
                    f"Inventoried {len(profiles)} resource(s); mapping is required",
                )
            return self.jobs.set_state(
                job.ingestion_id,
                IngestionState.INSPECTING,
                f"Verified {len(job.asset_ids)} approved asset(s); queued for inventory",
            )
        except (
            AcquisitionError,
            ApprovedSourceResolutionError,
            ArchiveError,
            InventoryError,
            IngestionJobConflict,
        ):
            return self.jobs.set_state(
                job.ingestion_id,
                IngestionState.FAILED,
                "Acquisition failed because an approved asset could not be verified",
            )
        except Exception:
            self.jobs.set_state(
                job.ingestion_id,
                IngestionState.FAILED,
                "Acquisition failed because the worker encountered an internal error",
            )
            raise

    @staticmethod
    def _is_archive(name: str) -> bool:
        lower = name.casefold()
        return lower.endswith((".zip", ".tar", ".tar.gz", ".tar.zst"))

    @staticmethod
    def _verify_job_manifest(job: IngestionJob, resolved: ResolvedApprovedSource) -> None:
        if resolved.approved_source_id != job.approved_source_id:
            raise AcquisitionError("Approved source identity changed after job creation")
        if resolved.manifest_sha256 != job.manifest_sha256:
            raise AcquisitionError("Approved manifest changed after the job was created")

    @staticmethod
    def _verify_receipt_claims(receipt: AssetReceipt, asset: ManifestAsset) -> None:
        checksum = asset.source_checksum
        if (
            receipt.provider_locator != asset.provider_locator
            or receipt.expected_size_bytes != asset.size_bytes
            or receipt.source_checksum_algorithm
            != (checksum.algorithm.value if checksum else None)
            or receipt.source_checksum_value != (checksum.value if checksum else None)
        ):
            raise AcquisitionError("Persisted receipt does not match the approved asset")

    @staticmethod
    def _receipt(
        job: IngestionJob,
        asset: ManifestAsset,
        acquired: AcquiredAsset,
    ) -> AssetReceipt:
        checksum = asset.source_checksum
        return AssetReceipt(
            ingestion_id=job.ingestion_id,
            asset_id=asset.asset_id,
            provider_locator=asset.provider_locator,
            expected_size_bytes=asset.size_bytes,
            source_checksum_algorithm=checksum.algorithm.value if checksum else None,
            source_checksum_value=checksum.value if checksum else None,
            observed_size_bytes=acquired.size_bytes,
            content_sha256=acquired.content_sha256,
            content_key=(
                f"sha256/{acquired.content_sha256[:2]}/{acquired.content_sha256}"
            ),
            acquired_at=datetime.now(UTC),
        )

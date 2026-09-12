"""Approved-source handoff and idempotent ingestion jobs."""

from .acquisition import AcquiredAsset, AcquisitionError, ZenodoAcquirer
from .contracts import ApprovedManifest, ManifestContractError, parse_manifest
from .jobs import (
    AssetReceipt,
    IngestionJob,
    IngestionJobConflict,
    IngestionJobStore,
    IngestionState,
)
from .service import CreateIngestion, IngestionService

__all__ = [
    "AcquiredAsset",
    "AcquisitionError",
    "ApprovedManifest",
    "AssetReceipt",
    "CreateIngestion",
    "IngestionJob",
    "IngestionJobConflict",
    "IngestionJobStore",
    "IngestionService",
    "IngestionState",
    "ManifestContractError",
    "ZenodoAcquirer",
    "parse_manifest",
]

"""Approved-source handoff and idempotent ingestion jobs."""

from .acquisition import AcquiredAsset, AcquisitionError, ZenodoAcquirer
from .archive import ArchiveError, ExtractedFile, ExtractionResult, SafeArchiveExtractor
from .contracts import ApprovedManifest, ManifestContractError, parse_manifest
from .jobs import (
    AssetReceipt,
    IngestionJob,
    IngestionJobConflict,
    IngestionJobStore,
    IngestionState,
)
from .providers import ProviderAcquirer
from .service import CreateIngestion, IngestionService
from .worker import AcquisitionWorker

__all__ = [
    "AcquiredAsset",
    "AcquisitionError",
    "AcquisitionWorker",
    "ApprovedManifest",
    "ArchiveError",
    "AssetReceipt",
    "CreateIngestion",
    "ExtractedFile",
    "ExtractionResult",
    "IngestionJob",
    "IngestionJobConflict",
    "IngestionJobStore",
    "IngestionService",
    "IngestionState",
    "ManifestContractError",
    "ProviderAcquirer",
    "SafeArchiveExtractor",
    "ZenodoAcquirer",
    "parse_manifest",
]

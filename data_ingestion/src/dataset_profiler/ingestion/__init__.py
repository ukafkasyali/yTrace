"""Approved-source handoff and idempotent ingestion jobs."""

from .acquisition import AcquiredAsset, AcquisitionError, ZenodoAcquirer
from .archive import ArchiveError, ExtractedFile, ExtractionResult, SafeArchiveExtractor
from .contracts import ApprovedManifest, ManifestContractError, parse_manifest
from .inventory import InventoryError, ResourceInventory
from .jobs import (
    AssetReceipt,
    IngestionJob,
    IngestionJobConflict,
    IngestionJobStore,
    IngestionState,
    ResourceFormat,
    ResourceProfile,
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
    "InventoryError",
    "ManifestContractError",
    "ProviderAcquirer",
    "ResourceFormat",
    "ResourceInventory",
    "ResourceProfile",
    "SafeArchiveExtractor",
    "ZenodoAcquirer",
    "parse_manifest",
]

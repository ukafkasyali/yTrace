from __future__ import annotations

import json
import sqlite3
import threading
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


def _to_camel(value: str) -> str:
    head, *tail = value.split("_")
    return head + "".join(part.title() for part in tail)


class IngestionState(StrEnum):
    QUEUED = "queued"
    ACQUIRING = "acquiring"
    INSPECTING = "inspecting"
    MAPPING = "mapping"
    VALIDATING = "validating"
    UNSUPPORTED_FORMAT = "unsupported_format"
    NEEDS_INPUT = "needs_input"
    FAILED = "failed"
    READY = "ready"


class IngestionJob(BaseModel):
    model_config = ConfigDict(alias_generator=_to_camel, populate_by_name=True, extra="forbid")

    ingestion_id: str = Field(pattern=r"^[0-9a-f-]{36}$")
    approved_source_id: str = Field(pattern=r"^src_[a-f0-9]{24}$")
    manifest_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_url: str
    source_kind: str
    source_revision: str
    asset_ids: list[str]
    job_revision: int = Field(ge=1)
    state: IngestionState
    message: str
    created_at: datetime
    updated_at: datetime


class AssetReceipt(BaseModel):
    model_config = ConfigDict(alias_generator=_to_camel, populate_by_name=True, extra="forbid")

    ingestion_id: str = Field(pattern=r"^[0-9a-f-]{36}$")
    asset_id: str = Field(pattern=r"^asset_[a-f0-9]{16}$")
    provider_locator: str
    expected_size_bytes: int = Field(gt=0)
    source_checksum_algorithm: str | None = None
    source_checksum_value: str | None = None
    observed_size_bytes: int = Field(gt=0)
    content_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    content_key: str = Field(pattern=r"^sha256/[a-f0-9]{2}/[a-f0-9]{64}$")
    acquired_at: datetime


class ResourceFormat(StrEnum):
    CSV = "CSV"
    TSV = "TSV"
    PARQUET = "PARQUET"
    NPY = "NPY"
    NPZ = "NPZ"
    MAT = "MAT"
    HDF5 = "HDF5"
    UNSUPPORTED = "UNSUPPORTED"


class ResourceProfile(BaseModel):
    model_config = ConfigDict(alias_generator=_to_camel, populate_by_name=True, extra="forbid")

    resource_id: str = Field(pattern=r"^res_[a-f0-9]{24}$")
    ingestion_id: str = Field(pattern=r"^[0-9a-f-]{36}$")
    asset_id: str = Field(pattern=r"^asset_[a-f0-9]{16}$")
    logical_path: str = Field(min_length=1, max_length=1_024)
    size_bytes: int = Field(ge=0)
    content_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    format: ResourceFormat
    details: dict = Field(default_factory=dict)
    inspected_at: datetime


class IngestionJobConflict(ValueError):
    pass


class IngestionJobNotFound(LookupError):
    pass


class IngestionJobStore:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS ingestion_jobs (
                ingestion_id TEXT PRIMARY KEY,
                approved_source_id TEXT NOT NULL UNIQUE,
                manifest_sha256 TEXT NOT NULL,
                source_url TEXT NOT NULL,
                source_kind TEXT NOT NULL,
                source_revision TEXT NOT NULL,
                asset_ids_json TEXT NOT NULL,
                job_revision INTEGER NOT NULL DEFAULT 1,
                state TEXT NOT NULL,
                message TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS mapping_specs (
                ingestion_id TEXT PRIMARY KEY,
                mapping_sha256 TEXT NOT NULL,
                mapping_json TEXT NOT NULL,
                confirmed_at TEXT NOT NULL,
                FOREIGN KEY (ingestion_id) REFERENCES ingestion_jobs(ingestion_id)
            )
            """
        )
        columns = {
            row["name"] for row in self.connection.execute("PRAGMA table_info(ingestion_jobs)")
        }
        if "job_revision" not in columns:
            self.connection.execute(
                "ALTER TABLE ingestion_jobs ADD COLUMN job_revision INTEGER NOT NULL DEFAULT 1"
            )
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS resource_profiles (
                resource_id TEXT PRIMARY KEY,
                ingestion_id TEXT NOT NULL,
                asset_id TEXT NOT NULL,
                logical_path TEXT NOT NULL,
                size_bytes INTEGER NOT NULL,
                content_sha256 TEXT NOT NULL,
                format TEXT NOT NULL,
                details_json TEXT NOT NULL,
                inspected_at TEXT NOT NULL,
                UNIQUE (ingestion_id, asset_id, logical_path),
                FOREIGN KEY (ingestion_id, asset_id)
                    REFERENCES asset_receipts(ingestion_id, asset_id)
            )
            """
        )
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS asset_receipts (
                ingestion_id TEXT NOT NULL,
                asset_id TEXT NOT NULL,
                provider_locator TEXT NOT NULL,
                expected_size_bytes INTEGER NOT NULL,
                source_checksum_algorithm TEXT,
                source_checksum_value TEXT,
                observed_size_bytes INTEGER NOT NULL,
                content_sha256 TEXT NOT NULL,
                content_key TEXT NOT NULL,
                acquired_at TEXT NOT NULL,
                PRIMARY KEY (ingestion_id, asset_id),
                FOREIGN KEY (ingestion_id) REFERENCES ingestion_jobs(ingestion_id)
            )
            """
        )
        self._lock = threading.RLock()

    def close(self) -> None:
        self.connection.close()

    @staticmethod
    def _job(row: sqlite3.Row) -> IngestionJob:
        return IngestionJob(
            ingestion_id=row["ingestion_id"],
            approved_source_id=row["approved_source_id"],
            manifest_sha256=row["manifest_sha256"],
            source_url=row["source_url"],
            source_kind=row["source_kind"],
            source_revision=row["source_revision"],
            asset_ids=json.loads(row["asset_ids_json"]),
            job_revision=row["job_revision"],
            state=row["state"],
            message=row["message"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def find_by_source(self, approved_source_id: str) -> IngestionJob | None:
        row = self.connection.execute(
            "SELECT * FROM ingestion_jobs WHERE approved_source_id = ?",
            (approved_source_id,),
        ).fetchone()
        return None if row is None else self._job(row)

    def get(self, ingestion_id: str) -> IngestionJob:
        row = self.connection.execute(
            "SELECT * FROM ingestion_jobs WHERE ingestion_id = ?",
            (ingestion_id,),
        ).fetchone()
        if row is None:
            raise IngestionJobNotFound("Ingestion job not found")
        return self._job(row)

    @staticmethod
    def _receipt(row: sqlite3.Row) -> AssetReceipt:
        return AssetReceipt(
            ingestion_id=row["ingestion_id"],
            asset_id=row["asset_id"],
            provider_locator=row["provider_locator"],
            expected_size_bytes=row["expected_size_bytes"],
            source_checksum_algorithm=row["source_checksum_algorithm"],
            source_checksum_value=row["source_checksum_value"],
            observed_size_bytes=row["observed_size_bytes"],
            content_sha256=row["content_sha256"],
            content_key=row["content_key"],
            acquired_at=row["acquired_at"],
        )

    def list_receipts(self, ingestion_id: str) -> list[AssetReceipt]:
        self.get(ingestion_id)
        rows = self.connection.execute(
            "SELECT * FROM asset_receipts WHERE ingestion_id = ? ORDER BY asset_id",
            (ingestion_id,),
        ).fetchall()
        return [self._receipt(row) for row in rows]

    def record_receipt(self, receipt: AssetReceipt) -> AssetReceipt:
        values = (
            receipt.ingestion_id,
            receipt.asset_id,
            receipt.provider_locator,
            receipt.expected_size_bytes,
            receipt.source_checksum_algorithm,
            receipt.source_checksum_value,
            receipt.observed_size_bytes,
            receipt.content_sha256,
            receipt.content_key,
            receipt.acquired_at.isoformat(),
        )
        with self._lock:
            self.connection.execute("BEGIN IMMEDIATE")
            try:
                existing = self.connection.execute(
                    "SELECT * FROM asset_receipts WHERE ingestion_id = ? AND asset_id = ?",
                    (receipt.ingestion_id, receipt.asset_id),
                ).fetchone()
                if existing is not None:
                    persisted = self._receipt(existing)
                    persisted_values = persisted.model_dump(exclude={"acquired_at"})
                    receipt_values = receipt.model_dump(exclude={"acquired_at"})
                    if persisted_values != receipt_values:
                        raise IngestionJobConflict("Asset already has a different acquisition receipt")
                    self.connection.execute("COMMIT")
                    return persisted
                self.connection.execute(
                    "INSERT INTO asset_receipts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    values,
                )
                self.connection.execute("COMMIT")
                return receipt
            except Exception:
                self.connection.execute("ROLLBACK")
                raise

    @staticmethod
    def _resource(row: sqlite3.Row) -> ResourceProfile:
        return ResourceProfile(
            resource_id=row["resource_id"],
            ingestion_id=row["ingestion_id"],
            asset_id=row["asset_id"],
            logical_path=row["logical_path"],
            size_bytes=row["size_bytes"],
            content_sha256=row["content_sha256"],
            format=row["format"],
            details=json.loads(row["details_json"]),
            inspected_at=row["inspected_at"],
        )

    def list_resources(self, ingestion_id: str) -> list[ResourceProfile]:
        self.get(ingestion_id)
        rows = self.connection.execute(
            "SELECT * FROM resource_profiles WHERE ingestion_id = ? ORDER BY asset_id, logical_path",
            (ingestion_id,),
        ).fetchall()
        return [self._resource(row) for row in rows]

    def record_resource(self, profile: ResourceProfile) -> ResourceProfile:
        values = (
            profile.resource_id,
            profile.ingestion_id,
            profile.asset_id,
            profile.logical_path,
            profile.size_bytes,
            profile.content_sha256,
            profile.format.value,
            json.dumps(profile.details, sort_keys=True, separators=(",", ":")),
            profile.inspected_at.isoformat(),
        )
        with self._lock:
            self.connection.execute("BEGIN IMMEDIATE")
            try:
                existing = self.connection.execute(
                    "SELECT * FROM resource_profiles "
                    "WHERE ingestion_id = ? AND asset_id = ? AND logical_path = ?",
                    (profile.ingestion_id, profile.asset_id, profile.logical_path),
                ).fetchone()
                if existing is not None:
                    persisted = self._resource(existing)
                    if persisted.model_dump(exclude={"inspected_at"}) != profile.model_dump(
                        exclude={"inspected_at"}
                    ):
                        raise IngestionJobConflict("Resource already has a different profile")
                    self.connection.execute("COMMIT")
                    return persisted
                self.connection.execute(
                    "INSERT INTO resource_profiles VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    values,
                )
                self.connection.execute("COMMIT")
                return profile
            except Exception:
                self.connection.execute("ROLLBACK")
                raise

    def get_mapping_payload(self, ingestion_id: str) -> dict | None:
        self.get(ingestion_id)
        row = self.connection.execute(
            "SELECT mapping_json FROM mapping_specs WHERE ingestion_id = ?",
            (ingestion_id,),
        ).fetchone()
        return None if row is None else json.loads(row["mapping_json"])

    def confirm_mapping(
        self,
        *,
        ingestion_id: str,
        expected_job_revision: int,
        resource_id: str,
        resource_sha256: str,
        mapping_sha256: str,
        mapping_payload: dict,
    ) -> tuple[dict, bool]:
        canonical = json.dumps(mapping_payload, sort_keys=True, separators=(",", ":"))
        now = datetime.now(UTC).isoformat()
        with self._lock:
            self.connection.execute("BEGIN IMMEDIATE")
            try:
                job_row = self.connection.execute(
                    "SELECT * FROM ingestion_jobs WHERE ingestion_id = ?",
                    (ingestion_id,),
                ).fetchone()
                if job_row is None:
                    raise IngestionJobNotFound("Ingestion job not found")
                existing = self.connection.execute(
                    "SELECT * FROM mapping_specs WHERE ingestion_id = ?",
                    (ingestion_id,),
                ).fetchone()
                if existing is not None:
                    payload = json.loads(existing["mapping_json"])
                    if existing["mapping_sha256"] != mapping_sha256:
                        raise IngestionJobConflict("Ingestion already has another confirmed mapping")
                    self.connection.execute("COMMIT")
                    return payload, False
                if job_row["job_revision"] != expected_job_revision:
                    raise IngestionJobConflict("Mapping decision used a stale job revision")
                if job_row["state"] not in {
                    IngestionState.MAPPING.value,
                    IngestionState.NEEDS_INPUT.value,
                }:
                    raise IngestionJobConflict("Ingestion is not waiting for a mapping")
                resource = self.connection.execute(
                    "SELECT * FROM resource_profiles WHERE ingestion_id = ? AND resource_id = ?",
                    (ingestion_id, resource_id),
                ).fetchone()
                if resource is None or resource["content_sha256"] != resource_sha256:
                    raise IngestionJobConflict("Mapping does not match the current resource")
                self.connection.execute(
                    "INSERT INTO mapping_specs VALUES (?, ?, ?, ?)",
                    (ingestion_id, mapping_sha256, canonical, now),
                )
                self.connection.execute(
                    "UPDATE ingestion_jobs SET state = ?, message = ?, updated_at = ?, "
                    "job_revision = job_revision + 1 WHERE ingestion_id = ?",
                    (
                        IngestionState.VALIDATING.value,
                        "Confirmed mapping queued for deterministic import",
                        now,
                        ingestion_id,
                    ),
                )
                self.connection.execute("COMMIT")
                return mapping_payload, True
            except Exception:
                self.connection.execute("ROLLBACK")
                raise

    def requeue_interrupted_acquisitions(self) -> int:
        now = datetime.now(UTC).isoformat()
        with self._lock:
            cursor = self.connection.execute(
                    "UPDATE ingestion_jobs SET state = ?, message = ?, updated_at = ?, "
                    "job_revision = job_revision + 1 WHERE state = ?",
                (
                    IngestionState.QUEUED.value,
                    "Queued after interrupted acquisition",
                    now,
                    IngestionState.ACQUIRING.value,
                ),
            )
        return cursor.rowcount

    def claim_next_acquisition(self) -> IngestionJob | None:
        now = datetime.now(UTC).isoformat()
        with self._lock:
            self.connection.execute("BEGIN IMMEDIATE")
            try:
                row = self.connection.execute(
                    """
                    SELECT * FROM ingestion_jobs
                    WHERE state = ?
                    ORDER BY created_at, ingestion_id
                    LIMIT 1
                    """,
                    (IngestionState.QUEUED.value,),
                ).fetchone()
                if row is None:
                    self.connection.execute("COMMIT")
                    return None
                self.connection.execute(
                    "UPDATE ingestion_jobs SET state = ?, message = ?, updated_at = ?, "
                    "job_revision = job_revision + 1 "
                    "WHERE ingestion_id = ?",
                    (
                        IngestionState.ACQUIRING.value,
                        "Acquiring approved assets",
                        now,
                        row["ingestion_id"],
                    ),
                )
                self.connection.execute("COMMIT")
                return self.get(row["ingestion_id"])
            except Exception:
                self.connection.execute("ROLLBACK")
                raise

    def set_state(
        self,
        ingestion_id: str,
        state: IngestionState,
        message: str,
    ) -> IngestionJob:
        now = datetime.now(UTC).isoformat()
        with self._lock:
            cursor = self.connection.execute(
                "UPDATE ingestion_jobs SET state = ?, message = ?, updated_at = ?, "
                "job_revision = job_revision + 1 "
                "WHERE ingestion_id = ?",
                (state.value, message, now, ingestion_id),
            )
        if cursor.rowcount != 1:
            raise IngestionJobNotFound("Ingestion job not found")
        return self.get(ingestion_id)

    def get_or_create(
        self,
        *,
        approved_source_id: str,
        manifest_sha256: str,
        source_url: str,
        source_kind: str,
        source_revision: str,
        asset_ids: list[str],
        state: IngestionState,
        message: str,
    ) -> tuple[IngestionJob, bool]:
        normalized_asset_ids = sorted(set(asset_ids))
        now = datetime.now(UTC).isoformat()
        with self._lock:
            self.connection.execute("BEGIN IMMEDIATE")
            try:
                existing = self.connection.execute(
                    "SELECT * FROM ingestion_jobs WHERE approved_source_id = ?",
                    (approved_source_id,),
                ).fetchone()
                if existing is not None:
                    job = self._job(existing)
                    if job.asset_ids != normalized_asset_ids:
                        raise IngestionJobConflict(
                            "Approved source already has an ingestion with another asset selection"
                        )
                    self.connection.execute("COMMIT")
                    return job, False
                ingestion_id = str(uuid4())
                self.connection.execute(
                    """
                    INSERT INTO ingestion_jobs (
                        ingestion_id, approved_source_id, manifest_sha256, source_url,
                        source_kind, source_revision, asset_ids_json, job_revision,
                        state, message, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        ingestion_id,
                        approved_source_id,
                        manifest_sha256,
                        source_url,
                        source_kind,
                        source_revision,
                        json.dumps(normalized_asset_ids, separators=(",", ":")),
                        1,
                        state.value,
                        message,
                        now,
                        now,
                    ),
                )
                self.connection.execute("COMMIT")
                return self.get(ingestion_id), True
            except Exception:
                self.connection.execute("ROLLBACK")
                raise

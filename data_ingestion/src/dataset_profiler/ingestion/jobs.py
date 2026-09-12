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
    state: IngestionState
    message: str
    created_at: datetime
    updated_at: datetime


class IngestionJobConflict(ValueError):
    pass


class IngestionJobNotFound(LookupError):
    pass


class IngestionJobStore:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.connection.row_factory = sqlite3.Row
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
                state TEXT NOT NULL,
                message TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
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
                    "INSERT INTO ingestion_jobs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        ingestion_id,
                        approved_source_id,
                        manifest_sha256,
                        source_url,
                        source_kind,
                        source_revision,
                        json.dumps(normalized_asset_ids, separators=(",", ":")),
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

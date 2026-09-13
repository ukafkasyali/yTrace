from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID

from data_sourcing.models import (
    ApprovalEvent,
    ApprovedSourceDetail,
    ApprovedSourcePage,
    ApprovedSourceSummary,
    AssetRole,
    CreateSourcingRun,
    ExecutionMode,
    Pagination,
    RunStatus,
    SourceKind,
    SourcingManifest,
    SourcingRun,
)


class RunNotFound(LookupError):
    pass


class ArtifactUnavailable(LookupError):
    pass


class ApprovedSourceNotFound(LookupError):
    pass


class IdempotencyConflict(ValueError):
    pass


class InvalidIdempotencyKey(ValueError):
    pass


def _validated_run_id(run_id: str) -> str:
    try:
        parsed = UUID(run_id)
    except ValueError as exc:
        raise RunNotFound("Sourcing run not found") from exc
    if str(parsed) != run_id:
        raise RunNotFound("Sourcing run not found")
    return run_id


class ArtifactStore:
    def __init__(self, runs_dir: Path):
        self.runs_dir = runs_dir.resolve()
        self.runs_dir.mkdir(parents=True, exist_ok=True)

    def run_dir(self, run_id: str) -> Path:
        target = self.runs_dir / _validated_run_id(run_id)
        if target.parent != self.runs_dir:
            raise RunNotFound("Sourcing run not found")
        return target

    @staticmethod
    def _atomic_write(path: Path, content: str) -> None:
        temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        temporary.write_text(content, encoding="utf-8")
        temporary.replace(path)

    def persist(self, state: dict) -> SourcingRun:
        run_id = _validated_run_id(state["run_id"])
        directory = self.run_dir(run_id)
        directory.mkdir(parents=True, exist_ok=True)
        run_path = directory / "run.json"
        created_at = datetime.fromtimestamp(state["started_at"], tz=UTC)
        if run_path.exists():
            created_at = self.read_run(run_id).created_at
        run = SourcingRun(
            run_id=run_id,
            status=RunStatus(state["status"]),
            brief=state["brief"],
            constraints=state["constraints"],
            requirements=state.get("requirements", []),
            requirements_confirmed=state.get("requirements_confirmed", False),
            hypotheses=state.get("hypotheses", []),
            candidates=state.get("candidates", []),
            profiles=state.get("profiles", []),
            evidence=state.get("evidence", []),
            assessments=state.get("assessments", []),
            recommended_candidate_id=state.get("recommended_candidate_id"),
            approved_candidate_id=state.get("approved_candidate_id"),
            excluded_candidate_ids=state.get("excluded_candidate_ids", []),
            review_feedback=state.get("review_feedback", []),
            review_iterations_used=state.get("review_iterations_used", 0),
            feedback_allowed=state.get("feedback_allowed", False),
            refinement_outcomes=state.get("refinement_outcomes", []),
            family_queries_used=state.get("family_queries_used", 0),
            gap_queries_used=state.get("gap_queries_used", 0),
            tavily_credits_used=state.get("tavily_credits_used", 0),
            execution_mode=ExecutionMode(state.get("execution_mode", ExecutionMode.LIVE.value)),
            errors=state.get("errors", []),
            report_markdown=state.get("report_markdown", ""),
            manifest=state.get("manifest"),
            created_at=created_at,
            updated_at=datetime.now(UTC),
        )
        self._atomic_write(run_path, run.model_dump_json(by_alias=True, indent=2))
        evidence_content = "".join(
            f"{json.dumps(item, sort_keys=True, separators=(',', ':'))}\n"
            for item in state.get("evidence", [])
        )
        self._atomic_write(directory / "evidence.jsonl", evidence_content)
        self._atomic_write(directory / "report.md", run.report_markdown)
        if run.manifest:
            self._atomic_write(
                directory / "manifest.json",
                run.manifest.model_dump_json(by_alias=True, indent=2),
            )
        return run

    def read_run(self, run_id: str) -> SourcingRun:
        path = self.run_dir(run_id) / "run.json"
        if not path.is_file():
            raise RunNotFound("Sourcing run not found")
        return SourcingRun.model_validate_json(path.read_text(encoding="utf-8"))

    def read_report(self, run_id: str) -> str:
        path = self.run_dir(run_id) / "report.md"
        if not path.is_file():
            raise ArtifactUnavailable("Report is not available")
        return path.read_text(encoding="utf-8")

    def read_manifest(self, run_id: str) -> SourcingManifest:
        path = self.run_dir(run_id) / "manifest.json"
        if not path.is_file():
            raise ArtifactUnavailable("Manifest is only available after approval")
        return SourcingManifest.model_validate_json(path.read_text(encoding="utf-8"))

    def persist_additional_manifest(self, manifest: SourcingManifest) -> None:
        if not re.fullmatch(r"ds_[a-f0-9]{12}", manifest.candidate_id):
            raise ValueError("Manifest candidate ID is invalid")
        directory = self.run_dir(manifest.run_id) / "manifests"
        directory.mkdir(parents=True, exist_ok=True)
        self._atomic_write(
            directory / f"{manifest.candidate_id}.json",
            manifest.model_dump_json(by_alias=True, indent=2),
        )

    def manifests(self) -> list[SourcingManifest]:
        manifests: list[SourcingManifest] = []
        for directory in sorted(self.runs_dir.iterdir()):
            if not directory.is_dir():
                continue
            try:
                manifests.append(self.read_manifest(directory.name))
            except (ArtifactUnavailable, RunNotFound, OSError, ValueError):
                pass
            additional_directory = directory / "manifests"
            if not additional_directory.is_dir():
                continue
            for path in sorted(additional_directory.glob("ds_*.json")):
                try:
                    manifests.append(
                        SourcingManifest.model_validate_json(path.read_text(encoding="utf-8"))
                    )
                except (OSError, ValueError):
                    continue
        return manifests


def _canonical_manifest_json(manifest: SourcingManifest) -> str:
    return json.dumps(
        manifest.model_dump(mode="json", by_alias=True),
        sort_keys=True,
        separators=(",", ":"),
    )


def _manifest_identity(manifest: SourcingManifest) -> tuple[SourceKind, str, str]:
    canonical_url = str(manifest.canonical_url).rstrip("/")
    kind = manifest.source_kind
    if kind is None:
        host = (urlsplit(canonical_url).hostname or "").casefold()
        kind = {
            "github.com": SourceKind.GITHUB,
            "zenodo.org": SourceKind.ZENODO,
            "www.zenodo.org": SourceKind.ZENODO,
            "huggingface.co": SourceKind.HUGGING_FACE,
        }.get(host)
    if kind is None:
        raise ValueError("Approved manifest does not identify a supported source provider")
    revision = manifest.source_revision
    if revision is None and manifest.revision:
        prefix = f"{kind.value}:"
        revision = next(
            (
                value.removeprefix(prefix)
                for value in manifest.revision.split(";")
                if value.startswith(prefix)
            ),
            None,
        )
    if not revision:
        raise ValueError("Approved manifest does not contain an immutable source revision")
    return kind, canonical_url, revision


class ApprovedSourceStore:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS approved_sources (
                approved_source_id TEXT PRIMARY KEY,
                source_kind TEXT NOT NULL,
                canonical_url TEXT NOT NULL,
                source_revision TEXT NOT NULL,
                name TEXT NOT NULL,
                latest_manifest_json TEXT NOT NULL,
                latest_manifest_sha256 TEXT NOT NULL,
                created_at TEXT NOT NULL,
                latest_approved_at TEXT NOT NULL,
                deleted_at TEXT,
                UNIQUE(source_kind, canonical_url, source_revision)
            );
            CREATE TABLE IF NOT EXISTS approval_events (
                approved_source_id TEXT NOT NULL REFERENCES approved_sources(approved_source_id),
                sourcing_run_id TEXT NOT NULL,
                candidate_id TEXT NOT NULL,
                manifest_json TEXT NOT NULL,
                manifest_sha256 TEXT NOT NULL,
                approved_at TEXT NOT NULL,
                PRIMARY KEY(approved_source_id, sourcing_run_id)
            );
            CREATE INDEX IF NOT EXISTS ix_approval_events_sourcing_run_id
                ON approval_events(sourcing_run_id);
            """
        )
        columns = {
            row["name"]
            for row in self.connection.execute("PRAGMA table_info(approved_sources)").fetchall()
        }
        if "deleted_at" not in columns:
            self.connection.execute("ALTER TABLE approved_sources ADD COLUMN deleted_at TEXT")
        self._lock = threading.RLock()

    def close(self) -> None:
        self.connection.close()

    def record_approval(
        self,
        manifest: SourcingManifest,
        *,
        restore_deleted: bool = True,
    ) -> str:
        kind, canonical_url, revision = _manifest_identity(manifest)
        identity = "|".join((kind.value, canonical_url, revision))
        approved_source_id = f"src_{hashlib.sha256(identity.encode()).hexdigest()[:24]}"
        manifest_json = _canonical_manifest_json(manifest)
        manifest_sha256 = hashlib.sha256(manifest_json.encode()).hexdigest()
        approved_at = manifest.approved_at.isoformat()
        with self._lock:
            self.connection.execute("BEGIN IMMEDIATE")
            try:
                self.connection.execute(
                    """
                    INSERT INTO approved_sources (
                        approved_source_id, source_kind, canonical_url, source_revision,
                        name, latest_manifest_json, latest_manifest_sha256, created_at,
                        latest_approved_at, deleted_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
                    ON CONFLICT(approved_source_id) DO UPDATE SET
                        name = excluded.name,
                        latest_manifest_json = excluded.latest_manifest_json,
                        latest_manifest_sha256 = excluded.latest_manifest_sha256,
                        latest_approved_at = excluded.latest_approved_at
                    WHERE excluded.latest_approved_at > approved_sources.latest_approved_at
                    """,
                    (
                        approved_source_id,
                        kind.value,
                        canonical_url,
                        revision,
                        manifest.name,
                        manifest_json,
                        manifest_sha256,
                        approved_at,
                        approved_at,
                    ),
                )
                event_cursor = self.connection.execute(
                    """
                    INSERT OR IGNORE INTO approval_events VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        approved_source_id,
                        manifest.run_id,
                        manifest.candidate_id,
                        manifest_json,
                        manifest_sha256,
                        approved_at,
                    ),
                )
                if restore_deleted and event_cursor.rowcount:
                    self.connection.execute(
                        """
                        UPDATE approved_sources SET deleted_at = NULL
                        WHERE approved_source_id = ?
                        """,
                        (approved_source_id,),
                    )
                self.connection.execute("COMMIT")
            except Exception:
                self.connection.execute("ROLLBACK")
                raise
        return approved_source_id

    @staticmethod
    def _summary(row: sqlite3.Row) -> ApprovedSourceSummary:
        manifest = SourcingManifest.model_validate_json(row["latest_manifest_json"])
        return ApprovedSourceSummary(
            approved_source_id=row["approved_source_id"],
            name=row["name"],
            canonical_url=row["canonical_url"],
            source_kind=row["source_kind"],
            source_revision=row["source_revision"],
            license_id=manifest.license_id,
            dataset_license_id=manifest.dataset_license_id,
            code_license_id=manifest.code_license_id,
            labels=manifest.labels,
            file_extensions=manifest.file_extensions,
            total_size_bytes=manifest.total_size_bytes,
            is_acquisition_ready=(
                manifest.schema_version == "1.1"
                and any(asset.role is AssetRole.DATA for asset in manifest.assets)
            ),
            approval_count=row["approval_count"],
            latest_manifest_sha256=row["latest_manifest_sha256"],
            created_at=row["created_at"],
            latest_approved_at=row["latest_approved_at"],
        )

    def list(
        self,
        *,
        page: int,
        page_size: int,
        source_kind: SourceKind | None = None,
        query: str | None = None,
    ) -> ApprovedSourcePage:
        clauses: list[str] = ["deleted_at IS NULL"]
        parameters: list[object] = []
        if source_kind is not None:
            clauses.append("source_kind = ?")
            parameters.append(source_kind.value)
        if query:
            escaped = query.casefold().replace("\\", "\\\\").replace("%", "\\%")
            escaped = escaped.replace("_", "\\_")
            clauses.append(
                "(lower(name) LIKE ? ESCAPE '\\' OR lower(canonical_url) LIKE ? ESCAPE '\\')"
            )
            parameters.extend((f"%{escaped}%", f"%{escaped}%"))
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        total = self.connection.execute(
            f"SELECT count(*) FROM approved_sources {where}",  # noqa: S608 - fixed clauses only
            parameters,
        ).fetchone()[0]
        rows = self.connection.execute(
            f"""
            SELECT approved_sources.*, count(approval_events.sourcing_run_id) AS approval_count
            FROM approved_sources
            JOIN approval_events USING (approved_source_id)
            {where}
            GROUP BY approved_source_id
            ORDER BY latest_approved_at DESC, approved_source_id ASC
            LIMIT ? OFFSET ?
            """,  # noqa: S608 - fixed clauses only
            (*parameters, page_size, (page - 1) * page_size),
        ).fetchall()
        return ApprovedSourcePage(
            data=[self._summary(row) for row in rows],
            pagination=Pagination(
                page=page,
                page_size=page_size,
                total_items=total,
                total_pages=(total + page_size - 1) // page_size,
            ),
        )

    def get(self, approved_source_id: str) -> ApprovedSourceDetail:
        row = self.connection.execute(
            """
            SELECT approved_sources.*, count(approval_events.sourcing_run_id) AS approval_count
            FROM approved_sources
            JOIN approval_events USING (approved_source_id)
            WHERE approved_source_id = ?
              AND deleted_at IS NULL
            GROUP BY approved_source_id
            """,
            (approved_source_id,),
        ).fetchone()
        if row is None:
            raise ApprovedSourceNotFound("Approved source not found")
        events = self.connection.execute(
            """
            SELECT sourcing_run_id, candidate_id, manifest_sha256, approved_at
            FROM approval_events
            WHERE approved_source_id = ?
            ORDER BY approved_at DESC, sourcing_run_id ASC
            """,
            (approved_source_id,),
        ).fetchall()
        return ApprovedSourceDetail(
            **self._summary(row).model_dump(),
            approvals=[ApprovalEvent.model_validate(dict(event)) for event in events],
        )

    def read_manifest(self, approved_source_id: str) -> SourcingManifest:
        row = self.connection.execute(
            """
            SELECT latest_manifest_json FROM approved_sources
            WHERE approved_source_id = ? AND deleted_at IS NULL
            """,
            (approved_source_id,),
        ).fetchone()
        if row is None:
            raise ApprovedSourceNotFound("Approved source not found")
        return SourcingManifest.model_validate_json(row["latest_manifest_json"])

    def candidate_ids_for_run(self, sourcing_run_id: str) -> list[str]:
        rows = self.connection.execute(
            """
            SELECT candidate_id FROM approval_events
            WHERE sourcing_run_id = ?
            ORDER BY approved_at ASC, candidate_id ASC
            """,
            (sourcing_run_id,),
        ).fetchall()
        return [row["candidate_id"] for row in rows]

    def delete(self, approved_source_id: str) -> None:
        with self._lock:
            cursor = self.connection.execute(
                """
                UPDATE approved_sources
                SET deleted_at = ?
                WHERE approved_source_id = ? AND deleted_at IS NULL
                """,
                (datetime.now(UTC).isoformat(), approved_source_id),
            )
            if cursor.rowcount:
                return
            exists = self.connection.execute(
                "SELECT 1 FROM approved_sources WHERE approved_source_id = ?",
                (approved_source_id,),
            ).fetchone()
            if exists is None:
                raise ApprovedSourceNotFound("Approved source not found")


class IdempotencyStore:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS idempotency_keys (
                key TEXT PRIMARY KEY,
                request_hash TEXT NOT NULL,
                run_id TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        self._lock = threading.RLock()

    def close(self) -> None:
        self.connection.close()

    def claim(self, key: str, request: CreateSourcingRun, proposed_run_id: str) -> tuple[str, bool]:
        if not key or len(key) > 200 or any(ord(character) < 33 for character in key):
            raise InvalidIdempotencyKey("Idempotency-Key must contain 1–200 visible characters")
        canonical = json.dumps(
            request.model_dump(mode="json", by_alias=True),
            sort_keys=True,
            separators=(",", ":"),
        )
        request_hash = hashlib.sha256(canonical.encode()).hexdigest()
        with self._lock:
            return self._claim_locked(key, request_hash, proposed_run_id)

    def _claim_locked(
        self,
        key: str,
        request_hash: str,
        proposed_run_id: str,
    ) -> tuple[str, bool]:
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            existing = self.connection.execute(
                "SELECT request_hash, run_id FROM idempotency_keys WHERE key = ?",
                (key,),
            ).fetchone()
            if existing:
                if existing[0] != request_hash:
                    raise IdempotencyConflict(
                        "Idempotency-Key was already used with a different request"
                    )
                self.connection.execute("COMMIT")
                return str(existing[1]), False
            self.connection.execute(
                "INSERT INTO idempotency_keys VALUES (?, ?, ?, ?)",
                (key, request_hash, proposed_run_id, datetime.now(UTC).isoformat()),
            )
            self.connection.execute("COMMIT")
            return proposed_run_id, True
        except Exception:
            self.connection.execute("ROLLBACK")
            raise

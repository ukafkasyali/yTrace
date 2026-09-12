from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from data_sourcing.models import (
    CreateSourcingRun,
    ExecutionMode,
    RunStatus,
    SourcingManifest,
    SourcingRun,
)


class RunNotFound(LookupError):
    pass


class ArtifactUnavailable(LookupError):
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
            hypotheses=state.get("hypotheses", []),
            candidates=state.get("candidates", []),
            profiles=state.get("profiles", []),
            evidence=state.get("evidence", []),
            assessments=state.get("assessments", []),
            recommended_candidate_id=state.get("recommended_candidate_id"),
            approved_candidate_id=state.get("approved_candidate_id"),
            review_feedback=state.get("review_feedback", []),
            review_iterations_used=state.get("review_iterations_used", 0),
            refinement_outcomes=state.get("refinement_outcomes", []),
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

"""Stable, JSON-serializable models for dataset onboarding jobs."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
import hashlib
import hmac
from pathlib import Path
from typing import Any


class JobStage(StrEnum):
    """Externally visible stages in one onboarding workflow."""

    CREATED = "created"
    PROFILING = "profiling"
    SEMANTIC_ANALYSIS = "semantic_analysis"
    VALIDATING = "validating"
    REPAIRING = "repairing"
    NEEDS_HUMAN_RESOLUTION = "needs_human_resolution"
    CONNECTOR_READY = "connector_ready"
    CONNECTOR_IMPLEMENTATION = "connector_implementation"
    CONNECTOR_TESTING = "connector_testing"
    BUILDING = "building"
    LOADING = "loading"
    VERIFYING = "verifying"
    COMPLETED = "completed"
    FAILED = "failed"


class JobStatus(StrEnum):
    """High-level execution status, separate from the current stage."""

    PENDING = "pending"
    RUNNING = "running"
    NEEDS_HUMAN_RESOLUTION = "needs_human_resolution"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass(frozen=True)
class SourceDescriptor:
    """Minimal handoff contract for an acquired dataset source."""

    source_type: str
    dataset_id: str
    local_path: str | None = None
    source_url: str | None = None
    revision: str | None = None
    archive_sha256: str | None = None
    documentation_paths: tuple[str, ...] = ()
    provenance: dict[str, Any] = field(default_factory=dict)
    discovery_metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.source_type != "local_directory":
            raise ValueError(
                "only source_type='local_directory' is currently supported"
            )
        if not self.dataset_id.strip():
            raise ValueError("dataset_id must not be empty")
        if not self.local_path:
            raise ValueError("local_directory sources require local_path")

    def to_dict(self) -> dict[str, Any]:
        """Return the stable wire representation."""
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> SourceDescriptor:
        """Decode a source descriptor from its wire representation."""
        return cls(
            source_type=raw["source_type"],
            dataset_id=raw["dataset_id"],
            local_path=raw.get("local_path"),
            source_url=raw.get("source_url"),
            revision=raw.get("revision"),
            archive_sha256=raw.get("archive_sha256"),
            documentation_paths=tuple(raw.get("documentation_paths", ())),
            provenance=dict(raw.get("provenance", {})),
            discovery_metadata=dict(raw.get("discovery_metadata", {})),
        )


@dataclass(frozen=True)
class ArtifactRef:
    """Reference to one structured or diagnostic job artifact."""

    name: str
    path: str
    stage: str
    media_type: str = "application/json"
    sha256: str | None = None


@dataclass(frozen=True)
class JobBlocker:
    """Frontend-safe representation of an implementation-blocking ambiguity."""

    field_path: str
    semantic_status: str
    downstream_system: str
    downstream_requirement: str
    candidate: Any = None
    evidence_refs: tuple[str, ...] = ()
    remaining_uncertainty: str | None = None


@dataclass(frozen=True)
class JobFailure:
    """Structured failure information retained with partial artifacts."""

    stage: str
    error_type: str
    message: str
    diagnostic_artifacts: tuple[str, ...] = ()


@dataclass
class OnboardingJob:
    """Persisted state for one dataset onboarding request."""

    job_id: str
    source: SourceDescriptor
    workflow_id: str
    stage: JobStage = JobStage.CREATED
    status: JobStatus = JobStatus.PENDING
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    artifacts: dict[str, ArtifactRef] = field(default_factory=dict)
    blockers: list[JobBlocker] = field(default_factory=list)
    error: JobFailure | None = None
    result: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return the stable job wire representation."""
        return {
            "schema_version": "1.0",
            "job_id": self.job_id,
            "source": self.source.to_dict(),
            "workflow_id": self.workflow_id,
            "stage": self.stage.value,
            "status": self.status.value,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "artifacts": {key: asdict(value) for key, value in self.artifacts.items()},
            "blockers": [asdict(value) for value in self.blockers],
            "error": asdict(self.error) if self.error else None,
            "result": self.result,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> OnboardingJob:
        """Decode persisted job state."""
        if raw.get("schema_version") != "1.0":
            raise ValueError("unsupported onboarding job schema_version")
        return cls(
            job_id=raw["job_id"],
            source=SourceDescriptor.from_dict(raw["source"]),
            workflow_id=raw["workflow_id"],
            stage=JobStage(raw["stage"]),
            status=JobStatus(raw["status"]),
            created_at=raw["created_at"],
            updated_at=raw["updated_at"],
            artifacts={
                key: ArtifactRef(**value)
                for key, value in raw.get("artifacts", {}).items()
            },
            blockers=[JobBlocker(**value) for value in raw.get("blockers", ())],
            error=JobFailure(**raw["error"]) if raw.get("error") else None,
            result=raw.get("result"),
        )

    @property
    def id(self) -> str:
        """Return the public job identifier shorthand."""
        return self.job_id

    def touch(self) -> None:
        """Advance the update timestamp before persistence."""
        self.updated_at = datetime.now(UTC).isoformat()

    def artifact_path(self, job_dir: Path, name: str) -> Path:
        """Resolve and verify a registered artifact below this job's directory."""
        reference = self.artifacts[name]
        path = resolve_job_path(job_dir, reference.path)
        if not path.is_file():
            raise ValueError(f"artifact {name!r} is missing or is not a file")
        expected = reference.sha256
        if (
            not isinstance(expected, str)
            or len(expected) != 64
            or any(
                character not in "0123456789abcdef"
                for character in expected.casefold()
            )
        ):
            raise ValueError(f"artifact {name!r} has no valid stored SHA-256")
        actual = file_sha256(path)
        if not hmac.compare_digest(actual, expected.casefold()):
            raise ValueError(f"artifact {name!r} does not match its stored SHA-256")
        return path


def resolve_job_path(job_dir: Path, relative_path: str) -> Path:
    """Resolve one artifact path without allowing it to escape the job directory."""
    if not isinstance(relative_path, str) or not relative_path:
        raise ValueError("artifact path must be a non-empty relative path")
    candidate_path = Path(relative_path)
    if candidate_path.is_absolute():
        raise ValueError("artifact path must be relative to the job directory")
    root = job_dir.resolve()
    candidate = (root / candidate_path).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as error:
        raise ValueError("artifact path resolves outside the job directory") from error
    return candidate


def file_sha256(path: Path) -> str:
    """Hash an artifact without loading an unbounded file into memory."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

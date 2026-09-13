"""Filesystem persistence for onboarding jobs and structured artifacts."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from .models import ArtifactRef, JobStage, OnboardingJob


class JobStore:
    """Persist jobs atomically in predictable per-job directories."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def job_dir(self, job_id: str) -> Path:
        """Return a validated path for one job."""
        if not job_id or any(
            character not in "abcdefghijklmnopqrstuvwxyz0123456789-"
            for character in job_id
        ):
            raise ValueError("invalid job_id")
        return self.root / job_id

    def create(self, job: OnboardingJob) -> None:
        """Create the job directory and initial state without overwriting."""
        directory = self.job_dir(job.job_id)
        directory.mkdir(parents=False, exist_ok=False)
        for child in ("semantic", "connector", "build", "verification", "logs"):
            (directory / child).mkdir()
        self.write_json(directory / "source.json", job.source.to_dict())
        self.save(job)

    def save(self, job: OnboardingJob) -> None:
        """Atomically persist current job state."""
        job.touch()
        self.write_json(self.job_dir(job.job_id) / "job.json", job.to_dict())

    def load(self, job_id: str) -> OnboardingJob:
        """Load one persisted job."""
        raw = json.loads(
            (self.job_dir(job_id) / "job.json").read_text(encoding="utf-8")
        )
        if not isinstance(raw, dict):
            raise ValueError("job.json root must be an object")
        return OnboardingJob.from_dict(raw)

    def write_artifact(
        self,
        job: OnboardingJob,
        name: str,
        relative_path: str,
        value: dict[str, Any] | list[Any],
        stage: JobStage,
    ) -> Path:
        """Write and register one JSON artifact."""
        path = self.job_dir(job.job_id) / relative_path
        self.write_json(path, value)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        job.artifacts[name] = ArtifactRef(
            name, relative_path, stage.value, sha256=digest
        )
        self.save(job)
        return path

    @staticmethod
    def write_json(path: Path, value: Any) -> None:
        """Write JSON through a same-directory temporary file and atomic replace."""
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
        temporary.write_text(
            json.dumps(value, indent=2, allow_nan=False) + "\n", encoding="utf-8"
        )
        temporary.replace(path)

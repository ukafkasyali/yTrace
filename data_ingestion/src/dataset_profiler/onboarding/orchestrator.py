"""Persisted state machine for end-to-end dataset onboarding."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from ..semantic_spec import (
    DatasetSpec,
    ImplementationOverride,
    ImplementationOverrideArtifact,
    build_connector_handoff,
    semantic_spec_sha256,
)
from ..semantic_spec.implementation_handoff import HUMAN_CONFIRMATION
from .backend import OnboardingBackend, StageCommandError
from .models import (
    JobBlocker,
    JobFailure,
    JobStage,
    JobStatus,
    OnboardingJob,
    SourceDescriptor,
)
from .store import JobStore


class OnboardingOrchestrator:
    """Coordinate one workflow while persisting every externally visible transition."""

    def __init__(self, jobs_root: str | Path, backend: OnboardingBackend) -> None:
        self.store = JobStore(jobs_root)
        self.backend = backend

    def create_job(self, source: SourceDescriptor) -> OnboardingJob:
        """Create a pending onboarding job without starting expensive work."""
        job = OnboardingJob(
            job_id=f"job-{uuid4().hex}",
            source=source,
            workflow_id=self.backend.workflow_id,
        )
        self.store.create(job)
        return job

    def get_job(self, job_id: str) -> OnboardingJob:
        """Return current persisted state."""
        job = self.store.load(job_id)
        self._check_workflow(job)
        return job

    def run_job(self, job_id: str) -> OnboardingJob:
        """Run or safely resume until completion, failure, or human resolution."""
        job = self.get_job(job_id)
        if job.status in {
            JobStatus.COMPLETED,
            JobStatus.FAILED,
            JobStatus.NEEDS_HUMAN_RESOLUTION,
        }:
            return job
        job.status = JobStatus.RUNNING
        self.store.save(job)
        try:
            while job.status is JobStatus.RUNNING:
                self._step(job)
        except Exception as exc:
            self._fail(job, exc)
        return self.get_job(job_id)

    def continue_job(self, job_id: str) -> OnboardingJob:
        """Resume a paused job after all blocking resolutions have been approved."""
        job = self.get_job(job_id)
        if job.status is JobStatus.NEEDS_HUMAN_RESOLUTION:
            raise ValueError("job still has unresolved human blockers")
        return self.run_job(job_id)

    def resolve_blocker(
        self,
        job_id: str,
        *,
        field_path: str,
        value: Any,
        approved_by: str,
        rationale: str,
        source: str = HUMAN_CONFIRMATION,
    ) -> OnboardingJob:
        """Apply one implementation-only decision using the existing approval validator."""
        job = self.get_job(job_id)
        if job.status is not JobStatus.NEEDS_HUMAN_RESOLUTION:
            raise ValueError("job is not awaiting human resolution")
        blocker = next(
            (item for item in job.blockers if item.field_path == field_path), None
        )
        if blocker is None:
            raise ValueError(f"field {field_path!r} is not a current blocker")
        spec = DatasetSpec.read_json(self._artifact(job, "dataset_spec"))
        existing = self._override_artifact(job, spec)
        raw_id = json.dumps(
            [job.job_id, field_path, value, approved_by, rationale],
            sort_keys=True,
            separators=(",", ":"),
        )
        override = ImplementationOverride(
            override_id=f"override-{hashlib.sha256(raw_id.encode()).hexdigest()[:16]}",
            field_path=field_path,
            value=value,
            source=source,
            approved_by=approved_by,
            rationale=rationale,
            downstream_system=blocker.downstream_system,
            evidence_refs=blocker.evidence_refs,
        )
        replacements = tuple(
            item for item in existing.overrides if item.field_path != field_path
        )
        candidate = ImplementationOverrideArtifact(
            schema_version="1.0",
            dataset_id=existing.dataset_id,
            semantic_spec_sha256=existing.semantic_spec_sha256,
            requirements=existing.requirements,
            overrides=(*replacements, override),
            downstream_context=existing.downstream_context,
        )
        handoff = build_connector_handoff(spec, candidate)
        self.store.write_artifact(
            job,
            "implementation_overrides",
            "semantic/implementation_overrides.json",
            candidate.to_dict(),
            JobStage.NEEDS_HUMAN_RESOLUTION,
        )
        self.store.write_artifact(
            job,
            "connector_handoff",
            "semantic/connector_handoff.json",
            handoff.to_dict(),
            JobStage.NEEDS_HUMAN_RESOLUTION,
        )
        self._apply_handoff_state(job, handoff.to_dict())
        self.store.save(job)
        return self.get_job(job_id)

    def _step(self, job: OnboardingJob) -> None:
        if job.stage is JobStage.CREATED:
            self._transition(job, JobStage.PROFILING)
        elif job.stage is JobStage.PROFILING:
            self._profile(job)
        elif job.stage is JobStage.SEMANTIC_ANALYSIS:
            self._semantic_analysis(job)
        elif job.stage is JobStage.VALIDATING:
            self._validate(job)
        elif job.stage is JobStage.REPAIRING:
            self._repair(job)
        elif job.stage is JobStage.CONNECTOR_READY:
            self._transition(job, JobStage.CONNECTOR_IMPLEMENTATION)
        elif job.stage is JobStage.CONNECTOR_IMPLEMENTATION:
            self._downstream(
                job,
                "connector_implementation",
                "connector/implementation_result.json",
                self.backend.implement_connector,
            )
            self._transition(job, JobStage.CONNECTOR_TESTING)
        elif job.stage is JobStage.CONNECTOR_TESTING:
            self._downstream(
                job,
                "connector_test",
                "connector/test_result.json",
                self.backend.test_connector,
            )
            self._transition(job, JobStage.BUILDING)
        elif job.stage is JobStage.BUILDING:
            self._downstream(
                job, "build_result", "build/build_result.json", self.backend.build
            )
            self._transition(job, JobStage.LOADING)
        elif job.stage is JobStage.LOADING:
            self._downstream(
                job, "load_result", "build/load_result.json", self.backend.load
            )
            self._transition(job, JobStage.VERIFYING)
        elif job.stage is JobStage.VERIFYING:
            self._downstream(
                job,
                "verification_result",
                "verification/verification_result.json",
                self.backend.verify,
            )
            self._complete(job)
        else:
            raise RuntimeError(f"cannot execute job from stage {job.stage.value}")

    def _profile(self, job: OnboardingJob) -> None:
        if "dataset_profile" not in job.artifacts:
            self.store.write_artifact(
                job,
                "dataset_profile",
                "semantic/dataset_profile.json",
                self.backend.profile(job),
                JobStage.PROFILING,
            )
        self._transition(job, JobStage.SEMANTIC_ANALYSIS)

    def _semantic_analysis(self, job: OnboardingJob) -> None:
        if "semantic_candidate" not in job.artifacts:
            result = self.backend.semantic_analysis(
                job, self._artifact(job, "dataset_profile")
            )
            self.store.write_artifact(
                job,
                "semantic_candidate",
                "semantic/dataset_spec.json",
                result.spec,
                job.stage,
            )
            self.store.write_artifact(
                job,
                "semantic_trace",
                "semantic/semantic_trace.json",
                result.trace,
                job.stage,
            )
            self.store.write_artifact(
                job,
                "semantic_evidence",
                "semantic/evidence_ids.json",
                {"evidence_ids": list(result.evidence_ids), "reused": result.reused},
                job.stage,
            )
        self._transition(job, JobStage.VALIDATING)

    def _validate(self, job: OnboardingJob) -> None:
        evidence_ids = self._evidence_ids(job)
        validation = self.backend.validate(
            self._artifact(job, "dataset_profile"),
            self._artifact(job, "semantic_candidate"),
            evidence_ids,
        )
        self.store.write_artifact(
            job,
            "validation_report",
            "semantic/validation_report.json",
            validation,
            job.stage,
        )
        if validation.get("valid") is not True:
            self._transition(job, JobStage.REPAIRING)
            return
        job.artifacts["dataset_spec"] = job.artifacts["semantic_candidate"]
        self.store.save(job)
        self._build_handoff(job)

    def _repair(self, job: OnboardingJob) -> None:
        result = self.backend.repair(
            job,
            self._artifact(job, "dataset_profile"),
            self._artifact(job, "semantic_candidate"),
            self._evidence_ids(job),
        )
        self.store.write_artifact(
            job,
            "dataset_spec",
            "semantic/dataset_spec.final.json",
            result.spec,
            job.stage,
        )
        self.store.write_artifact(
            job,
            "validation_report",
            "semantic/validation_report.final.json",
            result.validation,
            job.stage,
        )
        self.store.write_artifact(
            job,
            "repair_summary",
            "semantic/repair_summary.json",
            result.summary,
            job.stage,
        )
        self.store.write_artifact(
            job,
            "repair_traces",
            "semantic/repair_traces.json",
            list(result.traces),
            job.stage,
        )
        self.store.write_artifact(
            job,
            "semantic_evidence",
            "semantic/evidence_ids.json",
            {"evidence_ids": list(result.evidence_ids), "reused": False},
            job.stage,
        )
        if result.validation.get("valid") is not True:
            raise SemanticValidationError(result.validation)
        self._build_handoff(job)

    def _build_handoff(self, job: OnboardingJob) -> None:
        spec = DatasetSpec.read_json(self._artifact(job, "dataset_spec"))
        artifact = self._override_artifact(job, spec)
        handoff = build_connector_handoff(spec, artifact)
        self.store.write_artifact(
            job,
            "implementation_overrides",
            "semantic/implementation_overrides.json",
            artifact.to_dict(),
            job.stage,
        )
        self.store.write_artifact(
            job,
            "connector_handoff",
            "semantic/connector_handoff.json",
            handoff.to_dict(),
            job.stage,
        )
        self._apply_handoff_state(job, handoff.to_dict())
        self.store.save(job)

    def _override_artifact(
        self, job: OnboardingJob, spec: DatasetSpec
    ) -> ImplementationOverrideArtifact:
        if "implementation_overrides" in job.artifacts:
            return ImplementationOverrideArtifact.read_json(
                self._artifact(job, "implementation_overrides")
            )
        return ImplementationOverrideArtifact(
            schema_version="1.0",
            dataset_id=spec.identity.dataset_id,
            semantic_spec_sha256=semantic_spec_sha256(spec),
            requirements=self.backend.requirements,
            overrides=(),
            downstream_context=self.backend.downstream_context,
        )

    def _apply_handoff_state(self, job: OnboardingJob, handoff: dict[str, Any]) -> None:
        job.blockers = [
            JobBlocker(
                field_path=item["field_path"],
                semantic_status=item["semantic_resolution"],
                downstream_system=item["downstream_system"],
                downstream_requirement=item["requirement"],
                candidate=item.get("best_supported_candidate"),
                evidence_refs=tuple(item.get("supporting_evidence_refs", ())),
                remaining_uncertainty=item.get("remaining_uncertainty"),
            )
            for item in handoff.get("blocking_unresolved_fields", ())
            if item["field_path"] not in handoff.get("implementation_view", {})
        ]
        if handoff.get("connector_ready") is True:
            job.blockers = []
            job.status = JobStatus.PENDING
            job.stage = JobStage.CONNECTOR_READY
        else:
            job.status = JobStatus.NEEDS_HUMAN_RESOLUTION
            job.stage = JobStage.NEEDS_HUMAN_RESOLUTION

    def _downstream(
        self, job: OnboardingJob, name: str, path: str, callback: Any
    ) -> None:
        if name in job.artifacts:
            return
        job_dir = self.store.job_dir(job.job_id)
        if job.stage is JobStage.CONNECTOR_IMPLEMENTATION:
            value = callback(job, self._artifact(job, "connector_handoff"), job_dir)
        else:
            value = callback(job, job_dir)
        if value.get("passed") is not True:
            raise RuntimeError(f"{job.stage.value} did not report passed=true")
        self.store.write_artifact(job, name, path, value, job.stage)

    def _complete(self, job: OnboardingJob) -> None:
        verification = json.loads(
            self._artifact(job, "verification_result").read_text(encoding="utf-8")
        )
        job.result = {
            "status": "completed",
            "dataset_id": job.source.dataset_id,
            "semantic_spec": job.artifacts["dataset_spec"].path,
            "connector_handoff": job.artifacts["connector_handoff"].path,
            "connector": job.artifacts["connector_implementation"].path,
            "build": job.artifacts["build_result"].path,
            "verification": {
                "passed": verification.get("passed") is True,
                "artifact": job.artifacts["verification_result"].path,
            },
        }
        job.status = JobStatus.COMPLETED
        job.stage = JobStage.COMPLETED
        self.store.save(job)

    def _fail(self, job: OnboardingJob, exc: Exception) -> None:
        failing_stage = job.stage.value
        diagnostics: list[str] = []
        if isinstance(exc, StageCommandError):
            path = self.store.write_artifact(
                job,
                f"{failing_stage}_failure_receipt",
                f"logs/{failing_stage}_failure.json",
                exc.receipt,
                job.stage,
            )
            diagnostics.append(str(path.relative_to(self.store.job_dir(job.job_id))))
        failure = JobFailure(
            stage=failing_stage,
            error_type=type(exc).__name__,
            message=str(exc),
            diagnostic_artifacts=tuple(diagnostics),
        )
        job.error = failure
        job.result = {
            "status": "failed",
            "stage": failing_stage,
            "error_type": failure.error_type,
            "message": failure.message,
            "diagnostic_artifacts": diagnostics,
        }
        job.status = JobStatus.FAILED
        job.stage = JobStage.FAILED
        self.store.save(job)

    def _transition(self, job: OnboardingJob, stage: JobStage) -> None:
        job.stage = stage
        job.status = JobStatus.RUNNING
        self.store.save(job)

    def _artifact(self, job: OnboardingJob, name: str) -> Path:
        return job.artifact_path(self.store.job_dir(job.job_id), name)

    def _evidence_ids(self, job: OnboardingJob) -> set[str]:
        raw = json.loads(
            self._artifact(job, "semantic_evidence").read_text(encoding="utf-8")
        )
        return set(raw.get("evidence_ids", ()))

    def _check_workflow(self, job: OnboardingJob) -> None:
        if job.workflow_id != self.backend.workflow_id:
            raise ValueError(
                f"job uses workflow {job.workflow_id!r}, not configured {self.backend.workflow_id!r}"
            )


class SemanticValidationError(RuntimeError):
    """Report an invalid best candidate after bounded repair."""

    def __init__(self, validation: dict[str, Any]) -> None:
        errors = validation.get("errors", ())
        super().__init__(
            f"semantic validation failed after repair with {len(errors)} issue(s)"
        )
        self.validation = validation


def create_onboarding_job(
    source: SourceDescriptor,
    *,
    jobs_root: str | Path,
    backend: OnboardingBackend,
) -> OnboardingJob:
    """Convenience entry point for creating a persisted job."""
    return OnboardingOrchestrator(jobs_root, backend).create_job(source)


def onboard_dataset(
    source: SourceDescriptor,
    *,
    jobs_root: str | Path,
    backend: OnboardingBackend,
) -> OnboardingJob:
    """Create and run one onboarding job until a terminal or human-pause state."""
    service = OnboardingOrchestrator(jobs_root, backend)
    job = service.create_job(source)
    return service.run_job(job.id)

"""Focused state-machine tests for persisted dataset onboarding jobs."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from dataset_profiler.onboarding import (
    JobStage,
    JobStatus,
    OnboardingOrchestrator,
    RepairResult,
    SemanticResult,
    SourceDescriptor,
)
from dataset_profiler.semantic_spec import (
    DownstreamRequirement,
    ImplementationHandoffError,
)


_SPEC = json.loads(
    (Path(__file__).with_name("fixtures") / "bosch_unresolved_spec.json").read_text(
        encoding="utf-8"
    )
)
_REQUIREMENT = DownstreamRequirement(
    field_path="signals[0].unit",
    downstream_system="TimeF TimeSeriesSpec.unit_value",
    requirement="TimeF requires a concrete Pint unit.",
    best_supported_candidate={
        "name": "milligravity",
        "symbol": "mg",
        "timef_unit": "milligravity",
    },
    remaining_uncertainty="The acquisition-to-HDF5 transformation is undocumented.",
)


class FakeBackend:
    """Deterministic backend that records which expensive boundaries were invoked."""

    workflow_id = "test-workflow-v1"
    requirements = (_REQUIREMENT,)
    downstream_context = {"dataset_id": "boschresearch/cnc-machining"}

    def __init__(
        self,
        *,
        validation_valid: bool = True,
        repair_valid: bool = True,
        fail_stage: str | None = None,
    ) -> None:
        self.validation_valid = validation_valid
        self.repair_valid = repair_valid
        self.fail_stage = fail_stage
        self.calls: list[str] = []

    def _call(self, name: str) -> dict[str, object]:
        self.calls.append(name)
        if self.fail_stage == name:
            raise RuntimeError(f"forced {name} failure")
        return {"passed": True, "stage": name}

    def profile(self, job):
        self._call("profiling")
        return {"dataset_id": job.source.dataset_id, "fixture": True}

    def semantic_analysis(self, job, profile_path):
        self._call("semantic_analysis")
        return SemanticResult(_SPEC, {"tool_calls": []}, ())

    def validate(self, profile_path, spec_path, evidence_ids):
        self._call("validating")
        return {
            "valid": self.validation_valid,
            "errors": []
            if self.validation_valid
            else [{"code": "INVALID", "path": "signals"}],
        }

    def repair(self, job, profile_path, spec_path, evidence_ids):
        self._call("repairing")
        validation = {
            "valid": self.repair_valid,
            "errors": [] if self.repair_valid else [{"code": "STILL_INVALID"}],
        }
        return RepairResult(_SPEC, validation, (), {"rounds": []}, ())

    def implement_connector(self, job, handoff_path, job_dir):
        return self._call("connector_implementation")

    def test_connector(self, job, job_dir):
        return self._call("connector_testing")

    def build(self, job, job_dir):
        return self._call("building")

    def load(self, job, job_dir):
        return self._call("loading")

    def verify(self, job, job_dir):
        return self._call("verifying")


def _source(tmp_path: Path) -> SourceDescriptor:
    source = tmp_path / "source"
    source.mkdir()
    return SourceDescriptor(
        source_type="local_directory",
        local_path=str(source),
        dataset_id="bosch-cnc",
        provenance={"acquired_by": "test"},
    )


def _resolve(service: OnboardingOrchestrator, job_id: str, **changes):
    values = {
        "field_path": "signals[0].unit",
        "value": {"name": "milligravity", "symbol": "mg", "timef_unit": "milligravity"},
        "approved_by": "engineer@example.test",
        "rationale": "Approved for implementation while semantic truth remains unresolved.",
    }
    values.update(changes)
    return service.resolve_blocker(job_id, **values)


def test_normal_state_path_pauses_then_completes(tmp_path):
    backend = FakeBackend()
    service = OnboardingOrchestrator(tmp_path / "jobs", backend)
    created = service.create_job(_source(tmp_path))

    paused = service.run_job(created.id)
    assert paused.status is JobStatus.NEEDS_HUMAN_RESOLUTION
    assert paused.stage is JobStage.NEEDS_HUMAN_RESOLUTION
    assert [item.field_path for item in paused.blockers] == ["signals[0].unit"]
    assert "connector_implementation" not in backend.calls

    ready = _resolve(service, created.id)
    assert ready.stage is JobStage.CONNECTOR_READY
    completed = service.continue_job(created.id)

    assert completed.status is JobStatus.COMPLETED
    assert completed.stage is JobStage.COMPLETED
    assert completed.result["verification"]["passed"] is True
    assert backend.calls == [
        "profiling",
        "semantic_analysis",
        "validating",
        "connector_implementation",
        "connector_testing",
        "building",
        "loading",
        "verifying",
    ]


def test_profiling_failure_records_exact_stage_and_diagnostics(tmp_path):
    service = OnboardingOrchestrator(
        tmp_path / "jobs", FakeBackend(fail_stage="profiling")
    )
    job = service.create_job(_source(tmp_path))

    failed = service.run_job(job.id)

    assert failed.status is JobStatus.FAILED
    assert failed.stage is JobStage.FAILED
    assert failed.error.stage == "profiling"
    assert failed.error.error_type == "RuntimeError"
    assert "forced profiling failure" in failed.error.message


def test_invalid_semantic_result_after_repair_fails_with_artifacts(tmp_path):
    backend = FakeBackend(validation_valid=False, repair_valid=False)
    service = OnboardingOrchestrator(tmp_path / "jobs", backend)
    job = service.create_job(_source(tmp_path))

    failed = service.run_job(job.id)

    assert failed.status is JobStatus.FAILED
    assert failed.error.stage == "repairing"
    assert failed.error.error_type == "SemanticValidationError"
    assert "validation_report" in failed.artifacts
    assert "repair_summary" in failed.artifacts


def test_handoff_rejects_requirement_evidence_absent_from_semantic_run(tmp_path):
    backend = FakeBackend()
    backend.requirements = (
        replace(_REQUIREMENT, supporting_evidence_refs=("ev_missing",)),
    )
    service = OnboardingOrchestrator(tmp_path / "jobs", backend)
    job = service.create_job(_source(tmp_path))

    failed = service.run_job(job.id)

    assert failed.status is JobStatus.FAILED
    assert failed.error.stage == "validating"
    assert failed.error.error_type == "ImplementationHandoffError"
    assert "evidence absent" in failed.error.message


def test_invalid_nonhuman_approval_does_not_unblock(tmp_path):
    service = OnboardingOrchestrator(tmp_path / "jobs", FakeBackend())
    job = service.create_job(_source(tmp_path))
    service.run_job(job.id)

    with pytest.raises(ImplementationHandoffError, match="human_confirmation"):
        _resolve(service, job.id, source="agent_recommendation")

    unchanged = service.get_job(job.id)
    assert unchanged.status is JobStatus.NEEDS_HUMAN_RESOLUTION
    assert unchanged.blockers


def test_connector_failure_preserves_prior_artifacts(tmp_path):
    backend = FakeBackend(fail_stage="building")
    service = OnboardingOrchestrator(tmp_path / "jobs", backend)
    job = service.create_job(_source(tmp_path))
    service.run_job(job.id)
    _resolve(service, job.id)

    failed = service.continue_job(job.id)

    assert failed.status is JobStatus.FAILED
    assert failed.error.stage == "building"
    assert "connector_test" in failed.artifacts
    assert "build_result" not in failed.artifacts
    assert "connector_handoff" in failed.artifacts


def test_restart_loads_and_resumes_same_job(tmp_path):
    jobs = tmp_path / "jobs"
    first = OnboardingOrchestrator(jobs, FakeBackend())
    job = first.create_job(_source(tmp_path))
    first.run_job(job.id)
    _resolve(first, job.id)

    restarted_backend = FakeBackend()
    restarted = OnboardingOrchestrator(jobs, restarted_backend)
    completed = restarted.continue_job(job.id)

    assert completed.status is JobStatus.COMPLETED
    assert restarted_backend.calls == [
        "connector_implementation",
        "connector_testing",
        "building",
        "loading",
        "verifying",
    ]
    assert (jobs / job.id / "job.json").is_file()
    assert (jobs / job.id / "source.json").is_file()

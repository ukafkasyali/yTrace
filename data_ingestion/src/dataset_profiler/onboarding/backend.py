"""Thin adapters over the existing semantic pipeline and TimeNet workflow."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import subprocess
from typing import Any, Protocol

from ..evidence import DocumentationSource, EvidenceSession
from ..profiler import profile_dataset
from ..semantic_agent import generate_dataset_spec
from ..semantic_agent.profile_io import read_dataset_profile
from ..semantic_agent.repair import run_repairs, trace_evidence_ids
from ..semantic_spec import DatasetSpec, DownstreamRequirement, validate_dataset_spec
from .models import OnboardingJob


@dataclass(frozen=True)
class SemanticResult:
    """One semantic-agent output plus the evidence identity needed by validation."""

    spec: dict[str, Any]
    trace: dict[str, Any]
    evidence_ids: tuple[str, ...]
    reused: bool = False


@dataclass(frozen=True)
class RepairResult:
    """Best validator-guided repair candidate and its audit trail."""

    spec: dict[str, Any]
    validation: dict[str, Any]
    evidence_ids: tuple[str, ...]
    summary: dict[str, Any]
    traces: tuple[dict[str, Any], ...]


class OnboardingBackend(Protocol):
    """Stage boundary implemented by local components or test fakes."""

    workflow_id: str
    requirements: tuple[DownstreamRequirement, ...]
    downstream_context: dict[str, Any] | None

    def profile(self, job: OnboardingJob) -> dict[str, Any]: ...

    def semantic_analysis(
        self, job: OnboardingJob, profile_path: Path
    ) -> SemanticResult: ...

    def validate(
        self, profile_path: Path, spec_path: Path, evidence_ids: set[str]
    ) -> dict[str, Any]: ...

    def repair(
        self,
        job: OnboardingJob,
        profile_path: Path,
        spec_path: Path,
        evidence_ids: set[str],
    ) -> RepairResult: ...

    def implement_connector(
        self, job: OnboardingJob, handoff_path: Path, job_dir: Path
    ) -> dict[str, Any]: ...

    def test_connector(self, job: OnboardingJob, job_dir: Path) -> dict[str, Any]: ...

    def build(self, job: OnboardingJob, job_dir: Path) -> dict[str, Any]: ...

    def load(self, job: OnboardingJob, job_dir: Path) -> dict[str, Any]: ...

    def verify(self, job: OnboardingJob, job_dir: Path) -> dict[str, Any]: ...


@dataclass(frozen=True)
class CommandSpec:
    """A configured process invocation whose receipt becomes a structured artifact."""

    argv: tuple[str, ...]
    cwd: str
    environment: dict[str, str] | None = None
    result_path: str | None = None


class TimeNetCommandAdapter:
    """Invoke native TimeNet stages without embedding connector semantics."""

    def __init__(
        self,
        *,
        implementation: CommandSpec | None,
        testing: CommandSpec,
        building: CommandSpec,
        loading: CommandSpec,
        verifying: CommandSpec,
    ) -> None:
        self.commands = {
            "connector_implementation": implementation,
            "connector_testing": testing,
            "building": building,
            "loading": loading,
            "verifying": verifying,
        }

    def run(
        self,
        stage: str,
        *,
        job: OnboardingJob,
        job_dir: Path,
        handoff_path: Path | None = None,
    ) -> dict[str, Any]:
        """Run one native stage and return a stable subprocess receipt."""
        command = self.commands[stage]
        if command is None:
            return {
                "passed": True,
                "reused_existing_implementation": True,
                "message": "connector implementation already exists in the configured TimeNet checkout",
            }
        replacements = {
            "job_id": job.job_id,
            "job_dir": str(job_dir),
            "source_path": str(Path(job.source.local_path or "").resolve()),
            "dataset_id": job.source.dataset_id,
            "handoff_path": str(handoff_path or ""),
        }
        argv = tuple(part.format_map(replacements) for part in command.argv)
        environment = os.environ.copy()
        environment.update(
            {
                key: value.format_map(replacements)
                for key, value in (command.environment or {}).items()
            }
        )
        completed = subprocess.run(
            argv,
            cwd=command.cwd,
            env=environment,
            text=True,
            capture_output=True,
            check=False,
        )
        receipt = {
            "passed": completed.returncode == 0,
            "argv": list(argv),
            "cwd": command.cwd,
            "returncode": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
        }
        if completed.returncode:
            raise StageCommandError(stage, receipt)
        if command.result_path:
            result_path = Path(command.result_path.format_map(replacements))
            structured = _read_object(result_path)
            receipt["result"] = structured
            receipt["passed"] = structured.get("passed") is True
            if not receipt["passed"]:
                raise StageCommandError(stage, receipt)
        return receipt


class StageCommandError(RuntimeError):
    """Retain a failed native command receipt for job diagnostics."""

    def __init__(self, stage: str, receipt: dict[str, Any]) -> None:
        super().__init__(f"{stage} command exited with status {receipt['returncode']}")
        self.stage = stage
        self.receipt = receipt


class LocalOnboardingBackend:
    """Compose existing semantic functions with a native TimeNet stage adapter."""

    def __init__(
        self,
        *,
        workflow_id: str,
        semantic_client: Any | None,
        requirements: tuple[DownstreamRequirement, ...],
        timenet: TimeNetCommandAdapter,
        downstream_context: dict[str, Any] | None = None,
        seed_profile: str | Path | None = None,
        seed_semantic_spec: str | Path | None = None,
        seed_semantic_trace: str | Path | None = None,
    ) -> None:
        self.workflow_id = workflow_id
        self.semantic_client = semantic_client
        self.requirements = requirements
        self.timenet = timenet
        self.downstream_context = downstream_context
        self.seed_profile = Path(seed_profile).resolve() if seed_profile else None
        self.seed_semantic_spec = (
            Path(seed_semantic_spec).resolve() if seed_semantic_spec else None
        )
        self.seed_semantic_trace = (
            Path(seed_semantic_trace).resolve() if seed_semantic_trace else None
        )

    def profile(self, job: OnboardingJob) -> dict[str, Any]:
        """Run the existing deterministic profiler or reuse an explicit frozen artifact."""
        if self.seed_profile:
            return _read_object(self.seed_profile)
        return profile_dataset(
            job.source.local_path or "", job.source.dataset_id
        ).to_dict()

    def semantic_analysis(
        self, job: OnboardingJob, profile_path: Path
    ) -> SemanticResult:
        """Run the existing bounded semantic agent or reuse an explicit frozen run."""
        if self.seed_semantic_spec:
            spec = _read_object(self.seed_semantic_spec)
            trace = (
                _read_object(self.seed_semantic_trace)
                if self.seed_semantic_trace
                else {
                    "reused_artifact": str(self.seed_semantic_spec),
                    "tool_calls": [],
                }
            )
            evidence_ids = _all_spec_evidence_ids(DatasetSpec.from_dict(spec))
            return SemanticResult(spec, trace, tuple(sorted(evidence_ids)), reused=True)
        if self.semantic_client is None:
            raise RuntimeError(
                "semantic_client is required when no frozen semantic artifact is configured"
            )
        profile = read_dataset_profile(profile_path)
        documentation = _documentation(job.source)
        run = generate_dataset_spec(
            profile,
            EvidenceSession(profile, documentation_sources=documentation),
            self.semantic_client,
        )
        return SemanticResult(
            run.spec.to_dict(),
            run.trace,
            tuple(sorted(trace_evidence_ids(run.trace))),
        )

    def validate(
        self, profile_path: Path, spec_path: Path, evidence_ids: set[str]
    ) -> dict[str, Any]:
        """Invoke the existing deterministic DatasetSpec validator."""
        return validate_dataset_spec(
            read_dataset_profile(profile_path),
            DatasetSpec.read_json(spec_path),
            evidence_ids=evidence_ids,
        ).to_dict()

    def repair(
        self,
        job: OnboardingJob,
        profile_path: Path,
        spec_path: Path,
        evidence_ids: set[str],
    ) -> RepairResult:
        """Invoke the existing bounded validator-guided repair loop."""
        if self.semantic_client is None:
            raise RuntimeError(
                "semantic_client is required to repair an invalid semantic artifact"
            )
        profile = read_dataset_profile(profile_path)
        rounds, spec, validation, selection = run_repairs(
            profile,
            _documentation(job.source),
            DatasetSpec.read_json(spec_path),
            self.semantic_client,
            evidence_ids=evidence_ids,
        )
        known = set(evidence_ids)
        traces = []
        summary_rounds = []
        for item in rounds:
            traces.append(item["trace"])
            known.update(trace_evidence_ids(item["trace"]))
            summary_rounds.append(
                {
                    key: value
                    for key, value in item.items()
                    if key not in {"trace", "spec", "validation"}
                }
            )
        return RepairResult(
            spec.to_dict(),
            validation.to_dict(),
            tuple(sorted(known)),
            {"selection": selection, "rounds": summary_rounds},
            tuple(traces),
        )

    def implement_connector(
        self, job: OnboardingJob, handoff_path: Path, job_dir: Path
    ) -> dict[str, Any]:
        """Delegate connector implementation to the configured TimeNet environment."""
        return self.timenet.run(
            "connector_implementation",
            job=job,
            job_dir=job_dir,
            handoff_path=handoff_path,
        )

    def test_connector(self, job: OnboardingJob, job_dir: Path) -> dict[str, Any]:
        """Run native connector tests."""
        return self.timenet.run("connector_testing", job=job, job_dir=job_dir)

    def build(self, job: OnboardingJob, job_dir: Path) -> dict[str, Any]:
        """Run timenet-build in the native repository environment."""
        return self.timenet.run("building", job=job, job_dir=job_dir)

    def load(self, job: OnboardingJob, job_dir: Path) -> dict[str, Any]:
        """Exercise TimeNet.load for the built registry."""
        return self.timenet.run("loading", job=job, job_dir=job_dir)

    def verify(self, job: OnboardingJob, job_dir: Path) -> dict[str, Any]:
        """Run the configured raw-to-TimeF fidelity verifier."""
        return self.timenet.run("verifying", job=job, job_dir=job_dir)


def _documentation(source: Any) -> list[DocumentationSource]:
    return [
        DocumentationSource(f"doc-{index + 1}", path, Path(path).name)
        for index, path in enumerate(source.documentation_paths)
    ]


def _read_object(path: Path | None) -> dict[str, Any]:
    if path is None:
        raise ValueError("artifact path is required")
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"expected a JSON object in {path}")
    return raw


def _all_spec_evidence_ids(spec: DatasetSpec) -> set[str]:
    """Collect frozen evidence IDs so a previously validated run remains reproducible."""
    raw = spec.to_dict()
    result: set[str] = set()

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                if key == "evidence" and isinstance(child, list):
                    result.update(item for item in child if isinstance(item, str))
                else:
                    walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(raw)
    return result


def file_sha256(path: str | Path) -> str:
    """Return the SHA-256 of a file used by a configured workflow."""
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

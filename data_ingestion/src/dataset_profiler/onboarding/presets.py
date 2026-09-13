"""Known-good workflow configurations built connector details from callers."""

from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path

from ..semantic_spec import ImplementationOverrideArtifact
from .backend import CommandSpec, LocalOnboardingBackend, TimeNetCommandAdapter
from .connector_agent import ConnectorAgentConfig, ConnectorCodingAgent


def bosch_reference_backend(
    timenet_repo: str | Path | None = None,
    artifact_root: str | Path | None = None,
) -> LocalOnboardingBackend:
    """Return the Bosch semantic-to-agent-written-TimeNet workflow configuration."""
    project_root = Path(__file__).resolve().parents[4]
    ingestion_root = project_root / "data_ingestion"
    configured_timenet = (
        Path(timenet_repo).expanduser()
        if timenet_repo
        else project_root.parent / "TimeNet"
    )
    timenet_root = configured_timenet.resolve()
    artifacts = (
        Path(artifact_root).expanduser().resolve()
        if artifact_root
        else ingestion_root / "outputs"
    )
    python = timenet_root / ".venv/bin/python"
    build = timenet_root / ".venv/bin/timenet-build"
    connector_relative = (
        "packages/timenet-connectors/src/timenet_connectors/datasets/"
        "boschresearch/cnc_machining"
    )
    lock = ingestion_root / "timenet.lock"
    pinned_revision = json.loads(lock.read_text(encoding="utf-8"))["commit"]
    for required in (python, build):
        if not required.exists():
            raise FileNotFoundError(
                f"Bosch workflow dependency does not exist: {required}"
            )
    helper = ingestion_root / "scripts/bosch_onboarding_stage.py"
    override_path = (
        artifacts / "bosch_cnc_connector_handoff/implementation_overrides.json"
    )
    approved = ImplementationOverrideArtifact.read_json(override_path)
    # These four implementation-candidate references came from a later human
    # evidence review, not the persisted semantic-agent trace. Keep the candidate
    # and uncertainty, but do not present unbound IDs as job evidence.
    requirements = tuple(
        replace(requirement, supporting_evidence_refs=())
        for requirement in approved.requirements
    )
    adapter = TimeNetCommandAdapter(
        implementation=None,
        testing=CommandSpec(
            argv=(
                str(python),
                "-m",
                "pytest",
                f"{{timenet_worktree}}/{connector_relative}/tests/test_connector.py",
                "-q",
            ),
            cwd="{timenet_worktree}",
            environment={
                "PYTHONPATH": (
                    "{timenet_worktree}/packages/timenet/src:"
                    "{timenet_worktree}/packages/timenet-connectors/src"
                )
            },
        ),
        building=CommandSpec(
            argv=(
                str(build),
                "--quiet",
                "build",
                "boschresearch/cnc-machining",
                "--out",
                "{job_dir}/build/registry",
                "--no-isolation",
            ),
            cwd="{timenet_worktree}",
            environment={
                "TIMENET_BOSCH_CNC_SOURCE_DIR": "{source_path}",
                "TIMENET_HOME": "{job_dir}/build/timenet-home",
                "PYTHONPATH": (
                    "{timenet_worktree}/packages/timenet/src:"
                    "{timenet_worktree}/packages/timenet-connectors/src"
                ),
            },
        ),
        loading=CommandSpec(
            argv=(
                str(python),
                str(helper),
                "load",
                "--registry",
                "{job_dir}/build/registry",
                "--output",
                "{job_dir}/build/timenet_load.json",
            ),
            cwd="{timenet_worktree}",
            environment={
                "PYTHONPATH": (
                    "{timenet_worktree}/packages/timenet/src:"
                    "{timenet_worktree}/packages/timenet-connectors/src"
                )
            },
            result_path="{job_dir}/build/timenet_load.json",
        ),
        verifying=CommandSpec(
            argv=(
                str(python),
                str(helper),
                "verify",
                "--source",
                "{source_path}",
                "--registry",
                "{job_dir}/build/registry",
                "--output",
                "{job_dir}/verification/raw_timef_comparison.json",
            ),
            cwd="{timenet_worktree}",
            environment={
                "PYTHONPATH": (
                    "{timenet_worktree}/packages/timenet/src:"
                    "{timenet_worktree}/packages/timenet-connectors/src"
                )
            },
            result_path="{job_dir}/verification/raw_timef_comparison.json",
        ),
    )
    return LocalOnboardingBackend(
        workflow_id="bosch-cnc-reference-v1",
        semantic_client=None,
        requirements=requirements,
        downstream_context=approved.downstream_context,
        timenet=adapter,
        connector_agent=ConnectorCodingAgent(
            ConnectorAgentConfig(
                timenet_repository=timenet_root,
                pinned_revision=pinned_revision,
                dataset_id=approved.downstream_context["dataset_id"],
                focused_test_command=(
                    "env",
                    (
                        "PYTHONPATH={timenet_worktree}/packages/timenet/src:"
                        "{timenet_worktree}/packages/timenet-connectors/src"
                    ),
                    str(python),
                    "-m",
                    "pytest",
                    f"{{timenet_worktree}}/{connector_relative}/tests/test_connector.py",
                    "-q",
                ),
            )
        ),
        seed_profile=artifacts / "bosch_cnc_profile.json",
        seed_semantic_spec=artifacts / "bosch_cnc_v02_final/final_spec.json",
        seed_semantic_trace=(
            artifacts / "bosch_cnc_v02_final/kuka_part1_agent_trace.json"
        ),
    )

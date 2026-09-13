"""Known-good workflow configurations built connector details from callers."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from ..semantic_spec import ImplementationOverrideArtifact
from .backend import CommandSpec, LocalOnboardingBackend, TimeNetCommandAdapter


def bosch_reference_backend(
    timenet_repo: str | Path | None = None,
) -> LocalOnboardingBackend:
    """Return the verified Bosch semantic-to-native-TimeNet workflow configuration."""
    project_root = Path(__file__).resolve().parents[4]
    ingestion_root = project_root / "data_ingestion"
    configured_timenet = (
        Path(timenet_repo).expanduser()
        if timenet_repo
        else project_root.parent / "TimeNet"
    )
    timenet_root = configured_timenet.resolve()
    python = timenet_root / ".venv/bin/python"
    build = timenet_root / ".venv/bin/timenet-build"
    connector_dir = (
        timenet_root / "packages/timenet-connectors/src/timenet_connectors/datasets/"
        "boschresearch/cnc_machining"
    )
    for required in (python, build, connector_dir / "connector.py"):
        if not required.exists():
            raise FileNotFoundError(
                f"Bosch workflow dependency does not exist: {required}"
            )
    helper = ingestion_root / "scripts/bosch_onboarding_stage.py"
    override_path = (
        ingestion_root
        / "outputs/bosch_cnc_connector_handoff/implementation_overrides.json"
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
        implementation=CommandSpec(
            argv=(
                str(python),
                str(helper),
                "implementation",
                "--handoff",
                "{handoff_path}",
                "--output",
                "{job_dir}/connector/native_connector_result.json",
            ),
            cwd=str(timenet_root),
            result_path="{job_dir}/connector/native_connector_result.json",
        ),
        testing=CommandSpec(
            argv=(
                str(python),
                "-m",
                "pytest",
                str(connector_dir / "tests/test_connector.py"),
                "-q",
            ),
            cwd=str(timenet_root),
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
            cwd=str(timenet_root),
            environment={"TIMENET_BOSCH_CNC_SOURCE_DIR": "{source_path}"},
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
            cwd=str(timenet_root),
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
            cwd=str(timenet_root),
            result_path="{job_dir}/verification/raw_timef_comparison.json",
        ),
    )
    return LocalOnboardingBackend(
        workflow_id="bosch-cnc-reference-v1",
        semantic_client=None,
        requirements=requirements,
        downstream_context=approved.downstream_context,
        timenet=adapter,
        seed_profile=ingestion_root / "outputs/bosch_cnc_profile.json",
        seed_semantic_spec=ingestion_root
        / "outputs/bosch_cnc_v02_final/final_spec.json",
        seed_semantic_trace=(
            ingestion_root / "outputs/bosch_cnc_v02_final/kuka_part1_agent_trace.json"
        ),
    )

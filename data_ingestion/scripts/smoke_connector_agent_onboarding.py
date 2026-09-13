"""Run the persisted Bosch onboarding flow through an agent-written connector."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from dataset_profiler.onboarding import (
    JobStage,
    JobStatus,
    OnboardingOrchestrator,
    SourceDescriptor,
    bosch_reference_backend,
)
from dataset_profiler.semantic_spec import ImplementationOverrideArtifact


def main() -> int:
    """Run one real-source smoke and print its persisted stage evidence."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--jobs-root", type=Path, required=True)
    parser.add_argument("--timenet-repo", type=Path, required=True)
    parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument("--job-id", default="job-bosch-agent-smoke")
    args = parser.parse_args()

    service = OnboardingOrchestrator(
        args.jobs_root,
        bosch_reference_backend(args.timenet_repo, args.artifact_root),
    )
    created = service.create_job(
        SourceDescriptor(
            source_type="local_directory",
            local_path=str(args.source.resolve()),
            dataset_id="bosch-cnc",
            source_url="https://github.com/boschresearch/CNC_Machining",
            revision="d60581d6a3ab6015dcc5488c3d76112bb8e1bcb1",
            provenance={"approval": "user-provided local source checkout"},
        ),
        job_id=args.job_id,
    )
    paused = service.run_job(created.job_id)
    if paused.status is not JobStatus.NEEDS_HUMAN_RESOLUTION:
        raise RuntimeError(
            f"expected human-resolution pause, got {paused.status.value}"
        )
    approved = ImplementationOverrideArtifact.read_json(
        args.artifact_root / "bosch_cnc_connector_handoff/implementation_overrides.json"
    )
    by_field = {item.field_path: item for item in approved.overrides}
    current = paused
    for blocker in paused.blockers:
        override = by_field[blocker.field_path]
        current = service.resolve_blocker(
            created.job_id,
            field_path=override.field_path,
            value=override.value,
            approved_by=override.approved_by,
            rationale=override.rationale,
            source=override.source,
        )
    if current.stage is not JobStage.CONNECTOR_READY:
        raise RuntimeError(f"expected CONNECTOR_READY, got {current.stage.value}")
    completed = service.continue_job(created.job_id)
    if completed.status is not JobStatus.COMPLETED:
        raise RuntimeError(json.dumps(completed.to_dict(), indent=2))
    print(
        json.dumps(
            {
                "source": created.source.to_dict(),
                "stages": [
                    JobStage.CREATED.value,
                    JobStage.PROFILING.value,
                    JobStage.SEMANTIC_ANALYSIS.value,
                    JobStage.VALIDATING.value,
                    JobStage.NEEDS_HUMAN_RESOLUTION.value,
                    JobStage.CONNECTOR_READY.value,
                    JobStage.CONNECTOR_IMPLEMENTATION.value,
                    JobStage.CONNECTOR_TESTING.value,
                    JobStage.BUILDING.value,
                    JobStage.LOADING.value,
                    JobStage.VERIFYING.value,
                    JobStage.COMPLETED.value,
                ],
                "job": completed.to_dict(),
                "job_directory": str(service.store.job_dir(created.job_id)),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

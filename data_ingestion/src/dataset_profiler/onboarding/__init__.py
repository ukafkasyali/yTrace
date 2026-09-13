"""Public job-oriented API for reusable dataset onboarding."""

from .backend import (
    CommandSpec,
    LocalOnboardingBackend,
    OnboardingBackend,
    RepairResult,
    SemanticResult,
    StageCommandError,
    TimeNetCommandAdapter,
)
from .models import (
    ArtifactRef,
    JobBlocker,
    JobFailure,
    JobStage,
    JobStatus,
    OnboardingJob,
    SourceDescriptor,
)
from .orchestrator import (
    OnboardingOrchestrator,
    SemanticValidationError,
    create_onboarding_job,
    onboard_dataset,
)
from .presets import bosch_reference_backend

__all__ = [
    "ArtifactRef",
    "CommandSpec",
    "JobBlocker",
    "JobFailure",
    "JobStage",
    "JobStatus",
    "LocalOnboardingBackend",
    "OnboardingBackend",
    "OnboardingJob",
    "OnboardingOrchestrator",
    "RepairResult",
    "SemanticResult",
    "SemanticValidationError",
    "SourceDescriptor",
    "StageCommandError",
    "TimeNetCommandAdapter",
    "create_onboarding_job",
    "onboard_dataset",
    "bosch_reference_backend",
]

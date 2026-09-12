from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator


def to_camel(value: str) -> str:
    head, *tail = value.split("_")
    return head + "".join(part.title() for part in tail)


class WireModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="forbid",
        str_strip_whitespace=True,
    )


class RunStatus(StrEnum):
    QUEUED = "QUEUED"
    PLANNING = "PLANNING"
    DISCOVERING = "DISCOVERING"
    VERIFYING = "VERIFYING"
    ASSESSING = "ASSESSING"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    NEEDS_INPUT = "NEEDS_INPUT"
    FAILED = "FAILED"


class RequirementPriority(StrEnum):
    MUST = "MUST"
    SHOULD = "SHOULD"


class RequirementCategory(StrEnum):
    DOMAIN = "DOMAIN"
    TASK_LABELS = "TASK_LABELS"
    SAMPLING_RATE = "SAMPLING_RATE"
    MODALITY = "MODALITY"
    LICENSE = "LICENSE"
    PROVENANCE = "PROVENANCE"
    SCHEMA = "SCHEMA"
    ACQUISITION = "ACQUISITION"
    OTHER = "OTHER"


class VerificationStatus(StrEnum):
    VERIFIED = "VERIFIED"
    MISSING = "MISSING"
    CONFLICTING = "CONFLICTING"
    UNVERIFIED = "UNVERIFIED"


class SourceKind(StrEnum):
    ZENODO = "ZENODO"
    GITHUB = "GITHUB"
    HUGGING_FACE = "HUGGING_FACE"


class SourceRole(StrEnum):
    DISCOVERY_LEAD = "DISCOVERY_LEAD"
    DATASET_ARTIFACT = "DATASET_ARTIFACT"


class HypothesisStatus(StrEnum):
    PLANNED = "PLANNED"
    SEARCHED = "SEARCHED"
    EXHAUSTED = "EXHAUSTED"


class CandidateTier(StrEnum):
    RECOMMEND = "RECOMMEND"
    SHORTLIST = "SHORTLIST"
    REJECT = "REJECT"


class ConfidenceLevel(StrEnum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class ExecutionMode(StrEnum):
    LIVE = "LIVE"
    CACHED = "CACHED"
    PARTIAL = "PARTIAL"


class ApprovalDecision(StrEnum):
    APPROVE = "APPROVE"
    REJECT = "REJECT"


class RefinementOutcomeStatus(StrEnum):
    RECOMMENDATION_CHANGED = "RECOMMENDATION_CHANGED"
    RECOMMENDATION_WITHHELD = "RECOMMENDATION_WITHHELD"
    EVIDENCE_EXPANDED = "EVIDENCE_EXPANDED"
    CANDIDATES_ADDED = "CANDIDATES_ADDED"
    NO_CHANGE = "NO_CHANGE"


class SourcingConstraints(WireModel):
    must_have: list[str] = Field(default_factory=list, max_length=20)
    preferred: list[str] = Field(default_factory=list, max_length=20)
    allowed_licenses: list[str] = Field(default_factory=list, max_length=20)
    max_download_bytes: int = Field(default=25_000_000_000, gt=0, le=1_000_000_000_000)


class RequirementDefinition(WireModel):
    id: str = Field(pattern=r"^req_[a-z0-9_]{1,48}$")
    label: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1, max_length=500)
    priority: RequirementPriority
    category: RequirementCategory
    expected_values: list[Annotated[str, Field(min_length=1, max_length=500)]] = Field(
        default_factory=list,
        max_length=30,
    )
    is_system_required: bool = False

    @model_validator(mode="after")
    def custom_requirement_has_text(self) -> RequirementDefinition:
        if self.category is RequirementCategory.OTHER and len(self.expected_values) != 1:
            raise ValueError("custom requirements must contain exactly one expected value")
        return self


class CreateSourcingRun(WireModel):
    brief: str = Field(min_length=20, max_length=8_000)
    constraints: SourcingConstraints = Field(default_factory=SourcingConstraints)
    requirements: list[RequirementDefinition] | None = Field(default=None, max_length=20)

    @model_validator(mode="after")
    def requirement_ids_are_unique(self) -> CreateSourcingRun:
        if self.requirements is not None:
            ids = [item.id for item in self.requirements]
            if len(ids) != len(set(ids)):
                raise ValueError("requirement IDs must be unique")
            fixed_categories = {
                "req_provenance": RequirementCategory.PROVENANCE,
                "req_time_series": RequirementCategory.MODALITY,
            }
            if any(
                item.id in fixed_categories and item.category is not fixed_categories[item.id]
                for item in self.requirements
            ):
                raise ValueError("system requirement IDs cannot be reassigned")
        return self


class RequirementPreviewRequest(WireModel):
    brief: str = Field(min_length=20, max_length=8_000)
    constraints: SourcingConstraints = Field(default_factory=SourcingConstraints)
    custom_requirements: list[
        Annotated[str, Field(min_length=3, max_length=500)]
    ] = Field(default_factory=list, max_length=10)


class ResearchRequirement(RequirementDefinition):
    status: VerificationStatus = VerificationStatus.UNVERIFIED
    evidence_ids: list[str] = Field(default_factory=list)


class RequirementsPreview(WireModel):
    requirements: list[RequirementDefinition]


class SearchHypothesis(WireModel):
    id: str = Field(pattern=r"^hyp_[a-z0-9_]{1,48}$")
    rationale: str = Field(min_length=1, max_length=500)
    query: str = Field(min_length=3, max_length=400)
    status: HypothesisStatus = HypothesisStatus.PLANNED
    is_gap_query: bool = False


class SearchResult(WireModel):
    title: str = Field(min_length=1, max_length=500)
    url: HttpUrl
    content: str = Field(default="", max_length=8_000)
    score: float = Field(default=0, ge=0, le=1)
    query: str = Field(default="", max_length=400)


class DatasetCandidate(WireModel):
    id: str = Field(pattern=r"^ds_[a-f0-9]{12}$")
    name: str = Field(min_length=1, max_length=500)
    canonical_url: HttpUrl
    source_kind: SourceKind
    description: str = Field(default="", max_length=8_000)
    revision: str | None = Field(default=None, max_length=200)
    related_urls: list[HttpUrl] = Field(default_factory=list, max_length=20)
    source_role: SourceRole = SourceRole.DISCOVERY_LEAD
    discovery_depth: int = Field(default=0, ge=0, le=2)
    discovered_from_candidate_id: str | None = None


class DatasetProfile(WireModel):
    candidate_id: str
    name: str
    canonical_url: HttpUrl
    source_kinds: list[SourceKind]
    revision: str | None = None
    license_id: str | None = None
    file_count: int | None = Field(default=None, ge=0)
    total_size_bytes: int | None = Field(default=None, ge=0)
    file_extensions: list[str] = Field(default_factory=list)
    domains: list[str] = Field(default_factory=list)
    labels: list[str] = Field(default_factory=list)
    sample_rate_hz: float | None = Field(default=None, gt=0)
    channel_count: int | None = Field(default=None, ge=1)
    is_dataset_artifact: bool = False
    dataset_identity_reason: str = Field(
        default="Dataset identity has not been verified",
        max_length=500,
    )
    has_time_series_files: bool = False
    schema_documented: bool = False
    acquisition_feasible: bool = False


class EvidenceRecord(WireModel):
    id: str = Field(pattern=r"^ev_[a-f0-9]{16}$")
    candidate_id: str
    requirement_id: str | None = None
    claim_key: str = Field(min_length=1, max_length=120)
    observed_value: str = Field(min_length=1, max_length=2_000)
    source_url: HttpUrl
    source_kind: SourceKind
    status: VerificationStatus
    precedence: int = Field(ge=0, le=100)
    retrieved_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    note: str | None = Field(default=None, max_length=1_000)


class GateResult(WireModel):
    gate: str = Field(min_length=1, max_length=80)
    passed: bool
    reason: str = Field(min_length=1, max_length=500)
    evidence_ids: list[str] = Field(default_factory=list)


class ScoreBreakdown(WireModel):
    task_fit: int = Field(ge=0, le=35)
    training_readiness: int = Field(ge=0, le=20)
    acquisition_integrity: int = Field(ge=0, le=15)
    provenance_documentation: int = Field(ge=0, le=10)
    integration_readiness: int = Field(ge=0, le=10)
    license_clarity: int = Field(ge=0, le=5)
    evidence_consistency: int = Field(ge=0, le=5)

    @property
    def total(self) -> int:
        return sum(self.model_dump().values())


class CandidateAssessment(WireModel):
    candidate_id: str
    gates: list[GateResult]
    score: ScoreBreakdown
    total_score: int = Field(ge=0, le=100)
    tier: CandidateTier
    evidence_confidence: ConfidenceLevel
    recommendation_confidence: ConfidenceLevel = ConfidenceLevel.LOW
    missing_requirement_ids: list[str] = Field(default_factory=list)
    conflicts: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def total_matches_breakdown(self) -> CandidateAssessment:
        if self.total_score != self.score.total:
            raise ValueError("total_score must equal the score breakdown")
        if any(not gate.passed for gate in self.gates) and self.tier is not CandidateTier.REJECT:
            raise ValueError("a candidate with a failed hard gate must be rejected")
        return self


class ApprovalRequest(WireModel):
    decision: ApprovalDecision
    candidate_id: str | None = None
    note: str | None = Field(default=None, max_length=1_000)

    @model_validator(mode="after")
    def approved_candidate_is_required(self) -> ApprovalRequest:
        if self.decision is ApprovalDecision.APPROVE and not self.candidate_id:
            raise ValueError("candidateId is required when approving")
        if self.decision is ApprovalDecision.REJECT and not self.note:
            raise ValueError("note is required when rejecting for refinement")
        return self


class RefinementOutcome(WireModel):
    iteration: int = Field(ge=1, le=2)
    feedback: str = Field(min_length=1, max_length=1_000)
    query: str = Field(min_length=3, max_length=400)
    outcome: RefinementOutcomeStatus
    rejected_candidate_id: str | None = None
    previous_recommended_candidate_id: str | None = None
    recommended_candidate_id: str | None = None
    new_candidate_ids: list[str] = Field(default_factory=list)
    new_evidence_ids: list[str] = Field(default_factory=list)


class SourcingManifest(WireModel):
    run_id: str
    candidate_id: str
    name: str
    canonical_url: HttpUrl
    revision: str | None = None
    license_id: str
    labels: list[str]
    sample_rate_hz: float | None = None
    file_extensions: list[str]
    total_size_bytes: int | None = None
    evidence_ids: list[str]
    limitations: list[str]
    approved_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class SourcingRun(WireModel):
    run_id: str = Field(pattern=r"^[0-9a-f-]{36}$")
    status: RunStatus
    brief: str
    constraints: SourcingConstraints
    requirements: list[ResearchRequirement] = Field(default_factory=list)
    requirements_confirmed: bool = False
    hypotheses: list[SearchHypothesis] = Field(default_factory=list)
    candidates: list[DatasetCandidate] = Field(default_factory=list)
    profiles: list[DatasetProfile] = Field(default_factory=list)
    evidence: list[EvidenceRecord] = Field(default_factory=list)
    assessments: list[CandidateAssessment] = Field(default_factory=list)
    recommended_candidate_id: str | None = None
    approved_candidate_id: str | None = None
    excluded_candidate_ids: list[str] = Field(default_factory=list, max_length=2)
    review_feedback: list[str] = Field(default_factory=list, max_length=2)
    review_iterations_used: int = Field(default=0, ge=0, le=2)
    refinement_outcomes: list[RefinementOutcome] = Field(default_factory=list, max_length=2)
    gap_queries_used: int = Field(default=0, ge=0, le=2)
    tavily_credits_used: int = Field(default=0, ge=0, le=12)
    execution_mode: ExecutionMode = ExecutionMode.LIVE
    errors: list[str] = Field(default_factory=list)
    report_markdown: str = ""
    manifest: SourcingManifest | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class RunAccepted(WireModel):
    run_id: str
    status: RunStatus
    status_url: str


class ErrorDetail(WireModel):
    code: str
    message: str
    retryable: bool = False
    details: Any | None = None


class ErrorEnvelope(WireModel):
    error: ErrorDetail


ApprovalOutcome = Literal["approved", "rejected"]

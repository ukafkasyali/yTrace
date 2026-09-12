from __future__ import annotations

from collections import defaultdict

from data_sourcing.models import (
    CandidateAssessment,
    CandidateTier,
    ConfidenceLevel,
    DatasetProfile,
    EvidenceRecord,
    GateResult,
    RequirementCategory,
    RequirementPriority,
    ResearchRequirement,
    ScoreBreakdown,
    VerificationStatus,
)


def _evidence_for(evidence: list[EvidenceRecord], candidate_id: str, claim: str) -> list[str]:
    return [
        item.id
        for item in evidence
        if item.candidate_id == candidate_id and item.claim_key == claim
    ]


def detect_conflicts(evidence: list[EvidenceRecord], candidate_id: str) -> list[str]:
    values: dict[str, set[str]] = defaultdict(set)
    for item in evidence:
        if item.candidate_id == candidate_id and item.status is VerificationStatus.VERIFIED:
            values[item.claim_key].add(item.observed_value.casefold())
    return sorted(claim for claim, observed in values.items() if len(observed) > 1)


def requirement_is_met(requirement: ResearchRequirement, profile: DatasetProfile) -> bool:
    expected = {value.casefold() for value in requirement.expected_values}
    if requirement.category is RequirementCategory.TASK_LABELS:
        return expected.issubset({label.casefold() for label in profile.labels})
    if requirement.category is RequirementCategory.SAMPLING_RATE:
        try:
            minimum = max(float(value) for value in requirement.expected_values)
        except (ValueError, TypeError):
            return profile.sample_rate_hz is not None
        return profile.sample_rate_hz is not None and profile.sample_rate_hz >= minimum
    if requirement.category is RequirementCategory.LICENSE:
        return bool(profile.license_id)
    if requirement.category is RequirementCategory.PROVENANCE:
        return bool(profile.revision and profile.source_kinds)
    if requirement.category is RequirementCategory.SCHEMA:
        return profile.schema_documented
    if requirement.category is RequirementCategory.ACQUISITION:
        return profile.acquisition_feasible
    if requirement.category is RequirementCategory.MODALITY:
        return profile.has_time_series_files
    return False


def assess_candidate(
    profile: DatasetProfile,
    requirements: list[ResearchRequirement],
    evidence: list[EvidenceRecord],
) -> CandidateAssessment:
    conflicts = detect_conflicts(evidence, profile.candidate_id)
    required = [item for item in requirements if item.priority is RequirementPriority.MUST]
    missing = [item.id for item in required if not requirement_is_met(item, profile)]
    task_requirements = [
        item for item in requirements if item.category is RequirementCategory.TASK_LABELS
    ]
    task_met = sum(requirement_is_met(item, profile) for item in task_requirements)
    task_ratio = task_met / max(1, len(task_requirements))

    license_gate = GateResult(
        gate="license",
        passed=bool(profile.license_id),
        reason="Explicit licence found" if profile.license_id else "Explicit licence missing",
        evidence_ids=_evidence_for(evidence, profile.candidate_id, "license"),
    )
    gates = [
        GateResult(
            gate="provenance",
            passed=bool(profile.revision and profile.source_kinds),
            reason="Canonical versioned source found"
            if profile.revision and profile.source_kinds
            else "Canonical version or provenance missing",
            evidence_ids=_evidence_for(evidence, profile.candidate_id, "revision"),
        ),
        license_gate,
        GateResult(
            gate="time_series_files",
            passed=profile.has_time_series_files,
            reason=(
                "Time-series files found"
                if profile.has_time_series_files
                else "No usable time-series files found"
            ),
            evidence_ids=_evidence_for(evidence, profile.candidate_id, "file_extensions"),
        ),
        GateResult(
            gate="task_labels",
            passed=not task_requirements or task_met == len(task_requirements),
            reason="Required task labels found"
            if not task_requirements or task_met == len(task_requirements)
            else "Required task labels are incomplete",
            evidence_ids=_evidence_for(evidence, profile.candidate_id, "labels"),
        ),
        GateResult(
            gate="schema",
            passed=profile.schema_documented,
            reason="Schema or channel documentation found"
            if profile.schema_documented
            else "Schema or channel documentation missing",
            evidence_ids=_evidence_for(evidence, profile.candidate_id, "schema"),
        ),
        GateResult(
            gate="acquisition",
            passed=profile.acquisition_feasible,
            reason="Acquisition is within the configured bound"
            if profile.acquisition_feasible
            else "Acquisition size is unknown or exceeds the configured bound",
            evidence_ids=_evidence_for(evidence, profile.candidate_id, "total_size_bytes"),
        ),
    ]

    extensions = {extension.casefold() for extension in profile.file_extensions}
    integration = (
        10
        if extensions & {".csv", ".json", ".parquet"}
        else 7
        if extensions
        & {
            ".mat",
            ".h5",
            ".hdf5",
            ".npy",
            ".npz",
        }
        else 4
        if extensions & {".zip", ".tar", ".gz"}
        else 0
    )
    score = ScoreBreakdown(
        task_fit=round(35 * task_ratio) if task_requirements else 35,
        training_readiness=(12 if profile.labels else 0) + (8 if profile.schema_documented else 0),
        acquisition_integrity=(8 if profile.has_time_series_files else 0)
        + (7 if profile.acquisition_feasible else 0),
        provenance_documentation=(6 if profile.source_kinds else 0)
        + (4 if profile.revision else 0),
        integration_readiness=integration,
        license_clarity=5 if profile.license_id else 0,
        evidence_consistency=5 if not conflicts else 3 if len(conflicts) == 1 else 0,
    )
    gates_pass = all(gate.passed for gate in gates) and not missing
    tier = (
        CandidateTier.REJECT
        if not gates_pass or score.total < 65
        else CandidateTier.RECOMMEND
        if score.total >= 80
        else CandidateTier.SHORTLIST
    )
    met_required = len(required) - len(missing)
    coverage = met_required / max(1, len(required))
    evidence_confidence = (
        ConfidenceLevel.HIGH
        if coverage == 1 and not conflicts
        else ConfidenceLevel.MEDIUM
        if coverage >= 0.8 and len(conflicts) <= 1
        else ConfidenceLevel.LOW
    )
    return CandidateAssessment(
        candidate_id=profile.candidate_id,
        gates=gates,
        score=score,
        total_score=score.total,
        tier=tier,
        evidence_confidence=evidence_confidence,
        missing_requirement_ids=missing,
        conflicts=conflicts,
    )


def apply_recommendation_confidence(
    assessments: list[CandidateAssessment],
) -> list[CandidateAssessment]:
    ranked = sorted(assessments, key=lambda item: item.total_score, reverse=True)
    if not ranked:
        return assessments
    margin = ranked[0].total_score - (ranked[1].total_score if len(ranked) > 1 else 0)
    top = ranked[0]
    if top.tier is CandidateTier.RECOMMEND:
        confidence = (
            ConfidenceLevel.HIGH
            if top.evidence_confidence is ConfidenceLevel.HIGH and margin >= 10
            else ConfidenceLevel.MEDIUM
        )
        ranked[0] = top.model_copy(update={"recommendation_confidence": confidence})
    by_id = {item.candidate_id: item for item in ranked}
    return [by_id[item.candidate_id] for item in assessments]

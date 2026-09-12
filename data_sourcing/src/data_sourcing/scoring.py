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
    scalar_claims = {
        "batch_count",
        "batch_count_part_i",
        "batch_count_part_ii",
        "channel_count",
        "license",
        "sample_rate_hz",
    }
    values: dict[str, set[str]] = defaultdict(set)
    for item in evidence:
        if (
            item.candidate_id == candidate_id
            and item.status is VerificationStatus.VERIFIED
            and item.claim_key in scalar_claims
        ):
            values[item.claim_key].add(item.observed_value.casefold())
    return sorted(claim for claim, observed in values.items() if len(observed) > 1)


def requirement_is_evidenced(
    requirement: ResearchRequirement,
    profile: DatasetProfile,
    evidence: list[EvidenceRecord],
) -> bool:
    claim = requirement_claim_key(requirement)
    return requirement_is_met(requirement, profile) and bool(
        claim and _evidence_for(evidence, profile.candidate_id, claim)
    )


def requirement_claim_key(requirement: ResearchRequirement) -> str | None:
    claim_by_category = {
        RequirementCategory.DOMAIN: "domains",
        RequirementCategory.TASK_LABELS: "labels",
        RequirementCategory.SAMPLING_RATE: "sample_rate_hz",
        RequirementCategory.MODALITY: "file_extensions",
        RequirementCategory.LICENSE: "license",
        RequirementCategory.PROVENANCE: "revision",
        RequirementCategory.SCHEMA: "schema",
        RequirementCategory.ACQUISITION: "total_size_bytes",
    }
    return claim_by_category.get(requirement.category)


def requirement_is_met(requirement: ResearchRequirement, profile: DatasetProfile) -> bool:
    expected = {value.casefold() for value in requirement.expected_values}
    if requirement.category is RequirementCategory.DOMAIN:
        return bool(expected) and expected.issubset(
            {domain.casefold() for domain in profile.domains}
        )
    if requirement.category is RequirementCategory.TASK_LABELS:
        return bool(expected) and expected.issubset({label.casefold() for label in profile.labels})
    if requirement.category is RequirementCategory.SAMPLING_RATE:
        try:
            minimum = max(float(value) for value in requirement.expected_values)
        except (ValueError, TypeError):
            return profile.sample_rate_hz is not None
        return profile.sample_rate_hz is not None and profile.sample_rate_hz >= minimum
    if requirement.category is RequirementCategory.LICENSE:
        return bool(profile.license_id) and (
            not expected or profile.license_id.casefold() in expected
        )
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
    missing = [
        item.id for item in required if not requirement_is_evidenced(item, profile, evidence)
    ]
    fit_requirements = [
        item
        for item in requirements
        if item.category in {RequirementCategory.DOMAIN, RequirementCategory.TASK_LABELS}
    ]
    fit_met = sum(requirement_is_evidenced(item, profile, evidence) for item in fit_requirements)
    task_ratio = fit_met / max(1, len(fit_requirements))
    domain_requirements = [
        item for item in requirements if item.category is RequirementCategory.DOMAIN
    ]
    label_requirements = [
        item for item in requirements if item.category is RequirementCategory.TASK_LABELS
    ]
    domain_passed = all(
        requirement_is_evidenced(item, profile, evidence) for item in domain_requirements
    )
    labels_passed = all(
        requirement_is_evidenced(item, profile, evidence) for item in label_requirements
    )

    license_requirements = [
        item for item in required if item.category is RequirementCategory.LICENSE
    ]
    licence_allowed = all(
        requirement_is_evidenced(item, profile, evidence) for item in license_requirements
    )
    revision_evidence = _evidence_for(evidence, profile.candidate_id, "revision")
    file_evidence = _evidence_for(evidence, profile.candidate_id, "file_extensions")
    label_evidence = _evidence_for(evidence, profile.candidate_id, "labels")
    domain_evidence = _evidence_for(evidence, profile.candidate_id, "domains")
    schema_evidence = _evidence_for(evidence, profile.candidate_id, "schema")
    size_evidence = _evidence_for(evidence, profile.candidate_id, "total_size_bytes")
    license_evidence = _evidence_for(evidence, profile.candidate_id, "license")
    provenance_passed = bool(profile.revision and profile.source_kinds and revision_evidence)
    files_passed = profile.has_time_series_files and bool(file_evidence)
    schema_passed = profile.schema_documented and bool(schema_evidence)
    acquisition_passed = profile.acquisition_feasible and bool(size_evidence)
    license_gate = GateResult(
        gate="license",
        passed=bool(profile.license_id) and licence_allowed,
        reason=(
            "Explicit allowed licence found"
            if profile.license_id and licence_allowed
            else "Licence is missing or outside the allowed set"
        ),
        evidence_ids=license_evidence,
    )
    gates = [
        GateResult(
            gate="provenance",
            passed=provenance_passed,
            reason="Canonical versioned source found"
            if provenance_passed
            else "Canonical version or provenance missing",
            evidence_ids=revision_evidence,
        ),
        license_gate,
        GateResult(
            gate="time_series_files",
            passed=files_passed,
            reason=(
                "Time-series files found" if files_passed else "No usable time-series files found"
            ),
            evidence_ids=file_evidence,
        ),
    ]
    if domain_requirements:
        gates.append(
            GateResult(
                gate="domain",
                passed=domain_passed,
                reason=(
                    "Native sources match the requested domain"
                    if domain_passed
                    else "Native sources do not match the requested domain"
                ),
                evidence_ids=domain_evidence,
            )
        )
    gates.extend(
        [
            GateResult(
                gate="task_labels",
                passed=labels_passed,
                reason="Required task labels found"
                if labels_passed
                else "Required task labels are incomplete",
                evidence_ids=label_evidence,
            ),
            GateResult(
                gate="schema",
                passed=schema_passed,
                reason="Schema or channel documentation found"
                if schema_passed
                else "Schema or channel documentation missing",
                evidence_ids=schema_evidence,
            ),
            GateResult(
                gate="acquisition",
                passed=acquisition_passed,
                reason="Acquisition is within the configured bound"
                if acquisition_passed
                else "Acquisition size is unknown or exceeds the configured bound",
                evidence_ids=size_evidence,
            ),
        ]
    )

    extensions = {extension.casefold() for extension in profile.file_extensions}
    integration = (
        10
        if file_evidence and extensions & {".csv", ".json", ".parquet"}
        else 7
        if file_evidence
        and extensions
        & {
            ".mat",
            ".h5",
            ".hdf5",
            ".npy",
            ".npz",
        }
        else 4
        if file_evidence and extensions & {".zip", ".tar", ".gz", ".zst", ".tar.zst"}
        else 0
    )
    score = ScoreBreakdown(
        task_fit=round(35 * task_ratio) if fit_requirements else 0,
        training_readiness=(12 if profile.labels and label_evidence else 0)
        + (8 if profile.schema_documented and schema_evidence else 0),
        acquisition_integrity=(8 if profile.has_time_series_files and file_evidence else 0)
        + (7 if profile.acquisition_feasible and size_evidence else 0),
        provenance_documentation=(
            10 if profile.source_kinds and profile.revision and revision_evidence else 0
        ),
        integration_readiness=integration,
        license_clarity=5 if profile.license_id and license_evidence else 0,
        evidence_consistency=(
            5
            if any(item.candidate_id == profile.candidate_id for item in evidence) and not conflicts
            else 3
            if len(conflicts) == 1
            else 0
        ),
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


def candidate_is_approvable(assessment: CandidateAssessment) -> bool:
    return (
        assessment.total_score >= 65
        and assessment.tier is not CandidateTier.REJECT
        and not assessment.missing_requirement_ids
        and all(gate.passed for gate in assessment.gates)
    )


def candidate_rank_key(assessment: CandidateAssessment) -> tuple[bool, int, int, str]:
    domain_gate = next((gate for gate in assessment.gates if gate.gate == "domain"), None)
    tier_rank = {
        CandidateTier.RECOMMEND: 0,
        CandidateTier.SHORTLIST: 1,
        CandidateTier.REJECT: 2,
    }[assessment.tier]
    return (
        domain_gate is not None and not domain_gate.passed,
        tier_rank,
        -assessment.total_score,
        assessment.candidate_id,
    )

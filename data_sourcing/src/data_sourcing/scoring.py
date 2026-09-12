from __future__ import annotations

from collections import defaultdict

from data_sourcing.models import (
    CandidateAssessment,
    ConfidenceLevel,
    DatasetProfile,
    EvidenceRecord,
    GateResult,
    RequirementCategory,
    RequirementPriority,
    ResearchRequirement,
    SourceKind,
    SuitabilityFactor,
    SuitabilityFactorKind,
    SuitabilityLevel,
    VerificationStatus,
)

_GATE_LABELS = {
    "dataset_identity": "Dataset identity",
    "provenance": "Canonical provenance",
    "time_series_files": "Usable time-series files",
    "license": "Explicit licence",
    "domain": "Equipment or application domain",
    "task_labels": "Task labels",
    "schema": "Schema documentation",
    "acquisition": "Acquisition feasibility",
}


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
    if requirement.category is RequirementCategory.OTHER:
        return bool(claim and _evidence_for(evidence, profile.candidate_id, claim))
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
    if requirement.category is RequirementCategory.OTHER:
        return f"custom_requirement:{requirement.id}"
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


def _suitability_factors(
    *,
    profile: DatasetProfile,
    required: list[ResearchRequirement],
    preferred: list[ResearchRequirement],
    evidence: list[EvidenceRecord],
    gates: list[GateResult],
    missing: list[str],
    met_preferred: list[str],
    unmet_preferred: list[str],
    evidence_confidence: ConfidenceLevel,
    conflicts: list[str],
) -> list[SuitabilityFactor]:
    factors: list[SuitabilityFactor] = []
    requirements_by_id = {item.id: item for item in [*required, *preferred]}

    def requirement_evidence_ids(requirement: ResearchRequirement) -> list[str]:
        claim = requirement_claim_key(requirement)
        return _evidence_for(evidence, profile.candidate_id, claim) if claim else []

    for requirement_id in missing:
        requirement = requirements_by_id[requirement_id]
        factors.append(
            SuitabilityFactor(
                kind=SuitabilityFactorKind.BLOCKER,
                label=requirement.label,
                explanation="Mandatory requirement is unsupported by native evidence.",
                evidence_ids=[],
            )
        )

    failed_gate_labels = {factor.label for factor in factors}
    for gate in gates:
        label = _GATE_LABELS.get(gate.gate, gate.gate.replace("_", " ").title())
        if not gate.passed and label not in failed_gate_labels:
            factors.append(
                SuitabilityFactor(
                    kind=SuitabilityFactorKind.BLOCKER,
                    label=label,
                    explanation=gate.reason,
                    evidence_ids=gate.evidence_ids,
                )
            )

    if not missing and all(gate.passed for gate in gates):
        factors.append(
            SuitabilityFactor(
                kind=SuitabilityFactorKind.STRENGTH,
                label="Mandatory requirements",
                explanation="All mandatory requirements and integrity gates are supported.",
                evidence_ids=[
                    evidence_id
                    for requirement in required
                    for evidence_id in requirement_evidence_ids(requirement)
                ],
            )
        )

    for requirement_id in met_preferred:
        requirement = requirements_by_id[requirement_id]
        factors.append(
            SuitabilityFactor(
                kind=SuitabilityFactorKind.STRENGTH,
                label=requirement.label,
                explanation="Preferred requirement is supported by native evidence.",
                evidence_ids=requirement_evidence_ids(requirement),
            )
        )

    for requirement_id in unmet_preferred:
        requirement = requirements_by_id[requirement_id]
        factors.append(
            SuitabilityFactor(
                kind=SuitabilityFactorKind.LIMITATION,
                label=requirement.label,
                explanation="Preferred requirement is unsupported by native evidence.",
                evidence_ids=[],
            )
        )

    if conflicts:
        factors.append(
            SuitabilityFactor(
                kind=SuitabilityFactorKind.LIMITATION,
                label="Evidence consistency",
                explanation=f"Conflicting native claims remain: {', '.join(conflicts)}.",
                evidence_ids=[],
            )
        )
    elif evidence_confidence is not ConfidenceLevel.HIGH:
        factors.append(
            SuitabilityFactor(
                kind=SuitabilityFactorKind.LIMITATION,
                label="Evidence confidence",
                explanation="Native evidence is incomplete or insufficiently authoritative.",
                evidence_ids=[],
            )
        )
    return factors


def assess_candidate(
    profile: DatasetProfile,
    requirements: list[ResearchRequirement],
    evidence: list[EvidenceRecord],
) -> CandidateAssessment:
    conflicts = detect_conflicts(evidence, profile.candidate_id)
    required = [item for item in requirements if item.priority is RequirementPriority.MUST]
    preferred = [
        item for item in requirements if item.priority is RequirementPriority.SHOULD
    ]
    missing = [
        item.id for item in required if not requirement_is_evidenced(item, profile, evidence)
    ]
    met_preferred = [
        item.id for item in preferred if requirement_is_evidenced(item, profile, evidence)
    ]
    unmet_preferred = [item.id for item in preferred if item.id not in met_preferred]
    domain_requirements = [
        item for item in required if item.category is RequirementCategory.DOMAIN
    ]
    label_requirements = [
        item for item in required if item.category is RequirementCategory.TASK_LABELS
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
    identity_evidence = _evidence_for(evidence, profile.candidate_id, "dataset_identity")
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
    gates = [
        GateResult(
            gate="dataset_identity",
            passed=profile.is_dataset_artifact and bool(identity_evidence),
            reason=(
                "Primary source is a verified dataset artifact"
                if profile.is_dataset_artifact and identity_evidence
                else profile.dataset_identity_reason
            ),
            evidence_ids=identity_evidence,
        ),
        GateResult(
            gate="provenance",
            passed=provenance_passed,
            reason="Canonical versioned source found"
            if provenance_passed
            else "Canonical version or provenance missing",
            evidence_ids=revision_evidence,
        ),
    ]
    if any(item.category is RequirementCategory.MODALITY for item in required):
        gates.append(
            GateResult(
                gate="time_series_files",
                passed=files_passed,
                reason=(
                    "Time-series files found"
                    if files_passed
                    else "No usable time-series files found"
                ),
                evidence_ids=file_evidence,
            )
        )
    if license_requirements:
        gates.append(
            GateResult(
                gate="license",
                passed=bool(profile.license_id) and licence_allowed,
                reason=(
                    "Explicit allowed licence found"
                    if profile.license_id and licence_allowed
                    else "Licence is missing or outside the allowed set"
                ),
                evidence_ids=license_evidence,
            )
        )
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
    if label_requirements:
        gates.append(
            GateResult(
                gate="task_labels",
                passed=labels_passed,
                reason="Required task labels found"
                if labels_passed
                else "Required task labels are incomplete",
                evidence_ids=label_evidence,
            )
        )
    if any(item.category is RequirementCategory.SCHEMA for item in required):
        gates.append(
            GateResult(
                gate="schema",
                passed=schema_passed,
                reason="Schema or channel documentation found"
                if schema_passed
                else "Schema or channel documentation missing",
                evidence_ids=schema_evidence,
            )
        )
    if any(item.category is RequirementCategory.ACQUISITION for item in required):
        gates.append(
            GateResult(
                gate="acquisition",
                passed=acquisition_passed,
                reason="Acquisition is within the configured bound"
                if acquisition_passed
                else "Acquisition size is unknown or exceeds the configured bound",
                evidence_ids=size_evidence,
            )
        )

    gates_pass = all(gate.passed for gate in gates) and not missing
    met_required = len(required) - len(missing)
    coverage = met_required / max(1, len(required))
    evidence_confidence = (
        ConfidenceLevel.HIGH
        if coverage == 1 and not conflicts
        else ConfidenceLevel.MEDIUM
        if coverage >= 0.8 and len(conflicts) <= 1
        else ConfidenceLevel.LOW
    )
    suitability_level = (
        SuitabilityLevel.LOW
        if not gates_pass
        else SuitabilityLevel.MEDIUM
        if unmet_preferred
        or conflicts
        or evidence_confidence is not ConfidenceLevel.HIGH
        else SuitabilityLevel.HIGH
    )
    candidate_evidence = [
        item
        for item in evidence
        if item.candidate_id == profile.candidate_id
        and item.status is VerificationStatus.VERIFIED
    ]
    authoritative_source_kind = (
        max(
            candidate_evidence,
            key=lambda item: (item.precedence, item.source_kind.value),
        ).source_kind
        if candidate_evidence
        else None
    )
    suitability_factors = _suitability_factors(
        profile=profile,
        required=required,
        preferred=preferred,
        evidence=evidence,
        gates=gates,
        missing=missing,
        met_preferred=met_preferred,
        unmet_preferred=unmet_preferred,
        evidence_confidence=evidence_confidence,
        conflicts=conflicts,
    )
    return CandidateAssessment(
        candidate_id=profile.candidate_id,
        gates=gates,
        evidence_confidence=evidence_confidence,
        suitability_level=suitability_level,
        suitability_factors=suitability_factors,
        missing_requirement_ids=missing,
        met_preferred_requirement_ids=met_preferred,
        unmet_preferred_requirement_ids=unmet_preferred,
        authoritative_source_kind=authoritative_source_kind,
        conflicts=conflicts,
    )


def apply_recommendation_confidence(
    assessments: list[CandidateAssessment],
) -> list[CandidateAssessment]:
    ranked = sorted(assessments, key=candidate_rank_key)
    if not ranked:
        return assessments
    top = ranked[0]
    high_candidates = [
        item for item in ranked if item.suitability_level is SuitabilityLevel.HIGH
    ]
    if top.suitability_level is SuitabilityLevel.HIGH:
        confidence = (
            ConfidenceLevel.HIGH
            if top.evidence_confidence is ConfidenceLevel.HIGH
            and len(high_candidates) == 1
            else ConfidenceLevel.MEDIUM
        )
        ranked[0] = top.model_copy(update={"recommendation_confidence": confidence})
    by_id = {item.candidate_id: item for item in ranked}
    return [by_id[item.candidate_id] for item in assessments]


def candidate_is_approvable(assessment: CandidateAssessment) -> bool:
    return (
        assessment.suitability_level in {SuitabilityLevel.MEDIUM, SuitabilityLevel.HIGH}
        and not assessment.missing_requirement_ids
        and all(gate.passed for gate in assessment.gates)
    )


def candidate_rank_key(
    assessment: CandidateAssessment,
) -> tuple[bool, bool, int, int, int, int, str]:
    identity_gate = next(
        (gate for gate in assessment.gates if gate.gate == "dataset_identity"), None
    )
    domain_gate = next((gate for gate in assessment.gates if gate.gate == "domain"), None)
    level_rank = {
        SuitabilityLevel.HIGH: 0,
        SuitabilityLevel.MEDIUM: 1,
        SuitabilityLevel.LOW: 2,
    }[assessment.suitability_level]
    confidence_rank = {
        ConfidenceLevel.HIGH: 0,
        ConfidenceLevel.MEDIUM: 1,
        ConfidenceLevel.LOW: 2,
    }[assessment.evidence_confidence]
    source_rank = {
        SourceKind.ZENODO: 0,
        SourceKind.HUGGING_FACE: 1,
        SourceKind.GITHUB: 2,
        None: 3,
    }[assessment.authoritative_source_kind]
    return (
        identity_gate is None or not identity_gate.passed,
        domain_gate is not None and not domain_gate.passed,
        level_rank,
        len(assessment.unmet_preferred_requirement_ids),
        confidence_rank,
        source_rank,
        assessment.candidate_id,
    )

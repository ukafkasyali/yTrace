from datetime import UTC, datetime
from hashlib import sha256

from data_sourcing.models import (
    CandidateTier,
    ConfidenceLevel,
    DatasetProfile,
    EvidenceRecord,
    RequirementCategory,
    RequirementPriority,
    ResearchRequirement,
    SourceKind,
    VerificationStatus,
)
from data_sourcing.scoring import (
    apply_recommendation_confidence,
    assess_candidate,
    detect_conflicts,
)


def requirement(category: RequirementCategory, *values: str) -> ResearchRequirement:
    return ResearchRequirement(
        id=f"req_{category.value.casefold()}",
        label=category.value,
        description=category.value,
        priority=RequirementPriority.MUST,
        category=category,
        expected_values=list(values),
    )


def profile(candidate_id: str = "ds_0123456789ab") -> DatasetProfile:
    return DatasetProfile(
        candidate_id=candidate_id,
        name="Robot collision signals",
        canonical_url="https://zenodo.org/records/21941203",
        source_kinds=[SourceKind.ZENODO, SourceKind.GITHUB],
        revision="1",
        license_id="cc-by-4.0",
        file_count=48,
        total_size_bytes=9_100_000_000,
        file_extensions=[".mat", ".zip"],
        labels=["collision", "contact", "free"],
        sample_rate_hz=1_000,
        channel_count=7,
        has_time_series_files=True,
        schema_documented=True,
        acquisition_feasible=True,
    )


def evidence(value: str, source: SourceKind, suffix: str) -> EvidenceRecord:
    return EvidenceRecord(
        id="ev_" + ("a" if source is SourceKind.GITHUB else "b") * 16,
        candidate_id="ds_0123456789ab",
        claim_key="batch_count",
        observed_value=value,
        source_url=(
            "https://zenodo.org/records/21941203"
            if source is SourceKind.ZENODO
            else "https://github.com/zhang-zengjie/robot-raw-collision-signals"
        ),
        source_kind=source,
        status=VerificationStatus.VERIFIED,
        precedence=100 if source is SourceKind.ZENODO else 60,
        retrieved_at=datetime.now(UTC),
    )


def complete_evidence(candidate_id: str) -> list[EvidenceRecord]:
    facts = {
        "revision": "1",
        "license": "cc-by-4.0",
        "file_extensions": ".mat,.zip",
        "labels": "collision,contact,free",
        "sample_rate_hz": "1000",
        "schema": "documented",
        "total_size_bytes": "9100000000",
    }
    return [
        EvidenceRecord(
            id=f"ev_{sha256(f'{candidate_id}:{claim}'.encode()).hexdigest()[:16]}",
            candidate_id=candidate_id,
            claim_key=claim,
            observed_value=value,
            source_url="https://zenodo.org/records/21941203",
            source_kind=SourceKind.ZENODO,
            status=VerificationStatus.VERIFIED,
            precedence=100,
        )
        for claim, value in facts.items()
    ]


def test_complete_candidate_is_recommended() -> None:
    requirements = [
        requirement(RequirementCategory.TASK_LABELS, "collision", "contact", "free"),
        requirement(RequirementCategory.SAMPLING_RATE, "1000"),
        requirement(RequirementCategory.LICENSE),
        requirement(RequirementCategory.PROVENANCE),
        requirement(RequirementCategory.MODALITY),
        requirement(RequirementCategory.SCHEMA),
        requirement(RequirementCategory.ACQUISITION),
    ]

    dataset_profile = profile()
    assessment = assess_candidate(
        dataset_profile,
        requirements,
        complete_evidence(dataset_profile.candidate_id),
    )

    assert assessment.tier is CandidateTier.RECOMMEND
    assert assessment.total_score >= 80
    assert assessment.evidence_confidence is ConfidenceLevel.HIGH


def test_internal_fault_is_not_inferred_from_collision_labels() -> None:
    assessment = assess_candidate(
        profile(),
        [requirement(RequirementCategory.TASK_LABELS, "internal mechanical fault")],
        [],
    )

    assert assessment.tier is CandidateTier.REJECT
    assert "req_task_labels" in assessment.missing_requirement_ids


def test_conflicting_batch_counts_are_retained() -> None:
    records = [
        evidence("42", SourceKind.GITHUB, "github"),
        evidence("48", SourceKind.ZENODO, "zenodo"),
    ]

    assert detect_conflicts(records, "ds_0123456789ab") == ["batch_count"]


def test_recommendation_confidence_requires_margin() -> None:
    requirements = [requirement(RequirementCategory.TASK_LABELS, "collision")]
    first_profile = profile("ds_0123456789ab")
    first = assess_candidate(
        first_profile,
        requirements,
        complete_evidence(first_profile.candidate_id),
    )
    second_profile = profile("ds_abcdef012345")
    second = assess_candidate(
        second_profile,
        requirements,
        complete_evidence(second_profile.candidate_id),
    )

    updated = apply_recommendation_confidence([first, second])

    assert updated[0].recommendation_confidence is ConfidenceLevel.MEDIUM


def test_explicit_license_outside_allowlist_fails_hard_gate() -> None:
    licence = requirement(RequirementCategory.LICENSE, "apache-2.0")

    dataset_profile = profile()
    assessment = assess_candidate(
        dataset_profile,
        [licence],
        complete_evidence(dataset_profile.candidate_id),
    )

    assert assessment.tier is CandidateTier.REJECT
    assert next(gate for gate in assessment.gates if gate.gate == "license").passed is False


def test_profile_facts_without_evidence_cannot_be_scored_as_ready() -> None:
    assessment = assess_candidate(
        profile(),
        [requirement(RequirementCategory.TASK_LABELS, "collision")],
        [],
    )

    assert assessment.tier is CandidateTier.REJECT
    assert assessment.total_score == 0


def test_missing_license_and_unusable_files_fail_hard_gates() -> None:
    dataset_profile = profile().model_copy(
        update={"license_id": None, "has_time_series_files": False}
    )
    evidence_records = [
        item
        for item in complete_evidence(dataset_profile.candidate_id)
        if item.claim_key != "license"
    ]

    assessment = assess_candidate(
        dataset_profile,
        [
            requirement(RequirementCategory.LICENSE),
            requirement(RequirementCategory.MODALITY),
        ],
        evidence_records,
    )

    failed = {gate.gate for gate in assessment.gates if not gate.passed}
    assert {"license", "time_series_files"}.issubset(failed)
    assert assessment.tier is CandidateTier.REJECT

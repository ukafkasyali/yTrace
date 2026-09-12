from data_sourcing.adapters.native import NativeDocument
from data_sourcing.config import Settings
from data_sourcing.models import (
    RequirementCategory,
    RequirementPriority,
    ResearchRequirement,
    SourceKind,
)
from data_sourcing.relevance import (
    CustomRequirementJudgment,
    DomainJudgment,
    EvidenceRelevanceJudge,
    SupportedCustomRequirement,
    SupportedDomain,
)


def document(name: str, text: str) -> NativeDocument:
    return NativeDocument(
        source_url="https://zenodo.org/records/123",
        source_kind=SourceKind.ZENODO,
        name=name,
        revision="1",
        text=text,
    )


def test_semantic_domain_match_requires_a_verbatim_native_quote(monkeypatch) -> None:
    judge = EvidenceRelevanceJudge(Settings(_env_file=None))
    sources = [
        document(
            "Manufacturing process data",
            "Measurements from computer numerical control machining operations.",
        )
    ]
    monkeypatch.setattr(
        judge,
        "_model_judgment",
        lambda *_: DomainJudgment(
            supported=[
                SupportedDomain(
                    term="cnc machines",
                    document_index=0,
                    quote="computer numerical control machining operations",
                )
            ]
        ),
    )

    result = judge.evaluate("ds_0123456789ab", sources, ["cnc machines"])

    assert result.matched_terms == ["cnc machines"]
    assert result.evidence[0].note == (
        "Native excerpt: computer numerical control machining operations"
    )


def test_semantic_domain_match_rejects_an_unverifiable_quote(monkeypatch) -> None:
    judge = EvidenceRelevanceJudge(Settings(_env_file=None))
    sources = [document("Robot data", "Joint torque from robot collision experiments.")]
    monkeypatch.setattr(
        judge,
        "_model_judgment",
        lambda *_: DomainJudgment(
            supported=[
                SupportedDomain(
                    term="cnc machines",
                    document_index=0,
                    quote="CNC machining operations",
                )
            ]
        ),
    )

    result = judge.evaluate("ds_0123456789ab", sources, ["cnc machines"])

    assert result.matched_terms == []
    assert result.evidence == []


def test_semantic_judge_can_reject_a_negated_keyword_mention(monkeypatch) -> None:
    judge = EvidenceRelevanceJudge(Settings(_env_file=None))
    sources = [document("Robot data", "This is not a CNC machining dataset.")]
    monkeypatch.setattr(judge, "_model_judgment", lambda *_: DomainJudgment())

    result = judge.evaluate("ds_0123456789ab", sources, ["cnc"])

    assert result.matched_terms == []
    assert result.evidence == []


def test_custom_requirement_requires_a_verbatim_native_quote(monkeypatch) -> None:
    judge = EvidenceRelevanceJudge(Settings(_env_file=None))
    sources = [document("Robot data", "The archive contains 206 labelled collision sequences.")]
    custom = ResearchRequirement(
        id="req_custom_123456789abc",
        label="At least 200 collision sequences",
        description="Custom reviewer requirement",
        priority=RequirementPriority.MUST,
        category=RequirementCategory.OTHER,
        expected_values=["At least 200 collision sequences"],
    )
    monkeypatch.setattr(
        judge,
        "_model_custom_requirements",
        lambda *_: CustomRequirementJudgment(
            supported=[
                SupportedCustomRequirement(
                    requirement_id=custom.id,
                    document_index=0,
                    quote="contains 206 labelled collision sequences",
                )
            ]
        ),
    )

    result = judge.evaluate_custom_requirements("ds_0123456789ab", sources, [custom])

    assert result.evidence[0].requirement_id == custom.id
    assert result.evidence[0].claim_key == f"custom_requirement:{custom.id}"


def test_custom_requirement_rejects_an_invented_quote(monkeypatch) -> None:
    judge = EvidenceRelevanceJudge(Settings(_env_file=None))
    sources = [document("Robot data", "The archive contains collision recordings.")]
    custom = ResearchRequirement(
        id="req_custom_123456789abc",
        label="At least 200 collision sequences",
        description="Custom reviewer requirement",
        priority=RequirementPriority.MUST,
        category=RequirementCategory.OTHER,
        expected_values=["At least 200 collision sequences"],
    )
    monkeypatch.setattr(
        judge,
        "_model_custom_requirements",
        lambda *_: CustomRequirementJudgment(
            supported=[
                SupportedCustomRequirement(
                    requirement_id=custom.id,
                    document_index=0,
                    quote="contains 206 labelled collision sequences",
                )
            ]
        ),
    )

    result = judge.evaluate_custom_requirements("ds_0123456789ab", sources, [custom])

    assert result.evidence == []

from dataclasses import replace
from pathlib import Path

from dataset_profiler.evidence import (
    DocumentationSource,
    Evidence,
    EvidenceBudget,
    EvidenceErrorCode,
    EvidenceKind,
    EvidenceLimits,
    EvidenceSession,
)
from test_evidence import _kuka_profile


def _profile(path: Path):
    path.mkdir()
    return _kuka_profile(path)


def _documentation(tmp_path: Path) -> tuple[DocumentationSource, DocumentationSource]:
    readme = tmp_path / "README.md"
    readme.write_text(
        "Dataset signals\n"
        "PosMsr contains measured joint positions.\n"
        "Values are sampled during each experiment.\n"
        "\n"
        "MsrExtTrq records measured external torque.\n"
        "A collision annotation marks the recorded event.\n",
        encoding="utf-8",
    )
    notes = tmp_path / "notes.txt"
    notes.write_text(
        "Additional notes\n"
        "The lowercase posmsr spelling appears here.\n"
        "\n"
        "collision trials use several objects.\n",
        encoding="utf-8",
    )
    return (
        DocumentationSource("readme", readme, "Dataset README"),
        DocumentationSource("notes", notes),
    )


def test_documentation_source_discovery_is_bounded_and_hides_paths(tmp_path):
    profile = _profile(tmp_path / "dataset")
    sources = _documentation(tmp_path)

    evidence = EvidenceSession(profile, documentation_sources=list(sources)).documentation_sources()

    assert isinstance(evidence, Evidence)
    assert evidence.kind is EvidenceKind.DOCUMENTATION_SOURCES
    assert evidence.value["returned"] == 2
    assert [item["source_id"] for item in evidence.value["items"]] == ["notes", "readme"]
    assert all(item["status"] == "available" for item in evidence.value["items"])
    assert {item["document_type"] for item in evidence.value["items"]} == {"markdown", "text"}
    assert all("line_count" in item and "size_bytes" in item for item in evidence.value["items"])
    assert str(tmp_path) not in evidence.to_json()


def test_documentation_search_is_deterministic_and_ranks_exact_case_first(tmp_path):
    profile = _profile(tmp_path / "dataset")
    sources = _documentation(tmp_path)

    first = EvidenceSession(profile, documentation_sources=list(sources)).documentation_search("PosMsr")
    second = EvidenceSession(profile, documentation_sources=list(reversed(sources))).documentation_search("PosMsr")

    assert isinstance(first, list) and isinstance(second, list)
    assert [item.id for item in first] == [item.id for item in second]
    assert first[0].source == "readme"
    assert first[0].id.startswith("ev_documentation_posmsr_")


def test_documentation_excerpt_has_context_and_line_provenance(tmp_path):
    profile = _profile(tmp_path / "dataset")
    result = EvidenceSession(
        profile, documentation_sources=list(_documentation(tmp_path))
    ).documentation_search("MsrExtTrq", max_results=1)

    assert isinstance(result, list) and len(result) == 1
    evidence = result[0]
    assert evidence.value["line_start"] == 4
    assert evidence.value["match_line"] == 5
    assert evidence.value["line_end"] == 6
    assert "measured external torque" in evidence.value["excerpt"]
    assert "collision annotation" in evidence.value["excerpt"]
    assert len(evidence.metadata["document_sha256"]) == 64


def test_documentation_result_and_character_limits_are_enforced(tmp_path):
    profile = _profile(tmp_path / "dataset")
    source_path = tmp_path / "many.md"
    source_path.write_text("\n\n".join(f"collision occurrence {index}" for index in range(8)),
                           encoding="utf-8")
    limits = replace(
        EvidenceLimits(),
        max_documentation_results=2,
        max_documentation_excerpt_chars=24,
        max_documentation_response_chars=35,
    )
    session = EvidenceSession(
        profile,
        limits=limits,
        documentation_sources=[DocumentationSource("many", source_path)],
    )

    result = session.documentation_search("collision", max_results=99)

    assert isinstance(result, list)
    assert len(result) <= 2
    assert sum(len(item.value["excerpt"]) for item in result) <= 35
    assert all(len(item.value["excerpt"]) <= 24 for item in result)
    assert any(item.value["excerpt_truncated"] for item in result)


def test_documentation_query_and_character_budgets_are_enforced(tmp_path):
    profile = _profile(tmp_path / "dataset")
    source = _documentation(tmp_path)[0]
    query_limited = EvidenceSession(
        profile,
        budget=replace(EvidenceBudget(), max_documentation_queries=1),
        documentation_sources=[source],
    )
    assert isinstance(query_limited.documentation_search("PosMsr"), list)
    exhausted = query_limited.documentation_search("collision")
    assert exhausted.code is EvidenceErrorCode.QUERY_BUDGET_EXCEEDED

    character_limited = EvidenceSession(
        profile,
        budget=replace(EvidenceBudget(), max_total_documentation_chars=10),
        documentation_sources=[source],
    )
    first = character_limited.documentation_search("PosMsr")
    assert isinstance(first, list)
    assert character_limited.usage.documentation_chars == 10
    exhausted = character_limited.documentation_search("collision")
    assert exhausted.code is EvidenceErrorCode.DOCUMENTATION_BUDGET_EXCEEDED


def test_documentation_unknown_and_empty_queries_are_safe(tmp_path):
    profile = _profile(tmp_path / "dataset")
    session = EvidenceSession(profile, documentation_sources=list(_documentation(tmp_path)))

    assert session.documentation_search("term-that-is-not-present") == []
    invalid = session.documentation_search("   ")
    assert invalid.code is EvidenceErrorCode.INVALID_QUERY


def test_unavailable_unsupported_and_malformed_sources_are_structured(tmp_path):
    profile = _profile(tmp_path / "dataset")
    unavailable = EvidenceSession(profile).documentation_search("anything")
    assert unavailable.code is EvidenceErrorCode.UNAVAILABLE_DOCUMENTATION

    pdf = tmp_path / "manual.pdf"
    pdf.write_bytes(b"not parsed")
    unsupported_session = EvidenceSession(
        profile, documentation_sources=[DocumentationSource("manual", pdf)]
    )
    source_info = unsupported_session.documentation_sources()
    assert isinstance(source_info, Evidence)
    assert source_info.value["items"][0]["status"] == "unsupported"
    assert source_info.value["items"][0]["error_code"] == "UNSUPPORTED_DOCUMENTATION"
    assert unsupported_session.documentation_search("anything").code is \
        EvidenceErrorCode.UNSUPPORTED_DOCUMENTATION

    malformed = tmp_path / "broken.txt"
    malformed.write_bytes(b"\xff\xfe")
    malformed_session = EvidenceSession(
        profile, documentation_sources=[DocumentationSource("broken", malformed)]
    )
    assert malformed_session.documentation_search("anything").code is \
        EvidenceErrorCode.INVALID_DOCUMENTATION_SOURCE

    traversal_id_session = EvidenceSession(
        profile, documentation_sources=[DocumentationSource("../outside", malformed)]
    )
    traversal_info = traversal_id_session.documentation_sources()
    assert isinstance(traversal_info, Evidence)
    assert traversal_info.value["items"][0]["status"] == "invalid"


def test_documentation_search_integrates_with_existing_evidence_session(tmp_path):
    profile = _profile(tmp_path / "dataset")
    session = EvidenceSession(profile, documentation_sources=list(_documentation(tmp_path)))

    for query in ("PosMsr", "MsrExtTrq", "collision"):
        results = session.documentation_search(query)
        assert isinstance(results, list) and results
        assert all(isinstance(item, Evidence) for item in results)
        assert all(item.kind is EvidenceKind.DOCUMENTATION for item in results)
        assert all(item.target == query for item in results)
        assert all(item.value["query"] == query for item in results)
    assert session.usage.documentation_queries == 3


def test_documentation_source_count_and_serialized_response_limits(tmp_path):
    profile = _profile(tmp_path / "dataset")
    sources = list(_documentation(tmp_path))
    count_limited = EvidenceSession(
        profile,
        limits=replace(EvidenceLimits(), max_documentation_sources=1),
        documentation_sources=sources,
    ).documentation_sources()
    assert isinstance(count_limited, Evidence)
    assert count_limited.value["returned"] == 1
    assert count_limited.value["registered"] == 2
    assert count_limited.value["truncated"]

    response_limited = EvidenceSession(
        profile,
        limits=replace(EvidenceLimits(), max_response_bytes=1),
        documentation_sources=sources,
    ).documentation_sources()
    assert response_limited.code is EvidenceErrorCode.RESPONSE_TOO_LARGE

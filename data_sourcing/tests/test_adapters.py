import httpx
import pytest

from data_sourcing.adapters import NativeVerifier, TavilySearchAdapter, canonicalize_results
from data_sourcing.adapters.discovery import SourceUnavailable
from data_sourcing.config import Settings
from data_sourcing.models import DatasetCandidate, ExecutionMode, SearchResult, SourceKind
from data_sourcing.scoring import detect_conflicts


def settings(**updates: object) -> Settings:
    return Settings(_env_file=None, **updates)


def test_tavily_adapter_contract_and_credit_usage() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == "https://api.tavily.com/search"
        assert request.headers["Authorization"] == "Bearer test-key"
        payload = __import__("json").loads(request.content)
        assert payload["search_depth"] == "advanced"
        assert payload["safe_search"] is True
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "title": "Robot data",
                        "url": "https://zenodo.org/records/123",
                        "content": "Raw robot torque",
                        "score": 0.9,
                    }
                ],
                "usage": {"credits": 2},
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    adapter = TavilySearchAdapter(settings(tavily_api_key="test-key"), client)

    batch = adapter.search("robot collision data")

    assert batch.credits_used == 2
    assert batch.execution_mode is ExecutionMode.LIVE
    assert len(batch.results) == 1


def test_cached_search_is_explicit_and_free() -> None:
    adapter = TavilySearchAdapter(
        settings(), httpx.Client(transport=httpx.MockTransport(lambda _: None))
    )

    batch = adapter.search("robot collision dataset", allow_cached_demo=True)

    assert batch.execution_mode is ExecutionMode.CACHED
    assert batch.credits_used == 0
    assert len(batch.results) == 3


def test_live_search_failure_uses_cache_only_for_exact_demo() -> None:
    def unavailable(_: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"error": "unavailable"})

    adapter = TavilySearchAdapter(
        settings(tavily_api_key="test-key"),
        httpx.Client(transport=httpx.MockTransport(unavailable)),
    )

    batch = adapter.search("robot collision dataset", allow_cached_demo=True)

    assert batch.execution_mode is ExecutionMode.CACHED
    assert batch.credits_used == 0


def test_canonicalization_groups_repo_and_linked_records() -> None:
    results = [
        SearchResult(
            title="Robot signals",
            url="https://github.com/org/repo",
            content=("Data at https://zenodo.org/records/123 and https://zenodo.org/records/456"),
            score=1,
        ),
        SearchResult(
            title="Part one",
            url="https://zenodo.org/records/123",
            score=0.9,
        ),
    ]

    candidates = canonicalize_results(results)

    assert len(candidates) == 1
    assert str(candidates[0].canonical_url).rstrip("/") == "https://github.com/org/repo"
    assert {str(url).rstrip("/") for url in candidates[0].related_urls} == {
        "https://zenodo.org/records/123",
        "https://zenodo.org/records/456",
    }


def test_canonicalization_caps_deep_candidates_at_eight() -> None:
    results = [
        SearchResult(
            title=f"Dataset {index}",
            url=f"https://zenodo.org/records/{index}",
            score=0.5,
        )
        for index in range(1, 11)
    ]

    assert len(canonicalize_results(results, limit=8)) == 8


def test_native_fixture_retains_batch_contradiction_and_coverage_limits() -> None:
    search = TavilySearchAdapter(settings()).search(
        "robot collision dataset", allow_cached_demo=True
    )
    candidate = canonicalize_results(search.results)[0]
    verifier = NativeVerifier(settings())

    verified = verifier.verify(candidate, cached=True)

    assert verified.profile.labels == ["collision", "contact"]
    assert "free" not in verified.profile.labels
    assert "internal mechanical fault" not in verified.profile.labels
    assert verified.profile.sample_rate_hz == 1_000
    assert verified.profile.channel_count == 7
    assert verified.profile.license_id == "cc-by-4.0"
    assert verified.profile.acquisition_feasible is True
    assert "batch_count_part_ii" in detect_conflicts(verified.evidence, candidate.id)


def test_acquisition_gate_fails_when_cached_data_exceeds_request_bound() -> None:
    candidate = canonicalize_results(
        TavilySearchAdapter(settings())
        .search("robot collision dataset", allow_cached_demo=True)
        .results
    )[0]

    verified = NativeVerifier(settings()).verify(
        candidate,
        cached=True,
        max_download_bytes=1_000_000,
    )

    assert verified.profile.acquisition_feasible is False


def test_github_native_adapter_contract() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/readme"):
            return httpx.Response(
                200,
                text=(
                    "Dataset Structure with collision time-series torque signals at 1 kHz "
                    "for all seven joints in MATLAB (.mat)."
                ),
            )
        if "/commits/" in path:
            return httpx.Response(200, json={"sha": "abc123"})
        if "/git/trees/" in path:
            return httpx.Response(
                200,
                json={"tree": [{"path": "signals.mat", "type": "blob", "size": 200}]},
            )
        return httpx.Response(
            200,
            json={
                "full_name": "org/repo",
                "description": "Robot collision signals",
                "default_branch": "main",
                "license": {"spdx_id": "MIT"},
            },
        )

    candidate = DatasetCandidate(
        id="ds_111111111111",
        name="Robot signals",
        canonical_url="https://github.com/org/repo",
        source_kind=SourceKind.GITHUB,
    )
    verifier = NativeVerifier(
        settings(),
        httpx.Client(transport=httpx.MockTransport(handler)),
        validate_dns=False,
    )

    verified = verifier.verify(candidate)

    assert verified.profile.revision == "GITHUB:abc123"
    assert verified.profile.license_id == "MIT"
    assert verified.profile.has_time_series_files is True


def test_zenodo_native_adapter_contract() -> None:
    payload = {
        "title": "Robot collision record",
        "revision": 3,
        "metadata": {
            "description": (
                "Dataset structure: collision time-series torque signals at 1 kHz "
                "for all seven joints in MATLAB (.mat)."
            ),
            "license": {"id": "cc-by-4.0"},
        },
        "files": [{"key": "collision-batch-01.tar.zst", "size": 500}],
    }
    verifier = NativeVerifier(
        settings(),
        httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload))),
        validate_dns=False,
    )
    candidate = DatasetCandidate(
        id="ds_222222222222",
        name="Zenodo robot signals",
        canonical_url="https://zenodo.org/records/123",
        source_kind=SourceKind.ZENODO,
    )

    verified = verifier.verify(candidate)

    assert verified.profile.revision == "ZENODO:123.r3"
    assert verified.profile.total_size_bytes == 500
    assert verified.profile.labels == ["collision"]


def test_zenodo_label_aliases_are_normalized_to_task_classes() -> None:
    payload = {
        "title": "Robot collision and contact signals",
        "revision": 1,
        "metadata": {
            "description": (
                "Dataset structure: accidental collision (cls), intentional manual contacts "
                "(ctc), and free from contacts (fre), sampled at 1 kHz."
            ),
            "license": {"id": "cc-by-4.0"},
        },
        "files": [{"key": "fre-joint-1.csv", "size": 500}],
    }
    verifier = NativeVerifier(
        settings(),
        httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload))),
        validate_dns=False,
    )
    candidate = DatasetCandidate(
        id="ds_444444444444",
        name="Robot signal classes",
        canonical_url="https://zenodo.org/records/6461868",
        source_kind=SourceKind.ZENODO,
    )

    verified = verifier.verify(candidate)

    assert verified.profile.labels == ["collision", "contact", "free"]
    label_evidence = [
        item.observed_value for item in verified.evidence if item.claim_key == "labels"
    ]
    assert label_evidence == ["collision,contact,free"]


def test_hugging_face_native_adapter_contract() -> None:
    payload = {
        "id": "org/robot-data",
        "sha": "def456",
        "description": "Dataset columns contain robot contact torque time-series signals.",
        "cardData": {"license": "apache-2.0"},
        "siblings": [{"rfilename": "train.parquet", "size": 300}],
    }
    verifier = NativeVerifier(
        settings(),
        httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload))),
        validate_dns=False,
    )
    candidate = DatasetCandidate(
        id="ds_333333333333",
        name="HF robot signals",
        canonical_url="https://huggingface.co/datasets/org/robot-data",
        source_kind=SourceKind.HUGGING_FACE,
    )

    verified = verifier.verify(candidate)

    assert verified.profile.revision == "HUGGING_FACE:def456"
    assert verified.profile.license_id == "apache-2.0"
    assert verified.profile.file_extensions == [".parquet"]


def test_native_adapter_stops_stream_over_size_limit() -> None:
    verifier = NativeVerifier(
        settings(max_source_response_bytes=10_000),
        httpx.Client(
            transport=httpx.MockTransport(lambda _: httpx.Response(200, content=b"x" * 10_001))
        ),
        validate_dns=False,
    )
    candidate = DatasetCandidate(
        id="ds_444444444444",
        name="Oversized source",
        canonical_url="https://zenodo.org/records/123",
        source_kind=SourceKind.ZENODO,
    )

    with pytest.raises(SourceUnavailable, match="size limit"):
        verifier.verify(candidate)

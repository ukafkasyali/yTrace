import httpx

from data_sourcing.adapters import NativeVerifier, TavilySearchAdapter, canonicalize_results
from data_sourcing.config import Settings
from data_sourcing.models import ExecutionMode, SearchResult
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
    assert detect_conflicts(verified.evidence, candidate.id) >= ["batch_count"]


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

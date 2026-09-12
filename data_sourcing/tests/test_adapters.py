import httpx
import pytest

from data_sourcing.adapters import NativeVerifier, TavilySearchAdapter, canonicalize_results
from data_sourcing.adapters.discovery import SourceUnavailable
from data_sourcing.adapters.native import NativeDocument, NativeFile
from data_sourcing.config import Settings
from data_sourcing.models import DatasetCandidate, ExecutionMode, SearchResult, SourceKind
from data_sourcing.relevance import EvidenceRelevanceJudge
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


def test_canonicalization_preserves_repo_and_linked_record_identities() -> None:
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

    assert len(candidates) == 3
    by_url = {str(candidate.canonical_url).rstrip("/"): candidate for candidate in candidates}
    assert set(by_url) == {
        "https://github.com/org/repo",
        "https://zenodo.org/records/123",
        "https://zenodo.org/records/456",
    }
    assert {str(url).rstrip("/") for url in by_url["https://github.com/org/repo"].related_urls} == {
        "https://zenodo.org/records/123",
        "https://zenodo.org/records/456",
    }
    assert len({candidate.id for candidate in candidates}) == 3


def test_canonicalization_marks_every_unverified_source_as_a_lead() -> None:
    candidates = canonicalize_results(
        [
            SearchResult(
                title="Computer vision guide",
                url="https://github.com/example/guide",
                content="Dataset at https://zenodo.org/records/123",
                score=0.9,
            )
        ]
    )

    assert all(candidate.source_role.value == "DISCOVERY_LEAD" for candidate in candidates)
    assert all(candidate.discovery_depth == 0 for candidate in candidates)


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


def test_identity_judge_rejects_a_guide_even_when_it_contains_a_csv_index() -> None:
    judge = EvidenceRelevanceJudge(settings())
    document = NativeDocument(
        source_url="https://github.com/example/computer-vision-guide",
        source_kind=SourceKind.GITHUB,
        name="Computer-Vision-Guide",
        revision="v1",
        text="A curated learning guide and paper list for computer vision datasets.",
        files=[NativeFile(name="resources/datasets.csv", size=100)],
    )

    result = judge.evaluate_dataset_identity("ds_111111111111", document)

    assert result.is_dataset_artifact is False
    assert result.evidence == []
    assert "discovery" in result.reason.casefold()


def test_identity_judge_requires_source_local_files_and_ignores_linked_dataset_files() -> None:
    judge = EvidenceRelevanceJudge(settings())
    guide = NativeDocument(
        source_url="https://github.com/example/dataset-guide",
        source_kind=SourceKind.GITHUB,
        name="Dataset guide",
        revision="v1",
        text="This guide links to a robot collision dataset.",
        files=[],
        related_urls=["https://zenodo.org/records/123"],
    )

    result = judge.evaluate_dataset_identity("ds_111111111111", guide)

    assert result.is_dataset_artifact is False
    assert "direct" in result.reason.casefold()


def test_identity_judge_promotes_a_native_dataset_with_direct_measurements() -> None:
    judge = EvidenceRelevanceJudge(settings())
    document = NativeDocument(
        source_url="https://zenodo.org/records/123",
        source_kind=SourceKind.ZENODO,
        name="Robot collision measurements",
        revision="v1",
        text="This dataset contains recorded robot collision torque signals.",
        files=[NativeFile(name="signals.csv", size=100)],
    )

    result = judge.evaluate_dataset_identity("ds_111111111111", document)

    assert result.is_dataset_artifact is True
    assert result.evidence[0].claim_key == "dataset_identity"


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
                json={
                    "tree": [
                        {
                            "path": "signals.mat",
                            "type": "blob",
                            "size": 200,
                            "sha": "a" * 40,
                        }
                    ]
                },
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
    assert verified.profile.source_kind is SourceKind.GITHUB
    assert verified.profile.source_revision == "abc123"
    assert len(verified.profile.assets) == 1
    assert verified.profile.assets[0].provider_locator == f"github:org/repo:blob:{'a' * 40}"


def test_native_adapter_follows_validated_same_host_redirect() -> None:
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        if request.url.path == "/repos/mit-fast/Blackbird-Dataset":
            return httpx.Response(
                301,
                headers={"Location": "https://api.github.com/repositories/145477026"},
            )
        return httpx.Response(200, json={"id": 145477026})

    verifier = NativeVerifier(
        settings(),
        httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False),
        validate_dns=False,
    )

    payload = verifier._get_json("https://api.github.com/repos/mit-fast/Blackbird-Dataset")

    assert payload == {"id": 145477026}
    assert requests == [
        "https://api.github.com/repos/mit-fast/Blackbird-Dataset",
        "https://api.github.com/repositories/145477026",
    ]


def test_native_adapter_rejects_cross_host_redirect() -> None:
    verifier = NativeVerifier(
        settings(),
        httpx.Client(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(
                    301,
                    headers={"Location": "https://huggingface.co/api/datasets/untrusted/repo"},
                )
            ),
            follow_redirects=False,
        ),
        validate_dns=False,
    )

    with pytest.raises((SourceUnavailable, ValueError), match="host"):
        verifier._get_json("https://api.github.com/repos/org/repo")


def test_github_missing_readme_keeps_repository_metadata() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/readme"):
            return httpx.Response(404, json={"message": "Not Found"})
        if "/commits/" in path:
            return httpx.Response(200, json={"sha": "abc123"})
        if "/git/trees/" in path:
            return httpx.Response(
                200,
                json={"tree": [{"path": "robotfailure.data.html", "type": "blob", "size": 200}]},
            )
        return httpx.Response(
            200,
            json={
                "full_name": "MaxBenChrist/robot-failure-dataset",
                "description": "Robot failure data",
                "default_branch": "master",
                "license": {"spdx_id": "MIT"},
            },
        )

    candidate = DatasetCandidate(
        id="ds_555555555555",
        name="Robot failure data",
        canonical_url="https://github.com/MaxBenChrist/robot-failure-dataset",
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


def test_search_snippet_links_cannot_contribute_native_verification_evidence() -> None:
    requested_hosts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_hosts.append(request.url.host)
        assert request.url.host == "api.github.com"
        path = request.url.path
        if path.endswith("/readme"):
            return httpx.Response(200, text="A curated guide to useful robotics resources.")
        if "/commits/" in path:
            return httpx.Response(200, json={"sha": "abc123"})
        if "/git/trees/" in path:
            return httpx.Response(
                200,
                json={"tree": [{"path": "README.md", "type": "blob", "size": 200}]},
            )
        return httpx.Response(
            200,
            json={
                "full_name": "example/guide",
                "description": "Robotics guide",
                "default_branch": "main",
                "license": {"spdx_id": "MIT"},
            },
        )

    candidate = DatasetCandidate(
        id="ds_777777777777",
        name="Robotics guide",
        canonical_url="https://github.com/example/guide",
        source_kind=SourceKind.GITHUB,
        related_urls=["https://zenodo.org/records/123"],
    )
    verifier = NativeVerifier(
        settings(),
        httpx.Client(transport=httpx.MockTransport(handler)),
        validate_dns=False,
    )

    verified = verifier.verify(candidate)

    assert verified.profile.source_kinds == [SourceKind.GITHUB]
    assert set(requested_hosts) == {"api.github.com"}


def test_oversized_github_tree_does_not_discard_linked_native_record() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.url.host == "zenodo.org":
            return httpx.Response(
                200,
                json={
                    "title": "Robot collision signals",
                    "revision": 2,
                    "metadata": {
                        "description": (
                            "Dataset structure: collision time-series torque signals at 1 kHz."
                        ),
                        "license": {"id": "cc-by-4.0"},
                    },
                    "files": [{"key": "signals.csv", "size": 500}],
                },
            )
        if path.endswith("/readme"):
            return httpx.Response(
                200,
                text=(
                    "Dataset Structure for collision time-series torque signals. "
                    "Archive: https://zenodo.org/records/123"
                ),
            )
        if "/commits/" in path:
            return httpx.Response(200, json={"sha": "abc123"})
        if "/git/trees/" in path:
            return httpx.Response(200, content=b"x" * 10_001)
        return httpx.Response(
            200,
            json={
                "full_name": "org/robot-data",
                "description": "Robot collision signals",
                "default_branch": "main",
                "license": {"spdx_id": "MIT"},
            },
        )

    candidate = DatasetCandidate(
        id="ds_666666666666",
        name="Robot signals",
        canonical_url="https://github.com/org/robot-data",
        source_kind=SourceKind.GITHUB,
    )
    verifier = NativeVerifier(
        settings(max_source_response_bytes=10_000),
        httpx.Client(transport=httpx.MockTransport(handler)),
        validate_dns=False,
    )

    verified = verifier.verify(candidate)

    assert verified.profile.source_kinds == [SourceKind.ZENODO, SourceKind.GITHUB]
    assert verified.profile.file_extensions == [".csv"]
    assert verified.profile.acquisition_feasible is True


def test_dataset_index_does_not_absorb_every_linked_repository() -> None:
    requested_paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_paths.append(request.url.path)
        path = request.url.path
        if path.endswith("/readme"):
            return httpx.Response(
                200,
                text="A collection of useful datasets: https://github.com/mit-fast/Blackbird-Dataset",
            )
        if "/commits/" in path:
            return httpx.Response(200, json={"sha": "abc123"})
        if "/git/trees/" in path:
            return httpx.Response(
                200,
                json={"tree": [{"path": "README.md", "type": "blob", "size": 200}]},
            )
        return httpx.Response(
            200,
            json={
                "full_name": "mint-lab/awesome-robotics-datasets",
                "description": "A collection of useful datasets",
                "default_branch": "main",
                "license": {"spdx_id": "MIT"},
            },
        )

    candidate = DatasetCandidate(
        id="ds_777777777777",
        name="Robotics dataset index",
        canonical_url="https://github.com/mint-lab/awesome-robotics-datasets",
        source_kind=SourceKind.GITHUB,
    )
    verifier = NativeVerifier(
        settings(),
        httpx.Client(transport=httpx.MockTransport(handler)),
        validate_dns=False,
    )

    verified = verifier.verify(candidate)

    assert verified.profile.source_kinds == [SourceKind.GITHUB]
    assert not any("Blackbird-Dataset" in path for path in requested_paths)


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
        "files": [
            {
                "key": "collision-batch-01.tar.zst",
                "size": 500,
                "checksum": "md5:2942bfabb3d05332b66eb128e0842cff",
                "links": {
                    "self": "https://zenodo.org/api/files/bucket/collision-batch-01.tar.zst"
                },
            }
        ],
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
    assert verified.profile.assets[0].source_checksum is not None
    assert verified.profile.assets[0].source_checksum.algorithm.value == "md5"


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


def test_zenodo_cross_host_file_link_is_not_exposed_as_an_asset() -> None:
    payload = {
        "title": "Robot signal data",
        "revision": 1,
        "metadata": {
            "description": "Dataset structure: robot torque time-series signals.",
            "license": {"id": "cc-by-4.0"},
        },
        "files": [
            {
                "key": "signals.csv",
                "size": 500,
                "links": {"self": "https://example.test/internal/signals.csv"},
            }
        ],
    }
    verifier = NativeVerifier(
        settings(),
        httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload))),
        validate_dns=False,
    )
    candidate = DatasetCandidate(
        id="ds_888888888888",
        name="Robot signals",
        canonical_url="https://zenodo.org/records/123",
        source_kind=SourceKind.ZENODO,
    )

    verified = verifier.verify(candidate)

    assert verified.profile.has_time_series_files is True
    assert verified.profile.assets == []


def test_native_domain_evidence_distinguishes_cnc_from_robot_data() -> None:
    payload = {
        "title": "CNC machining process monitoring",
        "revision": 1,
        "metadata": {
            "description": (
                "Dataset structure for CNC machining with spindle current and head position "
                "time-series signals."
            ),
            "license": {"id": "cc-by-4.0"},
        },
        "files": [{"key": "cnc-signals.csv", "size": 500}],
    }
    verifier = NativeVerifier(
        settings(),
        httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload))),
        validate_dns=False,
    )
    candidate = DatasetCandidate(
        id="ds_555555555555",
        name="CNC process signals",
        canonical_url="https://zenodo.org/records/555",
        source_kind=SourceKind.ZENODO,
    )

    verified = verifier.verify(candidate)
    relevance = EvidenceRelevanceJudge(settings()).evaluate(
        candidate.id, verified.documents, ["cnc", "robot"]
    )

    assert relevance.matched_terms == ["cnc"]
    domain_evidence = [
        item.observed_value for item in relevance.evidence if item.claim_key == "domains"
    ]
    assert domain_evidence == ["cnc"]


def test_hugging_face_native_adapter_contract() -> None:
    payload = {
        "id": "org/robot-data",
        "sha": "def456",
        "description": "Dataset columns contain robot contact torque time-series signals.",
        "cardData": {"license": "apache-2.0"},
        "siblings": [
            {
                "rfilename": "train.parquet",
                "size": 300,
                "lfs": {"oid": "b" * 64, "size": 300},
            }
        ],
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
    assert str(verified.profile.assets[0].download_url) == (
        "https://huggingface.co/datasets/org/robot-data/resolve/def456/train.parquet"
    )
    assert verified.profile.assets[0].source_checksum is not None
    assert verified.profile.assets[0].source_checksum.value == "b" * 64


def test_hugging_face_missing_revision_fails_closed() -> None:
    payload = {
        "id": "org/robot-data",
        "description": "Dataset columns contain robot torque time-series signals.",
        "cardData": {"license": "apache-2.0"},
        "siblings": [{"rfilename": "train.parquet", "size": 300}],
    }
    verifier = NativeVerifier(
        settings(),
        httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload))),
        validate_dns=False,
    )
    candidate = DatasetCandidate(
        id="ds_999999999999",
        name="HF robot signals",
        canonical_url="https://huggingface.co/datasets/org/robot-data",
        source_kind=SourceKind.HUGGING_FACE,
    )

    with pytest.raises(SourceUnavailable, match="immutable revision"):
        verifier.verify(candidate)


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

import sqlite3
from uuid import uuid4

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from data_sourcing.adapters.discovery import SearchBatch
from data_sourcing.adapters.native import NativeDocument, NativeFile, build_verified_candidate
from data_sourcing.config import Settings
from data_sourcing.graph import DatasetScoutGraph, initial_state
from data_sourcing.models import (
    CreateSourcingRun,
    ExecutionMode,
    RunStatus,
    SearchHypothesis,
    SearchResult,
)


def build_graph() -> tuple[DatasetScoutGraph, sqlite3.Connection]:
    connection = sqlite3.connect(":memory:", check_same_thread=False)
    saver = SqliteSaver(connection)
    saver.setup()
    graph = DatasetScoutGraph(Settings(_env_file=None), saver)
    return graph, connection


def test_evidence_complete_run_interrupts_then_resumes_to_manifest() -> None:
    scout, connection = build_graph()
    run_id = str(uuid4())
    request = CreateSourcingRun(
        brief=(
            "Find robot collision and intentional contact time-series torque data sampled at 1 kHz."
        )
    )
    config = {"configurable": {"thread_id": run_id}}

    paused = scout.graph.invoke(
        initial_state(run_id, request, allow_cached_demo=True),
        config,
    )

    assert paused["status"] == RunStatus.AWAITING_APPROVAL.value
    assert paused["recommended_candidate_id"]
    snapshot = scout.graph.get_state(config)
    assert snapshot.next == ("approval",)

    completed = scout.graph.invoke(
        Command(
            resume={
                "decision": "APPROVE",
                "candidateId": paused["recommended_candidate_id"],
            }
        ),
        config,
    )

    assert completed["status"] == RunStatus.APPROVED.value
    assert completed["manifest"]["candidate_id"] == paused["recommended_candidate_id"]
    assert any("batch_count" in item for item in completed["manifest"]["limitations"])
    scout.close()
    connection.close()


def test_rejection_feedback_runs_a_bounded_refinement_then_pauses_again() -> None:
    scout, connection = build_graph()
    run_id = str(uuid4())
    config = {"configurable": {"thread_id": run_id}}
    paused = scout.graph.invoke(
        initial_state(
            run_id,
            CreateSourcingRun(
                brief=(
                    "Find robot collision and intentional contact time-series torque data from "
                    "https://github.com/zhang-zengjie/robot-raw-collision-signals"
                )
            ),
            allow_cached_demo=True,
        ),
        config,
    )

    refined = scout.graph.invoke(
        Command(
            resume={
                "decision": "REJECT",
                "candidateId": paused["recommended_candidate_id"],
                "note": "Prioritize datasets that include free-motion baseline recordings.",
            }
        ),
        config,
    )

    assert refined["status"] == RunStatus.AWAITING_APPROVAL.value
    assert refined["review_iterations_used"] == 1
    assert refined["review_feedback"] == [
        "Prioritize datasets that include free-motion baseline recordings."
    ]
    assert any(item["id"] == "hyp_review_refinement_1" for item in refined["hypotheses"])
    assert refined["refinement_outcomes"] == [
        {
            "iteration": 1,
            "feedback": "Prioritize datasets that include free-motion baseline recordings.",
            "query": next(
                item["query"]
                for item in refined["hypotheses"]
                if item["id"] == "hyp_review_refinement_1"
            ),
            "outcome": "RECOMMENDATION_WITHHELD",
            "rejected_candidate_id": paused["recommended_candidate_id"],
            "previous_recommended_candidate_id": paused["recommended_candidate_id"],
            "recommended_candidate_id": None,
            "new_candidate_ids": [],
            "new_evidence_ids": [],
        }
    ]
    assert refined["recommended_candidate_id"] is None
    assert refined["excluded_candidate_ids"] == [paused["recommended_candidate_id"]]
    assert "excluded by reviewer" in refined["report_markdown"]
    assert scout.graph.get_state(config).next == ("approval",)

    second_refinement = scout.graph.invoke(
        Command(
            resume={
                "decision": "REJECT",
                "note": "Prefer machine-readable CSV files.",
            }
        ),
        config,
    )
    assert second_refinement["status"] == RunStatus.NEEDS_INPUT.value
    assert second_refinement["review_iterations_used"] == 2
    scout.close()
    connection.close()


def test_refinement_prioritizes_new_results_and_records_recommendation_change() -> None:
    original_url = "https://zenodo.org/records/6461868"
    refined_url = "https://zenodo.org/records/1"

    class SequencedSearch:
        def __init__(self) -> None:
            self.calls = 0

        def search(self, query: str, *, allow_cached_demo: bool = False) -> SearchBatch:
            self.calls += 1
            url = original_url if self.calls <= 3 else refined_url
            return SearchBatch(
                results=[SearchResult(title=f"Dataset {self.calls}", url=url, query=query)],
                credits_used=2,
                execution_mode=ExecutionMode.LIVE,
            )

        def close(self) -> None:
            pass

    class CompleteVerifier:
        def verify(self, candidate, *, cached=False, max_download_bytes=25_000_000_000):
            document = NativeDocument(
                source_url=str(candidate.canonical_url),
                source_kind=candidate.source_kind,
                name=candidate.name,
                revision="v1",
                license_id="cc-by-4.0",
                text=(
                    "Collision and intentional contact torque time-series at 1 kHz. "
                    "Dataset structure documents seven joints and signal columns."
                ),
                files=[NativeFile(name="signals.csv", size=100)],
            )
            return build_verified_candidate(candidate, [document], max_download_bytes)

        def close(self) -> None:
            pass

    connection = sqlite3.connect(":memory:", check_same_thread=False)
    saver = SqliteSaver(connection)
    saver.setup()
    scout = DatasetScoutGraph(
        Settings(_env_file=None),
        saver,
        search=SequencedSearch(),
        verifier=CompleteVerifier(),
    )
    run_id = str(uuid4())
    config = {"configurable": {"thread_id": run_id}}
    paused = scout.graph.invoke(
        initial_state(
            run_id,
            CreateSourcingRun(
                brief="Find robot collision and intentional contact torque data sampled at 1 kHz."
            ),
            allow_cached_demo=False,
        ),
        config,
    )

    refined = scout.graph.invoke(
        Command(
            resume={
                "decision": "REJECT",
                "candidateId": paused["recommended_candidate_id"],
                "note": "Find another independently published dataset.",
            }
        ),
        config,
    )

    outcome = refined["refinement_outcomes"][0]
    assert paused["recommended_candidate_id"] == "ds_9be731e6fb6b"
    assert refined["recommended_candidate_id"] == "ds_7d0f10684c2e"
    assert outcome["outcome"] == "RECOMMENDATION_CHANGED"
    assert outcome["rejected_candidate_id"] == "ds_9be731e6fb6b"
    assert refined["excluded_candidate_ids"] == ["ds_9be731e6fb6b"]
    assert outcome["previous_recommended_candidate_id"] == "ds_9be731e6fb6b"
    assert outcome["recommended_candidate_id"] == "ds_7d0f10684c2e"
    assert outcome["new_candidate_ids"] == ["ds_7d0f10684c2e"]
    assert outcome["new_evidence_ids"]
    scout.close()
    connection.close()


def test_reviewer_can_approve_an_alternate_eligible_candidate(monkeypatch) -> None:
    scout, connection = build_graph()
    run_id = str(uuid4())
    state = scout.graph.invoke(
        initial_state(
            run_id,
            CreateSourcingRun(
                brief=(
                    "Find robot collision and intentional contact time-series torque data from "
                    "https://github.com/zhang-zengjie/robot-raw-collision-signals"
                )
            ),
            allow_cached_demo=True,
        ),
        {"configurable": {"thread_id": run_id}},
    )
    recommended_id = state["recommended_candidate_id"]
    alternate_id = "ds_aaaaaaaaaaaa"
    alternate_profile = dict(state["profiles"][0], candidate_id=alternate_id)
    alternate_assessment = dict(state["assessments"][0], candidate_id=alternate_id)
    alternate_evidence = [
        dict(item, id=f"ev_{index:016x}", candidate_id=alternate_id)
        for index, item in enumerate(state["evidence"], start=1)
    ]
    state["profiles"] = [*state["profiles"], alternate_profile]
    state["assessments"] = [*state["assessments"], alternate_assessment]
    state["evidence"] = [*state["evidence"], *alternate_evidence]
    monkeypatch.setattr(
        "data_sourcing.graph.interrupt",
        lambda _: {"decision": "APPROVE", "candidateId": alternate_id},
    )

    approval_update = scout.approval(state)
    manifest_update = scout.manifest_generation(state | approval_update)

    assert recommended_id != alternate_id
    assert approval_update["approved_candidate_id"] == alternate_id
    assert manifest_update["manifest"]["candidate_id"] == alternate_id
    scout.close()
    connection.close()


def test_unresolved_free_motion_label_uses_two_gap_queries_and_abstains() -> None:
    scout, connection = build_graph()
    run_id = str(uuid4())
    request = CreateSourcingRun(
        brief="Find robot collision, contact, and free-motion torque time-series datasets."
    )

    result = scout.graph.invoke(
        initial_state(run_id, request, allow_cached_demo=True),
        {"configurable": {"thread_id": run_id}},
    )

    assert result["status"] == RunStatus.NEEDS_INPUT.value
    assert result["gap_queries_used"] == 2
    assert len(result["hypotheses"]) == 5
    assert result["recommended_candidate_id"] is None
    assert "req_task_labels" in result["report_markdown"]
    scout.close()
    connection.close()


def test_unknown_task_labels_are_mandatory_input_not_vacuous_success() -> None:
    scout, connection = build_graph()
    run_id = str(uuid4())
    request = CreateSourcingRun(
        brief="Find a useful public industrial robot dataset for a future analysis task."
    )

    result = scout.graph.invoke(
        initial_state(run_id, request, allow_cached_demo=False),
        {"configurable": {"thread_id": run_id}},
    )

    assert result["status"] == RunStatus.NEEDS_INPUT.value
    task_requirement = next(
        item for item in result["requirements"] if item["id"] == "req_task_labels"
    )
    assert task_requirement["status"] == "MISSING"
    scout.close()
    connection.close()


def test_mixed_live_and_cached_discovery_is_marked_partial() -> None:
    class SequencedSearch:
        def __init__(self) -> None:
            self.calls = 0

        def search(self, query: str, *, allow_cached_demo: bool = False) -> SearchBatch:
            self.calls += 1
            return SearchBatch(
                results=[
                    SearchResult(
                        title=f"Result {self.calls}",
                        url=f"https://zenodo.org/records/{self.calls}",
                        query=query,
                    )
                ],
                credits_used=2 if self.calls == 1 else 0,
                execution_mode=(ExecutionMode.LIVE if self.calls == 1 else ExecutionMode.CACHED),
                warnings=[] if self.calls == 1 else ["cache fallback"],
            )

        def close(self) -> None:
            pass

    connection = sqlite3.connect(":memory:", check_same_thread=False)
    saver = SqliteSaver(connection)
    search = SequencedSearch()
    scout = DatasetScoutGraph(Settings(_env_file=None), saver, search=search)
    state = initial_state(
        str(uuid4()),
        CreateSourcingRun(brief="Find robot collision telemetry from public datasets."),
        allow_cached_demo=True,
    )
    hypotheses = [
        SearchHypothesis(id="hyp_one", rationale="first", query="robot collision one"),
        SearchHypothesis(id="hyp_two", rationale="second", query="robot collision two"),
    ]
    state["hypotheses"] = [item.model_dump(mode="json") for item in hypotheses]

    update = scout._perform_searches(state, hypotheses)

    assert update["execution_mode"] == ExecutionMode.PARTIAL.value
    assert "cache fallback" in update["errors"]
    scout.close()
    connection.close()


def test_query_budget_is_three_initial_plus_two_gap_searches() -> None:
    class EmptySearch:
        def __init__(self) -> None:
            self.calls = 0

        def search(self, query: str, *, allow_cached_demo: bool = False) -> SearchBatch:
            self.calls += 1
            return SearchBatch(
                results=[],
                credits_used=2,
                execution_mode=ExecutionMode.LIVE,
            )

        def close(self) -> None:
            pass

    connection = sqlite3.connect(":memory:", check_same_thread=False)
    saver = SqliteSaver(connection)
    search = EmptySearch()
    scout = DatasetScoutGraph(Settings(_env_file=None), saver, search=search)
    run_id = str(uuid4())

    result = scout.graph.invoke(
        initial_state(
            run_id,
            CreateSourcingRun(
                brief="Find public robot collision torque time-series datasets for training."
            ),
            allow_cached_demo=False,
        ),
        {"configurable": {"thread_id": run_id}},
    )

    assert search.calls == 5
    assert result["tavily_credits_used"] == 10
    assert result["gap_queries_used"] == 2
    assert result["status"] == RunStatus.NEEDS_INPUT.value
    scout.close()
    connection.close()

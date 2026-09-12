import sqlite3
from uuid import uuid4

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from data_sourcing.adapters.discovery import SearchBatch
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

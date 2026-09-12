import sqlite3
from uuid import uuid4

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from data_sourcing.config import Settings
from data_sourcing.graph import DatasetScoutGraph, initial_state
from data_sourcing.models import CreateSourcingRun, RunStatus


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

from data_sourcing.config import Settings
from data_sourcing.models import CreateSourcingRun, RequirementCategory
from data_sourcing.planning import RequirementPlanner, deterministic_draft, make_gap_hypothesis


def request(brief: str) -> CreateSourcingRun:
    return CreateSourcingRun(brief=brief)


def test_deterministic_extraction_keeps_internal_fault_distinct() -> None:
    draft = deterministic_draft(
        request("Find 1 kHz robot collision and contact time series for observability.")
    )

    assert draft.task_labels == ["collision", "contact"]
    assert "internal mechanical fault" not in draft.task_labels
    assert draft.minimum_sample_rate_hz == 1_000


def test_planner_always_adds_hard_gate_requirements() -> None:
    planner = RequirementPlanner(Settings(_env_file=None))

    requirements = planner.requirements(request("Find robot collision and free-motion telemetry."))

    assert {item.category for item in requirements} >= {
        RequirementCategory.PROVENANCE,
        RequirementCategory.LICENSE,
        RequirementCategory.MODALITY,
        RequirementCategory.TASK_LABELS,
        RequirementCategory.SCHEMA,
        RequirementCategory.ACQUISITION,
    }
    task = next(item for item in requirements if item.category is RequirementCategory.TASK_LABELS)
    assert task.expected_values == ["collision", "free"]


def test_planner_produces_exactly_three_initial_hypotheses() -> None:
    planner = RequirementPlanner(Settings(_env_file=None))

    hypotheses = planner.hypotheses(request("Find robot collision telemetry with joint torque."))

    assert len(hypotheses) == 3
    assert all(not item.is_gap_query for item in hypotheses)


def test_gap_hypothesis_is_bounded_and_marks_itself() -> None:
    planner = RequirementPlanner(Settings(_env_file=None))
    requirements = planner.requirements(request("Find robot collision telemetry datasets."))

    hypothesis = make_gap_hypothesis(1, requirements[:2], "robot collision telemetry")

    assert hypothesis.id == "hyp_gap_1"
    assert hypothesis.is_gap_query is True
    assert len(hypothesis.query) <= 400

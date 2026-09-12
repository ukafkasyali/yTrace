from data_sourcing.config import Settings
from data_sourcing.models import (
    CreateSourcingRun,
    RequirementCategory,
    RequirementDefinition,
    RequirementPreviewRequest,
    RequirementPriority,
)
from data_sourcing.planning import (
    PlanningDraft,
    RequirementPlanner,
    _bounded_model_draft,
    _ModelPlanningDraft,
    deterministic_draft,
    make_gap_hypothesis,
)


def request(brief: str) -> CreateSourcingRun:
    return CreateSourcingRun(brief=brief)


def test_deterministic_extraction_keeps_internal_fault_distinct() -> None:
    draft = deterministic_draft(
        request("Find 1 kHz robot collision and contact time series for observability.")
    )

    assert draft.task_labels == ["collision", "contact"]
    assert "internal mechanical fault" not in draft.task_labels
    assert draft.minimum_sample_rate_hz == 1_000


def test_cnc_brief_creates_an_explicit_domain_requirement() -> None:
    planner = RequirementPlanner(Settings(_env_file=None))
    cnc_request = request(
        "Find a dataset of CNC machines where the head makes accidental contact."
    )

    draft = deterministic_draft(cnc_request)
    requirements = planner.requirements(cnc_request)

    assert draft.domain_terms == ["cnc"]
    assert "cnc" in draft.search_terms
    domain = next(
        item for item in requirements if item.category is RequirementCategory.DOMAIN
    )
    assert domain.priority.value == "MUST"
    assert domain.expected_values == ["cnc"]


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


def test_time_series_requirement_is_configurable_in_preview() -> None:
    planner = RequirementPlanner(Settings(_env_file=None))

    preview = planner.preview(
        RequirementPreviewRequest(
            brief="Find public robot collision telemetry for observability analysis."
        )
    )

    time_series = next(item for item in preview.requirements if item.id == "req_time_series")
    assert time_series.category is RequirementCategory.MODALITY
    assert time_series.priority is RequirementPriority.MUST
    assert time_series.is_system_required is False


def test_preview_omits_an_empty_task_label_requirement_and_adds_custom_text() -> None:
    planner = RequirementPlanner(Settings(_env_file=None))

    preview = planner.preview(
        RequirementPreviewRequest(
            brief="Find a useful public industrial dataset for future analysis.",
            custom_requirements=["Must include ambient temperature measurements"],
        )
    )

    assert not any(
        item.category is RequirementCategory.TASK_LABELS for item in preview.requirements
    )
    custom = next(
        item for item in preview.requirements if item.category is RequirementCategory.OTHER
    )
    assert custom.priority.value == "MUST"
    assert custom.expected_values == ["Must include ambient temperature measurements"]
    assert custom.id.startswith("req_custom_")


def test_custom_requirement_ids_are_stable_and_deduplicated() -> None:
    planner = RequirementPlanner(Settings(_env_file=None))
    request = RequirementPreviewRequest(
        brief="Find public robot collision data for model training.",
        custom_requirements=["At least 100 labelled events", "At least 100 labelled events"],
    )

    first = planner.preview(request)
    second = planner.preview(request)
    first_custom = [
        item for item in first.requirements if item.category is RequirementCategory.OTHER
    ]
    second_custom = [
        item for item in second.requirements if item.category is RequirementCategory.OTHER
    ]

    assert len(first_custom) == 1
    assert [item.id for item in first_custom] == [item.id for item in second_custom]


def test_confirmed_client_requirement_cannot_claim_system_status() -> None:
    planner = RequirementPlanner(Settings(_env_file=None))
    confirmed = planner.confirm_requirements(
        [
            RequirementDefinition(
                id="req_custom_spoofed",
                label="Custom requirement",
                description="A client-defined rule.",
                priority=RequirementPriority.MUST,
                category=RequirementCategory.OTHER,
                expected_values=["A supported custom property"],
                is_system_required=True,
            )
        ]
    )

    custom = next(item for item in confirmed if item.category is RequirementCategory.OTHER)
    assert custom.is_system_required is False


def test_confirmed_client_requirement_cannot_reuse_a_system_id() -> None:
    planner = RequirementPlanner(Settings(_env_file=None))
    confirmed = planner.confirm_requirements(
        [
            RequirementDefinition(
                id="req_provenance",
                label="Disguised custom rule",
                description="Attempts to collide with a fixed requirement.",
                priority=RequirementPriority.SHOULD,
                category=RequirementCategory.OTHER,
                expected_values=["Ignore canonical provenance"],
            )
        ]
    )

    assert [item.id for item in confirmed].count("req_provenance") == 1
    provenance = next(item for item in confirmed if item.id == "req_provenance")
    assert provenance.category is RequirementCategory.PROVENANCE
    assert provenance.is_system_required is True


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


def test_model_cannot_infer_internal_fault_from_collision_brief(monkeypatch) -> None:
    planner = RequirementPlanner(Settings(_env_file=None))
    monkeypatch.setattr(
        planner,
        "_model_draft",
        lambda _: PlanningDraft(task_labels=["collision", "internal mechanical fault"]),
    )

    draft = planner.draft(request("Find robot collision telemetry for contact observability."))

    assert "collision" in draft.task_labels
    assert "internal mechanical fault" not in draft.task_labels


def test_model_domain_terms_are_kept_only_when_grounded_in_the_brief(monkeypatch) -> None:
    planner = RequirementPlanner(Settings(_env_file=None))
    monkeypatch.setattr(
        planner,
        "_model_draft",
        lambda _: PlanningDraft(domain_terms=["hydraulic press", "wind turbine"]),
    )

    draft = planner.draft(
        request("Find datasets from hydraulic presses where the tool head makes contact.")
    )

    assert "hydraulic press" in draft.domain_terms
    assert "wind turbine" not in draft.domain_terms


def test_model_draft_is_normalized_to_deterministic_bounds() -> None:
    raw = _ModelPlanningDraft(
        task_labels=[f"label-{index}" for index in range(10)],
        domain_terms=[f"domain-{index}" for index in range(8)],
        minimum_sample_rate_hz=1_000,
        modality_terms=[f"modality-{index}" for index in range(10)],
        search_terms=[f"term-{index}" for index in range(14)],
    )

    bounded = _bounded_model_draft(raw)

    assert bounded.task_labels == [f"label-{index}" for index in range(8)]
    assert bounded.domain_terms == [f"domain-{index}" for index in range(6)]
    assert bounded.modality_terms == [f"modality-{index}" for index in range(8)]
    assert bounded.search_terms == [f"term-{index}" for index in range(12)]


def test_merged_draft_is_normalized_to_deterministic_bounds(monkeypatch) -> None:
    planner = RequirementPlanner(Settings(_env_file=None))
    monkeypatch.setattr(
        planner,
        "_model_draft",
        lambda _: PlanningDraft(
            task_labels=[f"label-{index}" for index in range(8)],
            modality_terms=[f"modality-{index}" for index in range(8)],
            search_terms=[f"term-{index}" for index in range(12)],
        ),
    )

    draft = planner.draft(
        request("Find 1 kHz robot collision and contact time series with joint torque.")
    )

    assert draft.task_labels[:2] == ["collision", "contact"]
    assert len(draft.task_labels) == 8
    assert len(draft.modality_terms) == 8
    assert len(draft.search_terms) == 12


def test_model_label_variants_are_normalized_to_task_classes(monkeypatch) -> None:
    planner = RequirementPlanner(Settings(_env_file=None))
    monkeypatch.setattr(
        planner,
        "_model_draft",
        lambda _: PlanningDraft(
            task_labels=["collision", "intentional contact", "free from contacts"]
        ),
    )

    draft = planner.draft(
        request("Find collision, intentional contact, and free-motion telemetry.")
    )

    assert draft.task_labels == ["collision", "contact", "free"]

from robot_observability.metrics import evaluate_rows, parse_answer


def test_parse_last_answer_object() -> None:
    text = 'Evidence: stable. Answer: {"contact":false,"onset_ms":null}'
    assert parse_answer(text) == {"contact": False, "onset_ms": None}


def test_metrics_include_invalid_outputs() -> None:
    rows = [
        {"target": {"contact": True}, "output": 'Answer: {"contact":true}'},
        {"target": {"contact": False}, "output": "not json"},
    ]
    metrics = evaluate_rows(rows)
    assert metrics["parse_validity"] == 0.5
    assert metrics["schema_exact_match"] == 0.5
    assert metrics["answer_exact_match"] == 0.5
    assert metrics["contact_accuracy"] == 0.5
    assert metrics["contact_n"] == 2


def test_semantics_macro_f1_uses_only_declared_classes() -> None:
    rows = [
        {"target": {"event_type": "free"}, "prediction": {"event_type": "free"}},
        {
            "target": {"event_type": "intentional"},
            "prediction": {"event_type": "intentional"},
        },
        {"target": {"event_type": "accidental"}, "prediction": None},
    ]
    metrics = evaluate_rows(rows)
    assert metrics["semantics_macro_f1"] == 2 / 3
    assert metrics["semantics_n"] == 3
    assert metrics["schema_exact_match"] == 2 / 3
    assert metrics["answer_exact_match"] == 2 / 3


def test_schema_and_value_fit_are_distinct() -> None:
    rows = [
        {
            "target": {"contact": True},
            "prediction": {"contact": False},
        },
        {
            "target": {"contact": True},
            "prediction": {"contact": True, "onset_ms": 400},
        },
    ]
    metrics = evaluate_rows(rows)
    assert metrics["schema_exact_match"] == 0.5
    assert metrics["answer_exact_match"] == 0.0


def test_affected_joint_and_evidence_interval_metrics() -> None:
    rows = [
        {
            "target": {
                "affected_joints": ["J2", "J4"],
                "evidence_start_ms": 300,
                "evidence_end_ms": 500,
            },
            "prediction": {
                "affected_joints": ["J2", "J3"],
                "evidence_start_ms": 320,
                "evidence_end_ms": 520,
            },
        }
    ]
    metrics = evaluate_rows(rows)
    assert metrics["affected_joints_exact_match"] == 0.0
    assert metrics["affected_joints_set_f1"] == 0.5
    assert metrics["evidence_interval_coverage"] == 1.0
    assert metrics["evidence_start_mae_ms"] == 20.0
    assert metrics["evidence_end_mae_ms"] == 20.0
    assert metrics["evidence_interval_iou"] == 180 / 220

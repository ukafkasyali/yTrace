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
    assert metrics["contact_accuracy"] == 0.5

import json

import numpy as np
import pytest

from robot_observability.plot_baseline import (
    _balanced_example_rows,
    _wandb_run_id,
    build_prompt,
    example_diagnostics,
    load_resumable_rows,
    select_locked_subset,
)


def _records(counts: dict[str, int]) -> list[dict[str, object]]:
    return [
        {
            "record_id": f"{event_type}-{index}",
            "session_id": f"session-{event_type}-{index}",
            "event_type": event_type,
        }
        for event_type, count in counts.items()
        for index in range(count)
    ]


def test_locked_subset_matches_trained_model_evaluator() -> None:
    records = _records({"free": 10, "intentional": 10, "accidental": 10})
    selected = select_locked_subset(records, 12, 42)
    expected = sorted(np.random.default_rng(42).choice(30, size=12, replace=False).tolist())
    assert [item["dataset_index"] for item in selected] == expected
    assert [item["record_id"] for item in selected] == [records[index]["record_id"] for index in expected]


def test_locked_subset_rejects_duplicate_record_ids() -> None:
    records = _records({"free": 1, "intentional": 1, "accidental": 1})
    records.append(dict(records[0]))
    with pytest.raises(ValueError, match="Duplicate record_id"):
        select_locked_subset(records, 3, 42)


def test_prompt_contains_schema_but_accepts_no_example_metadata() -> None:
    prompt = build_prompt()
    assert "contact, event_type, onset_ms" in prompt
    assert "not measurements from this example" in prompt
    assert "accidental-17" not in prompt


def test_resume_repairs_only_truncated_final_line(tmp_path) -> None:
    path = tmp_path / "predictions.jsonl"
    first = {"record_id": "a", "target": {}, "output": "x"}
    path.write_text(json.dumps(first) + "\n{", encoding="utf-8")
    assert load_resumable_rows(path, {"a", "b"}) == {"a": first}
    assert path.read_text(encoding="utf-8") == json.dumps(first, sort_keys=True) + "\n"


def test_resume_rejects_record_outside_manifest(tmp_path) -> None:
    path = tmp_path / "predictions.jsonl"
    path.write_text(json.dumps({"record_id": "wrong"}) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="not in the locked manifest"):
        load_resumable_rows(path, {"expected"})


def test_wandb_examples_are_balanced() -> None:
    rows = [
        {"target": {"event_type": event_type}, "record_id": f"{event_type}-{index}"}
        for event_type in ("free", "intentional", "accidental")
        for index in range(4)
    ]
    chosen = _balanced_example_rows(rows, 6)
    assert [row["target"]["event_type"] for row in chosen] == [
        "free",
        "intentional",
        "accidental",
        "free",
        "intentional",
        "accidental",
    ]


def test_example_diagnostics_expose_task_failures() -> None:
    diagnostics = example_diagnostics(
        {
            "target": {
                "contact": True,
                "event_type": "accidental",
                "strongest_joint": "J3",
                "onset_ms": 400,
            },
            "prediction": {
                "contact": True,
                "event_type": "intentional",
                "strongest_joint": "J3",
                "onset_ms": 425,
            },
        }
    )
    assert diagnostics == {
        "contact_correct": True,
        "semantics_correct": False,
        "strongest_joint_correct": True,
        "onset_abs_error_ms": 25.0,
    }


def test_wandb_run_id_falls_back_when_generate_id_was_removed() -> None:
    class ModernWandb:
        class util:
            pass

    run_id = _wandb_run_id(ModernWandb)
    assert len(run_id) == 8
    assert all(character in "0123456789abcdef" for character in run_id)

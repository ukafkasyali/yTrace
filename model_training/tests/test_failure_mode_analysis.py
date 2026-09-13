import gzip
import importlib.util
import json
from pathlib import Path

SCRIPT = Path(__file__).parents[1] / "scripts" / "analyze_failure_modes.py"
SPEC = importlib.util.spec_from_file_location("analyze_failure_modes", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def test_analyzer_catches_dead_selection_and_late_task_regression(tmp_path: Path) -> None:
    run = tmp_path / "run"
    run.mkdir()
    (run / "status.json").write_text(
        json.dumps({"state": "complete", "global_step": 200, "elapsed_seconds": 3600}), encoding="utf-8"
    )
    (run / "run_manifest.json").write_text(
        json.dumps(
            {
                "config": {
                    "checkpoint_selection": {"gates": {"first_pass/schema_exact_match": 0.95}},
                    "experiment": {"hypothesis": "focused_three_intent_curriculum"},
                    "training": {"curriculum_intents": "all"},
                },
                "training_intent_counts": {f"intent-{index}": 1 for index in range(7)},
            }
        ),
        encoding="utf-8",
    )
    common = {
        "event": "generation_eval",
        "contact_f1": 1.0,
        "semantics_macro_f1": 1.0,
        "affected_joints_set_f1": 1.0,
        "onset_within_50ms": 1.0,
        "evidence_interval_iou": 1.0,
        "schema_exact_match": 1.0,
        "retry_rate": 0.0,
        "rationale_presence": 1.0,
        "n": 1,
    }
    events = [
        {**common, "step": 100, "strongest_joint_accuracy": 1.0, "first_pass/schema_exact_match": 0.9},
        {"event": "grounding_checkpoint_selection", "step": 100, "score": 1.0, "eligible": False},
        {"event": "validation_check", "step": 100, "loss": 0.2},
        {**common, "step": 200, "strongest_joint_accuracy": 0.5, "first_pass/schema_exact_match": 0.9},
        {"event": "grounding_checkpoint_selection", "step": 200, "score": 0.75, "eligible": False},
        {"event": "validation_check", "step": 200, "loss": 0.1},
        {"event": "zero_signal_eval", "step": 200, "prediction_change_rate": 1.0},
    ]
    write_jsonl(run / "metrics.jsonl", events)
    write_jsonl(
        run / "generation_step_step_000200.jsonl",
        [
            {
                "record_id": "r1",
                "intent": "strongest_joint",
                "target": {"strongest_joint": "J1"},
                "prediction": {"strongest_joint": "J2"},
                "first_pass_output": 'Rationale: J2 dominates.\nAnswer: {"strongest_joint":"J2"}',
                "output": 'Rationale: J2 dominates.\nAnswer: {"strongest_joint":"J2"}',
                "retry_used": False,
            }
        ],
    )

    report = MODULE.analyze_run(run)
    codes = {finding["code"] for finding in report["findings"]}
    assert "checkpoint_gate_deadlock" in codes
    assert "late_decoded_regression" in codes
    assert "manifest_hypothesis_mismatch" in codes
    assert report["checkpoint_selection"]["best_observed_decoded_step"] == 100
    assert report["final_validation"]["row_analysis"]["failure_counts"]["strongest_joint_error"] == 1


def test_row_analysis_separates_schema_retry_and_temporal_failures() -> None:
    rows = [
        {
            "record_id": "r1",
            "intent": "onset",
            "target": {"onset_ms": 400},
            "prediction": {"onset_ms": 470},
            "first_pass_output": "",
            "output": 'Rationale: Evidence begins at 470 ms.\nAnswer: {"onset_ms":470}',
            "retry_used": True,
        },
        {
            "record_id": "r2",
            "intent": "affected_joints",
            "target": {"affected_joints": ["J1"]},
            "prediction": {"affected_joints": ["affected_joints"]},
            "first_pass_output": 'Answer: {"affected_joints":["affected_joints"]}',
            "output": 'Answer: {"affected_joints":["affected_joints"]}',
            "retry_used": False,
        },
    ]

    analysis = MODULE.analyze_rows(rows)
    assert analysis["failure_counts"]["onset_error_over_50ms"] == 1
    assert analysis["failure_counts"]["retry_required"] == 1
    assert analysis["failure_counts"]["schema_value_violation"] == 1
    assert analysis["failure_counts"]["missing_rationale"] == 1
    assert analysis["per_intent"]["affected_joints"]["schema_validity"] == 0.0


def test_read_jsonl_accepts_compressed_receipts(tmp_path: Path) -> None:
    path = tmp_path / "metrics.jsonl.gz"
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        handle.write('{"event":"generation_eval","step":1}\n')

    assert MODULE.read_jsonl(path) == [{"event": "generation_eval", "step": 1}]

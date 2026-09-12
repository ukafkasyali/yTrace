import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).parents[1] / "scripts" / "report_fit_probe.py"


def _write_run(run_dir: Path, state: str) -> None:
    run_dir.mkdir()
    events = [
        {
            "event": "training_probe_eval",
            "step": 0,
            "loss_fraction_of_initial": 1.0,
            "relative_signal_loss_gap": 0.0,
            "parse_validity": 0.0,
            "schema_exact_match": 0.0,
            "answer_exact_match": 0.0,
        },
        {
            "event": "training_probe_eval",
            "step": 320,
            "loss_fraction_of_initial": 0.1,
            "relative_signal_loss_gap": 0.2,
            "matched_relative_generalization_gap": 0.3,
            "parse_validity": 1.0,
            "schema_exact_match": 1.0,
            "answer_exact_match": 0.9,
            "intent/contact/answer_exact_match": 0.9,
        },
        {
            "event": "training_probe_zero_signal_eval",
            "step": 320,
            "prediction_change_rate": 0.8,
        },
        {
            "event": "training_probe_signal_ablation",
            "step": 320,
            "delta/contact_accuracy": 0.5,
        },
    ]
    (run_dir / "metrics.jsonl").write_text(
        "".join(json.dumps(event) + "\n" for event in events), encoding="utf-8"
    )
    (run_dir / "status.json").write_text(json.dumps({"state": state}), encoding="utf-8")


def test_fit_probe_report_passes_only_a_complete_grounded_run(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    _write_run(run_dir, "complete")
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--run-dir", str(run_dir), "--strict"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert json.loads(result.stdout)["verdict"] == "pass"

    (run_dir / "status.json").write_text(json.dumps({"state": "training"}), encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--run-dir", str(run_dir), "--strict"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2
    assert json.loads(result.stdout)["checks"]["run_complete"] is False

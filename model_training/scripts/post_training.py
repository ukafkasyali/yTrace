"""Wait for a training tmux session, then run locked test and grounding checks."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path


def write_status(path: Path, **payload: object) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def tmux_session_exists(name: str) -> bool:
    result = subprocess.run(
        ["tmux", "has-session", "-t", name],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return result.returncode == 0


def numeric_metrics(path: Path, prefix: str) -> dict[str, float | int]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {
        f"{prefix}/{key}": value
        for key, value in payload.items()
        if isinstance(value, (int, float)) and not isinstance(value, bool)
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--training-session", default="train-full")
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--prepared-root", type=Path, default=Path("data/prepared/v1"))
    parser.add_argument("--evaluation-output", type=Path, required=True)
    parser.add_argument("--zero-signal-output", type=Path)
    parser.add_argument("--sanity-output", type=Path, required=True)
    parser.add_argument("--test-samples", type=int, default=512)
    parser.add_argument("--poll-seconds", type=int, default=60)
    parser.add_argument("--wandb-run-id")
    parser.add_argument("--wandb-project", default="robot-observability")
    args = parser.parse_args()

    status_path = args.run_dir / "post_training_status.json"
    write_status(status_path, state="waiting_for_training", session=args.training_session)
    while tmux_session_exists(args.training_session):
        time.sleep(args.poll_seconds)

    training_status = json.loads((args.run_dir / "status.json").read_text(encoding="utf-8"))
    if training_status.get("state") != "complete":
        write_status(status_path, state="blocked", training_status=training_status)
        raise RuntimeError(f"Training did not complete successfully: {training_status}")

    checkpoint = args.run_dir / "best_model.pt"
    zero_signal_output = args.zero_signal_output or Path(f"{args.evaluation_output}-zero-signal")
    write_status(status_path, state="evaluating", checkpoint=str(checkpoint))
    try:
        subprocess.run(
            [
                sys.executable,
                "scripts/evaluate_opentslm.py",
                "--checkpoint",
                str(checkpoint),
                "--prepared-root",
                str(args.prepared_root),
                "--output",
                str(args.evaluation_output),
                "--samples",
                str(args.test_samples),
                "--batch-size",
                "4",
            ],
            check=True,
        )
        write_status(status_path, state="evaluating_zero_signal", checkpoint=str(checkpoint))
        subprocess.run(
            [
                sys.executable,
                "scripts/evaluate_opentslm.py",
                "--checkpoint",
                str(checkpoint),
                "--prepared-root",
                str(args.prepared_root),
                "--output",
                str(zero_signal_output),
                "--samples",
                str(args.test_samples),
                "--batch-size",
                "4",
                "--zero-signal",
            ],
            check=True,
        )
        write_status(status_path, state="checking_grounding", checkpoint=str(checkpoint))
        subprocess.run(
            [
                sys.executable,
                "scripts/sanity_check_equivariance.py",
                "--checkpoint",
                str(checkpoint),
                "--prepared-root",
                str(args.prepared_root),
                "--output",
                str(args.sanity_output),
            ],
            check=True,
        )
        combined_metrics = {
            **numeric_metrics(args.evaluation_output / "metrics.json", "test"),
            **numeric_metrics(zero_signal_output / "metrics.json", "test_zero_signal"),
            **numeric_metrics(args.sanity_output / "metrics.json", "sanity"),
        }
        if args.wandb_run_id:
            try:
                import wandb

                run = wandb.init(
                    project=args.wandb_project,
                    id=args.wandb_run_id,
                    resume="must",
                    job_type="evaluation",
                )
                run.summary.update(combined_metrics)
                run.finish()
            except Exception as error:  # noqa: BLE001 - local evaluation remains authoritative
                combined_metrics["wandb_error"] = repr(error)
        write_status(status_path, state="complete", metrics=combined_metrics)
    except Exception as error:
        write_status(status_path, state="failed", error=repr(error))
        raise


if __name__ == "__main__":
    main()

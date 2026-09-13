"""Train and evaluate the multi-task 1D CNN baseline."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from robot_observability.cnn_baseline import train_cnn


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=Path("configs/cnn_1d.yaml"))
    parser.add_argument("--prepared-root", type=Path, default=Path("data/prepared/v1"))
    parser.add_argument("--output", type=Path, default=Path("runs"))
    parser.add_argument("--run-name", required=True)
    parser.add_argument("--seed", type=int, default=20260912)
    parser.add_argument("--allow-cpu", action="store_true")
    args = parser.parse_args()
    run_root = args.output / args.run_name
    try:
        report = train_cnn(
            args.prepared_root,
            run_root,
            args.config,
            seed=args.seed,
            allow_cpu=args.allow_cpu,
        )
    except Exception as error:
        if run_root.exists():
            temporary = run_root / "status.tmp"
            temporary.write_text(
                json.dumps({"state": "failed", "error": repr(error)}, indent=2) + "\n",
                encoding="utf-8",
            )
            temporary.replace(run_root / "status.json")
        raise
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

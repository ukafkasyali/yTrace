from __future__ import annotations

import argparse
import json
from pathlib import Path

from robot_observability.plot_baseline import evaluate_plot_vlm


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepared-root", type=Path, default=Path("data/prepared/v1"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--endpoint", default="http://127.0.0.1:8000")
    parser.add_argument("--model", default="Qwen/Qwen3-VL-4B-Instruct")
    parser.add_argument("--limit", type=int, default=512)
    args = parser.parse_args()
    print(
        json.dumps(
            evaluate_plot_vlm(
                args.prepared_root, args.output, endpoint=args.endpoint, model=args.model, limit=args.limit
            ),
            indent=2,
        )
    )


if __name__ == "__main__":
    main()

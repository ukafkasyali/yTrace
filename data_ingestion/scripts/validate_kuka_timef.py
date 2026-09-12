"""Validate persisted KUKA Part I TimeF output against DatasetProfile."""

from __future__ import annotations

import argparse
from pathlib import Path

from dataset_profiler.timef_validation import validate_kuka_timef


def main() -> int:
    """Run structured validation and return a process status."""
    parser = argparse.ArgumentParser()
    parser.add_argument("registry", type=Path)
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = validate_kuka_timef(args.registry, args.profile, args.source)
    rendered = report.to_json() + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())

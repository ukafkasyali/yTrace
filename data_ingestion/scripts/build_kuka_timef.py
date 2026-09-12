"""Build KUKA Part I directly into a TimeF registry."""

from __future__ import annotations

import argparse
from pathlib import Path

from dataset_profiler.timenet import build_kuka_collision_part1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("registry", type=Path)
    args = parser.parse_args()
    print(build_kuka_collision_part1(args.source, args.registry))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

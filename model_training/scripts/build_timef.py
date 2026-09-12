"""Build the connector into a local TimeF registry."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from timenet.engine import run_pipeline

from robot_observability.timenet_connector import CONNECTOR


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--cache", type=Path)
    parser.add_argument("--raw-root", type=Path, default=Path("data/raw"))
    args = parser.parse_args()
    os.environ["ROBOT_COLLISION_RAW_ROOT"] = str(args.raw_root.resolve())
    version = run_pipeline(CONNECTOR(), args.registry, cache_dir=args.cache, keep_cache=True)
    print(version)


if __name__ == "__main__":
    main()

"""Build one KUKA Part I or Part II batch into a TimeF registry."""

from __future__ import annotations

import argparse
from pathlib import Path

from dataset_profiler.timenet import build_kuka_timef_dataset


_PART_DATASET_IDS = {
    "part1": "kuka/collision-part1",
    "part2": "kuka/contact-part2",
}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Convert one extracted KUKA Part I or Part II batch into a TimeF registry."
    )
    parser.add_argument("source", type=Path, help="Extracted Part I or Part II batch/root directory")
    parser.add_argument("registry", type=Path, help="Target TimeF registry directory")
    parser.add_argument(
        "--part",
        choices=sorted(_PART_DATASET_IDS),
        required=True,
        help="Dataset part; conversion is intentionally one part at a time",
    )
    args = parser.parse_args()
    dataset_id = _PART_DATASET_IDS[args.part]
    version_dir = build_kuka_timef_dataset(dataset_id, args.source, args.registry)
    print(f"Built {dataset_id}: {version_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

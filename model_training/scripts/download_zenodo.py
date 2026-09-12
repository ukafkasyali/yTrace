"""Resumably download and verify both raw Zenodo records.

The script intentionally uses curl for robust range-resume behavior and emits
JSONL state suitable for `tail -f` during an overnight transfer.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from robot_observability.data.zenodo import download_raw_corpus


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--keep-archives", action="store_true")
    args = parser.parse_args()
    download_raw_corpus(args.output, workers=args.workers, keep_archives=args.keep_archives)


if __name__ == "__main__":
    main()

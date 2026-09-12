"""Build KUKA Part II directly into a TimeF registry."""

from __future__ import annotations

import argparse
from pathlib import Path

from dataset_profiler.timenet import build_kuka_contact_part2


parser = argparse.ArgumentParser()
parser.add_argument("source", type=Path)
parser.add_argument("registry", type=Path)
args = parser.parse_args()
print(build_kuka_contact_part2(args.source, args.registry))

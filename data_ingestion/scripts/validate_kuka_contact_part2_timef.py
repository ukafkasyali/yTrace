"""Validate persisted KUKA Part II TimeF output against DatasetProfile."""

from __future__ import annotations

import argparse
from pathlib import Path

from dataset_profiler.timef_validation import validate_kuka_contact_part2


parser = argparse.ArgumentParser()
parser.add_argument("registry", type=Path)
parser.add_argument("profile", type=Path)
parser.add_argument("--source", required=True, type=Path)
parser.add_argument("--output", type=Path)
args = parser.parse_args()

report = validate_kuka_contact_part2(args.registry, args.profile, args.source)
payload = report.to_json()
if args.output:
    args.output.write_text(payload + "\n", encoding="utf-8")
print(payload)
raise SystemExit(0 if report.passed else 1)

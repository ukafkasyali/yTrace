"""Prove whether two prepared corpora contain identical model inputs and labels."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from itertools import zip_longest
from pathlib import Path

import numpy as np

_DISPLAY_STAT_FIELDS = {"raw_mean_nm", "raw_std_nm", "raw_rms_nm"}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def compare_arrays(reference: Path, candidate: Path, chunk_rows: int) -> dict[str, object]:
    left = np.load(reference, mmap_mode="r")
    right = np.load(candidate, mmap_mode="r")
    shape_equal = left.shape == right.shape
    dtype_equal = left.dtype == right.dtype
    exact = shape_equal and dtype_equal
    max_abs_difference = 0.0
    if shape_equal:
        for start in range(0, len(left), chunk_rows):
            stop = min(start + chunk_rows, len(left))
            left_chunk = np.asarray(left[start:stop])
            right_chunk = np.asarray(right[start:stop])
            exact = exact and np.array_equal(left_chunk, right_chunk)
            if left_chunk.size:
                difference = float(
                    np.max(np.abs(left_chunk.astype(np.float64) - right_chunk.astype(np.float64)))
                )
                max_abs_difference = max(max_abs_difference, difference)
    return {
        "shape_equal": shape_equal,
        "reference_shape": list(left.shape),
        "candidate_shape": list(right.shape),
        "dtype_equal": dtype_equal,
        "reference_dtype": str(left.dtype),
        "candidate_dtype": str(right.dtype),
        "exact_values": exact,
        "max_abs_difference": max_abs_difference if shape_equal else None,
        "reference_sha256": sha256_file(reference),
        "candidate_sha256": sha256_file(candidate),
    }


def compare_records(reference: Path, candidate: Path) -> dict[str, object]:
    """Compare model metadata exactly and isolate non-model reduction roundoff."""
    reference_hash = sha256_file(reference)
    candidate_hash = sha256_file(candidate)
    model_metadata_exact = True
    row_count = 0
    differing_model_fields: Counter[str] = Counter()
    differing_display_fields: Counter[str] = Counter()
    display_stats_max_abs_difference = 0.0
    with reference.open(encoding="utf-8") as left, candidate.open(encoding="utf-8") as right:
        for left_line, right_line in zip_longest(left, right):
            row_count += 1
            if left_line is None or right_line is None:
                model_metadata_exact = False
                differing_model_fields["row_count"] += 1
                continue
            left_row = json.loads(left_line)
            right_row = json.loads(right_line)
            for key in set(left_row) | set(right_row):
                if left_row.get(key) == right_row.get(key):
                    continue
                if key not in _DISPLAY_STAT_FIELDS:
                    model_metadata_exact = False
                    differing_model_fields[key] += 1
                    continue
                differing_display_fields[key] += 1
                left_values = np.asarray(left_row.get(key), dtype=np.float64)
                right_values = np.asarray(right_row.get(key), dtype=np.float64)
                if left_values.shape == right_values.shape and left_values.size:
                    display_stats_max_abs_difference = max(
                        display_stats_max_abs_difference,
                        float(np.max(np.abs(left_values - right_values))),
                    )
                else:
                    display_stats_max_abs_difference = float("inf")
    return {
        "raw_file_exact": reference_hash == candidate_hash,
        "model_metadata_exact": model_metadata_exact,
        "rows_compared": row_count,
        "differing_model_fields": dict(differing_model_fields),
        "differing_display_fields": dict(differing_display_fields),
        "display_stats_max_abs_difference": display_stats_max_abs_difference,
        "reference_sha256": reference_hash,
        "candidate_sha256": candidate_hash,
    }


def audit(reference: Path, candidate: Path, chunk_rows: int = 128) -> dict[str, object]:
    file_checks: dict[str, dict[str, object]] = {}
    for relative in ("splits.json", "normalization.json"):
        reference_path = reference / relative
        candidate_path = candidate / relative
        left_hash = sha256_file(reference_path)
        right_hash = sha256_file(candidate_path)
        file_checks[relative] = {
            "exact": left_hash == right_hash,
            "reference_sha256": left_hash,
            "candidate_sha256": right_hash,
        }
    for split in ("train", "validation", "test"):
        records = f"{split}/records.jsonl"
        file_checks[records] = compare_records(reference / records, candidate / records)
        signals = f"{split}/signals.npy"
        file_checks[signals] = compare_arrays(reference / signals, candidate / signals, chunk_rows)
    passed = all(
        bool(
            check.get(
                "exact",
                check.get("exact_values", check.get("model_metadata_exact", False)),
            )
        )
        for check in file_checks.values()
    )
    return {
        "passed": passed,
        "interpretation": (
            "A pass means TimeF ingestion exactly preserved model tensors, labels, recording-group "
            "splits, and train-only normalization. UI-only floating-point reduction statistics are "
            "reported separately and do not enter model prompts or targets."
        ),
        "reference": str(reference.resolve()),
        "candidate": str(candidate.resolve()),
        "checks": file_checks,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--chunk-rows", type=int, default=128)
    args = parser.parse_args()
    report = audit(args.reference, args.candidate, args.chunk_rows)
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    if not report["passed"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()

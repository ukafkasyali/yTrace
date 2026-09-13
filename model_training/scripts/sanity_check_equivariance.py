"""Verify that predictions respond to temporal shifts and channel permutations."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

import numpy as np
import torch
from opentslm.model.llm.OpenTSLMSP import OpenTSLMSP

from robot_observability.constants import JOINT_NAMES
from robot_observability.metrics import parse_answer
from robot_observability.opentslm_dataset import RobotQADataset


def generate(model: OpenTSLMSP, sample: dict[str, object]) -> tuple[str, dict[str, object] | None]:
    with torch.no_grad():
        output = model.generate([sample], max_new_tokens=128, do_sample=False)[0]
    return output, parse_answer(output)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--prepared-root", type=Path, default=Path("data/prepared/v1"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--samples", type=int, default=12)
    parser.add_argument("--shift-ms", type=int, default=64)
    parser.add_argument("--seed", type=int, default=20260912)
    parser.add_argument(
        "--output-format",
        choices=("answer_only", "answer_then_evidence", "rationale_then_answer"),
        default="rationale_then_answer",
    )
    args = parser.parse_args()

    model = OpenTSLMSP(llm_id="meta-llama/Llama-3.2-1B", device="cuda")
    model.enable_lora()
    model.load_from_file(str(args.checkpoint))
    model.eval()
    dataset = RobotQADataset(
        args.prepared_root,
        "validation",
        mode="summary",
        output_format=args.output_format,
    )
    rng = np.random.default_rng(args.seed)
    candidate_indices: dict[str, list[int]] = {"accidental": [], "intentional": []}
    seen_sessions: dict[str, set[str]] = {"accidental": set(), "intentional": set()}
    for index, metadata in enumerate(dataset.prepared.records):
        label = str(metadata["event_type"])
        session = str(metadata["session_id"])
        if label in candidate_indices and session not in seen_sessions[label]:
            candidate_indices[label].append(index)
            seen_sessions[label].add(session)
    selected_indices = []
    for class_index, label in enumerate(("accidental", "intentional")):
        requested = args.samples // 2 + (class_index < args.samples % 2)
        selected_indices.extend(rng.choice(candidate_indices[label], size=requested, replace=False).tolist())
    selected = [dataset[index] for index in sorted(selected_indices)]
    rows = []
    for sample in selected:
        base_output, base = generate(model, sample)
        shifted = dict(sample)
        shifted_signal = torch.zeros_like(sample["time_series"])
        shifted_signal[:, args.shift_ms :] = sample["time_series"][:, : -args.shift_ms]
        shifted["time_series"] = shifted_signal
        shifted_output, shifted_prediction = generate(model, shifted)

        strongest = str(sample["metadata"]["strongest_joint"])
        source_index = JOINT_NAMES.index(strongest)
        target_index = (source_index + 1) % len(JOINT_NAMES)
        permuted = dict(sample)
        permuted_signal = sample["time_series"].clone()
        temporary = permuted_signal[source_index].clone()
        permuted_signal[source_index] = permuted_signal[target_index]
        permuted_signal[target_index] = temporary
        permuted["time_series"] = permuted_signal
        permuted_output, permuted_prediction = generate(model, permuted)
        expected_permuted_joint = JOINT_NAMES[target_index]

        row = {
            "record_id": sample["record_id"],
            "session_id": sample["metadata"]["session_id"],
            "event_type": sample["metadata"]["event_type"],
            "target_onset_ms": sample["metadata"]["onset_sample"],
            "target_joint": strongest,
            "expected_permuted_joint": expected_permuted_joint,
            "base": base,
            "shifted": shifted_prediction,
            "permuted": permuted_prediction,
            "outputs": {"base": base_output, "shifted": shifted_output, "permuted": permuted_output},
        }
        rows.append(row)

    base_onset_errors = []
    shift_errors = []
    shift_errors_base_within_50ms = []
    base_joint_hits = []
    permutation_hits = []
    permutation_hits_base_correct = []
    shift_changes = []
    permutation_changes = []
    for row in rows:
        base, shifted, permuted = row["base"], row["shifted"], row["permuted"]
        if base and shifted and base.get("onset_ms") is not None and shifted.get("onset_ms") is not None:
            base_error = abs(float(base["onset_ms"]) - float(row["target_onset_ms"]))
            delta_error = abs((float(shifted["onset_ms"]) - float(base["onset_ms"])) - args.shift_ms)
            base_onset_errors.append(base_error)
            shift_errors.append(delta_error)
            if base_error <= 50:
                shift_errors_base_within_50ms.append(delta_error)
            shift_changes.append(shifted.get("onset_ms") != base.get("onset_ms"))
        if permuted:
            base_correct = bool(base and base.get("strongest_joint") == row["target_joint"])
            hit = permuted.get("strongest_joint") == row["expected_permuted_joint"]
            base_joint_hits.append(base_correct)
            permutation_hits.append(hit)
            if base_correct:
                permutation_hits_base_correct.append(hit)
            permutation_changes.append(
                bool(base and permuted.get("strongest_joint") != base.get("strongest_joint"))
            )
    summary = {
        "samples": len(rows),
        "sessions": len({str(row["session_id"]) for row in rows}),
        "event_type_counts": dict(Counter(str(row["event_type"]) for row in rows)),
        "shift_ms": args.shift_ms,
        "output_format": args.output_format,
        "base_onset_coverage": len(base_onset_errors) / len(rows) if rows else 0.0,
        "base_onset_mae_ms": float(np.mean(base_onset_errors)) if base_onset_errors else None,
        "shift_equivariance_mae_ms": float(np.mean(shift_errors)) if shift_errors else None,
        "shift_equivariance_mae_base_within_50ms": (
            float(np.mean(shift_errors_base_within_50ms)) if shift_errors_base_within_50ms else None
        ),
        "shift_prediction_change_rate": float(np.mean(shift_changes)) if shift_changes else None,
        "base_strongest_joint_accuracy": float(np.mean(base_joint_hits)) if base_joint_hits else None,
        "channel_permutation_accuracy": float(np.mean(permutation_hits)) if permutation_hits else None,
        "channel_permutation_accuracy_base_correct": (
            float(np.mean(permutation_hits_base_correct)) if permutation_hits_base_correct else None
        ),
        "channel_prediction_change_rate": (
            float(np.mean(permutation_changes)) if permutation_changes else None
        ),
    }
    args.output.mkdir(parents=True, exist_ok=False)
    (args.output / "rows.jsonl").write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows), encoding="utf-8"
    )
    (args.output / "metrics.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()

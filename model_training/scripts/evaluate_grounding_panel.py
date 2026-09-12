"""Evaluate a checkpoint on the fixed validation-only grounding panel."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from robot_observability.opentslm_dataset import RobotQADataset
from robot_observability.train_opentslm import generation_eval, stratified_grounding_subset


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--prepared-root", type=Path, default=Path("data/prepared/timef-v1"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model-id", default="meta-llama/Llama-3.2-1B")
    parser.add_argument("--samples", type=int, default=48)
    parser.add_argument("--batch-size", type=int, default=4)
    parser.add_argument("--seed", type=int, default=20260912)
    args = parser.parse_args()

    from opentslm.model.llm.OpenTSLMSP import OpenTSLMSP

    if args.output.exists():
        raise FileExistsError(f"Refusing to overwrite evaluation directory: {args.output}")
    args.output.mkdir(parents=True)

    model = OpenTSLMSP(llm_id=args.model_id, device="cuda")
    model.enable_lora()
    model.load_from_file(str(args.checkpoint))
    dataset = RobotQADataset(
        args.prepared_root,
        "validation",
        mode="summary",
        seed=args.seed,
        output_format="rationale_then_answer",
    )
    panel = stratified_grounding_subset(dataset, min(args.samples, len(dataset)), args.seed + 3)
    metrics, rows = generation_eval(
        model,
        panel,
        args.output / "predictions.jsonl",
        batch_size=args.batch_size,
    )
    report = {
        "checkpoint": str(args.checkpoint.resolve()),
        "split": "validation",
        "selection": "fixed free-plus-J1-J7 panel, onset-quintile spread within joint",
        "seed": args.seed,
        "requested_samples": args.samples,
        "joint_counts": dict(Counter(str(row["target"].get("strongest_joint") or "free") for row in rows)),
        "event_counts": dict(Counter(str(row["target"]["event_type"]) for row in rows)),
        **metrics,
    }
    (args.output / "metrics.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

"""Evaluate a checkpoint on the fixed validation-only grounding panel."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from robot_observability.metrics import evaluate_rows
from robot_observability.opentslm_dataset import RobotQADataset
from robot_observability.train_opentslm import (
    generation_eval,
    stratified_grounding_subset,
    stratified_summary_subset,
    stratified_training_probe_subset,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--prepared-root", type=Path, default=Path("data/prepared/timef-v1"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model-id", default="meta-llama/Llama-3.2-1B")
    parser.add_argument("--samples", type=int, default=48)
    parser.add_argument("--batch-size", type=int, default=4)
    parser.add_argument("--seed", type=int, default=20260912)
    parser.add_argument("--reuse-predictions", action="store_true")
    parser.add_argument(
        "--output-format",
        choices=("answer_only", "answer_then_evidence", "rationale_then_answer"),
        default="rationale_then_answer",
    )
    parser.add_argument("--prompt-set", choices=("train", "heldout"), default="train")
    parser.add_argument(
        "--selection",
        choices=("event-intent-stratified", "event-stratified", "joint-stratified"),
        default="event-stratified",
    )
    args = parser.parse_args()

    if args.output.exists() and not args.reuse_predictions:
        raise FileExistsError(f"Refusing to overwrite evaluation directory: {args.output}")
    args.output.mkdir(parents=True, exist_ok=args.reuse_predictions)
    dataset = RobotQADataset(
        args.prepared_root,
        "validation",
        mode="all_intents" if args.selection == "event-intent-stratified" else "summary",
        seed=args.seed,
        output_format=args.output_format,
        prompt_set=args.prompt_set,
    )
    selection_size = min(args.samples, len(dataset))
    if args.selection == "event-intent-stratified":
        panel = stratified_training_probe_subset(dataset, selection_size, args.seed + 3)
        selection_description = "fixed event-by-intent stratified panel with natural joint prevalence"
    elif args.selection == "joint-stratified":
        panel = stratified_grounding_subset(dataset, selection_size, args.seed + 3)
        selection_description = "fixed free-plus-J1-J7 panel, onset-quintile spread within joint"
    else:
        panel = stratified_summary_subset(dataset, selection_size, args.seed + 3)
        selection_description = "fixed event-stratified panel with natural joint prevalence"
    predictions_path = args.output / "predictions.jsonl"
    if args.reuse_predictions:
        rows = [json.loads(line) for line in predictions_path.read_text(encoding="utf-8").splitlines()]
        if len(rows) != len(panel):
            raise ValueError(f"Prediction receipt has {len(rows)} rows but the frozen panel has {len(panel)}")
        metrics = evaluate_rows(rows)
    else:
        from opentslm.model.llm.OpenTSLMSP import OpenTSLMSP

        model = OpenTSLMSP(llm_id=args.model_id, device="cuda")
        model.enable_lora()
        model.load_from_file(str(args.checkpoint))
        metrics, rows = generation_eval(
            model,
            panel,
            predictions_path,
            batch_size=args.batch_size,
        )
    panel_metadata = [panel[index]["metadata"] for index in range(len(panel))]
    report = {
        "checkpoint": str(args.checkpoint.resolve()),
        "split": "validation",
        "selection": selection_description,
        "seed": args.seed,
        "output_format": args.output_format,
        "prompt_set": args.prompt_set,
        "requested_samples": args.samples,
        "joint_counts": dict(
            Counter(str(metadata.get("strongest_joint") or "free") for metadata in panel_metadata)
        ),
        "event_counts": dict(Counter(str(metadata["event_type"]) for metadata in panel_metadata)),
        "intent_counts": dict(Counter(str(row["intent"]) for row in rows)),
        **metrics,
    }
    (args.output / "metrics.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

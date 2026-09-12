"""Evaluate a trained OpenTSLM adapter on a deterministic held-out subset."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

import numpy as np
import torch
from torch.utils.data import DataLoader, Dataset, Subset

from robot_observability.metrics import evaluate_rows, parse_answer
from robot_observability.opentslm_dataset import RobotQADataset
from robot_observability.qa import answer_payload


def collate(batch: list[dict[str, object]]) -> list[dict[str, object]]:
    return batch


def fixed_subset(dataset: Dataset, size: int, seed: int) -> Dataset:
    if size >= len(dataset):
        return dataset
    rng = np.random.default_rng(seed)
    indices = sorted(rng.choice(len(dataset), size=size, replace=False).tolist())
    return Subset(dataset, indices)


class ZeroSignalDataset:
    def __init__(self, dataset: Dataset) -> None:
        self.dataset = dataset

    def __len__(self) -> int:
        return len(self.dataset)

    def __getitem__(self, index: int) -> dict[str, object]:
        sample = dict(self.dataset[index])
        sample["time_series"] = torch.zeros_like(sample["time_series"])
        return sample


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--prepared-root", type=Path, default=Path("data/prepared/v1"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model-id", default="meta-llama/Llama-3.2-1B")
    parser.add_argument("--split", choices=("validation", "test"), default="test")
    parser.add_argument("--samples", type=int, default=512)
    parser.add_argument("--batch-size", type=int, default=1)
    parser.add_argument("--seed", type=int, default=20260912)
    parser.add_argument("--zero-signal", action="store_true")
    args = parser.parse_args()

    from opentslm.model.llm.OpenTSLMSP import OpenTSLMSP

    if args.output.exists():
        raise FileExistsError(f"Refusing to overwrite evaluation directory: {args.output}")
    args.output.mkdir(parents=True)

    model = OpenTSLMSP(llm_id=args.model_id, device="cuda")
    model.enable_lora()
    model.load_from_file(str(args.checkpoint))
    model.eval()
    full_dataset = RobotQADataset(args.prepared_root, args.split, mode="summary")
    selected_dataset = fixed_subset(full_dataset, min(args.samples, len(full_dataset)), args.seed)
    dataset: Dataset = ZeroSignalDataset(selected_dataset) if args.zero_signal else selected_dataset
    loader = DataLoader(
        dataset,
        batch_size=args.batch_size,
        shuffle=False,
        collate_fn=collate,
        num_workers=0,
    )

    rows = []
    predictions_path = args.output / "predictions.jsonl"
    with predictions_path.open("w", encoding="utf-8") as handle, torch.no_grad():
        for batch in loader:
            outputs = model.generate(batch, max_new_tokens=180, do_sample=False)
            for sample, output in zip(batch, outputs):
                row = {
                    "record_id": sample["record_id"],
                    "intent": sample["intent"],
                    "target": answer_payload(sample["metadata"], sample["intent"]),
                    "output": output,
                    "prediction": parse_answer(output),
                }
                rows.append(row)
                handle.write(json.dumps(row, sort_keys=True) + "\n")

    metrics = evaluate_rows(rows)
    report = {
        "checkpoint": str(args.checkpoint.resolve()),
        "split": args.split,
        "selection": "fixed random subset without replacement",
        "input_condition": "zero_signal" if args.zero_signal else "real_signal",
        "target_class_counts": dict(Counter(str(row["target"]["event_type"]) for row in rows)),
        "seed": args.seed,
        "requested_samples": args.samples,
        **metrics,
    }
    (args.output / "metrics.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

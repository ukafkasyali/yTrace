"""Compare ordinary and premature-EOS-blocked decoding on a trained adapter."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from opentslm.model.llm.OpenTSLMSP import OpenTSLMSP

from robot_observability.metrics import parse_answer
from robot_observability.opentslm_dataset import RobotQADataset


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--prepared-root", type=Path, default=Path("data/prepared/v1"))
    parser.add_argument("--samples-per-class", type=int, default=4)
    parser.add_argument("--min-new-tokens", type=int, default=16)
    parser.add_argument("--record-id", action="append", default=[])
    args = parser.parse_args()

    model = OpenTSLMSP(llm_id="meta-llama/Llama-3.2-1B", device="cuda")
    model.enable_lora()
    model.load_from_file(str(args.checkpoint))
    model.eval()
    dataset = RobotQADataset(args.prepared_root, "test", mode="summary")
    selected = []
    if args.record_id:
        requested = set(args.record_id)
        selected = [
            dataset[index] for index in range(len(dataset)) if dataset[index]["record_id"] in requested
        ]
        missing = requested - {str(sample["record_id"]) for sample in selected}
        if missing:
            raise ValueError(f"Unknown record IDs: {sorted(missing)}")
    else:
        counts = {"free": 0, "intentional": 0, "accidental": 0}
        for index in range(len(dataset)):
            sample = dataset[index]
            event_type = str(sample["metadata"]["event_type"])
            if counts[event_type] < args.samples_per_class:
                selected.append(sample)
                counts[event_type] += 1
            if all(count == args.samples_per_class for count in counts.values()):
                break

    ordinary = model.generate(selected, max_new_tokens=128, do_sample=False)
    guarded = model.generate(
        selected,
        max_new_tokens=128,
        min_new_tokens=args.min_new_tokens,
        do_sample=False,
    )
    for sample, ordinary_output, guarded_output in zip(selected, ordinary, guarded):
        print(
            json.dumps(
                {
                    "record_id": sample["record_id"],
                    "event_type": sample["metadata"]["event_type"],
                    "ordinary": ordinary_output,
                    "ordinary_prediction": parse_answer(ordinary_output),
                    "guarded": guarded_output,
                    "guarded_prediction": parse_answer(guarded_output),
                },
                sort_keys=True,
            ),
            flush=True,
        )


if __name__ == "__main__":
    main()

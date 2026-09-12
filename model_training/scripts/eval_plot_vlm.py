from __future__ import annotations

import argparse
import json
from pathlib import Path

import yaml

from robot_observability.plot_baseline import (
    InferenceSettings,
    PlotSettings,
    evaluate_plot_vlm,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate a zero-shot plot VLM on a locked test subset")
    parser.add_argument("--config", type=Path, default=Path("configs/qwen3_vl.yaml"))
    parser.add_argument("--prepared-root", type=Path, default=Path("data/prepared/v1"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--endpoint")
    parser.add_argument("--model")
    parser.add_argument("--model-revision")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--selection-seed", type=int)
    parser.add_argument("--quantization")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--wandb-mode", choices=("online", "offline", "disabled"))
    args = parser.parse_args()

    config = yaml.safe_load(args.config.read_text(encoding="utf-8"))
    evaluation = config["evaluation"]
    inference_config = config["inference"]
    plot_config = config["plot"]
    wandb_config = config.get("wandb", {})
    if evaluation.get("split") != "test":
        raise ValueError("The locked zero-shot benchmark must use evaluation.split: test")
    model = args.model or config["model"]
    if args.model and args.model != config["model"] and not args.model_revision:
        raise ValueError("Overriding --model also requires --model-revision for provenance")
    metrics = evaluate_plot_vlm(
        args.prepared_root,
        args.output,
        endpoint=args.endpoint or config["endpoint"],
        model=model,
        model_revision=args.model_revision or config.get("model_revision"),
        limit=args.limit if args.limit is not None else int(evaluation["samples"]),
        selection_seed=(
            args.selection_seed if args.selection_seed is not None else int(evaluation["selection_seed"])
        ),
        quantization=args.quantization or str(config.get("quantization", "none")),
        resume=args.resume,
        plot=PlotSettings(**plot_config),
        inference=InferenceSettings(**inference_config),
        wandb_project=wandb_config.get("project"),
        wandb_entity=wandb_config.get("entity"),
        wandb_run_name=wandb_config.get("run_name"),
        wandb_mode=args.wandb_mode or wandb_config.get("mode", "disabled"),
        wandb_table_examples=int(wandb_config.get("table_examples", 48)),
        log_every=int(wandb_config.get("log_every", 10)),
    )
    print(json.dumps(metrics, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

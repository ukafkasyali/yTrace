"""Observable single-H100 OpenTSLM SoftPrompt fine-tuning loop."""

from __future__ import annotations

import argparse
import json
import math
import os
import platform
import random
import time
from collections import Counter
from contextlib import nullcontext
from pathlib import Path

import numpy as np
import torch
import yaml
from torch.utils.data import DataLoader, Dataset, Subset
from torch.utils.tensorboard import SummaryWriter

from robot_observability.constants import OPENTSLM_COMMIT, TIMENET_COMMIT
from robot_observability.metrics import evaluate_rows, parse_answer
from robot_observability.opentslm_dataset import RobotQADataset
from robot_observability.qa import answer_payload


def emit(path: Path, event: str, **fields: object) -> None:
    payload = {"timestamp": time.time(), "event": event, **fields}
    line = json.dumps(payload, sort_keys=True)
    print(line, flush=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def write_status(path: Path, **payload: object) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def collate(batch: list[dict[str, object]]) -> list[dict[str, object]]:
    return batch


def fixed_subset(dataset: Dataset, size: int, seed: int) -> Dataset:
    if size >= len(dataset):
        return dataset
    rng = np.random.default_rng(seed)
    return Subset(dataset, sorted(rng.choice(len(dataset), size=size, replace=False).tolist()))


def stratified_summary_subset(dataset: RobotQADataset, size: int, seed: int) -> Dataset:
    if dataset.mode != "summary":
        raise ValueError("Stratified generation canary requires summary mode")
    groups: dict[str, list[int]] = {}
    for index, record in enumerate(dataset.prepared.records):
        groups.setdefault(str(record["event_type"]), []).append(index)
    rng = np.random.default_rng(seed)
    classes = sorted(groups)
    selected = []
    for class_index, label in enumerate(classes):
        requested = size // len(classes) + (class_index < size % len(classes))
        requested = min(requested, len(groups[label]))
        selected.extend(rng.choice(groups[label], size=requested, replace=False).tolist())
    return Subset(dataset, sorted(selected))


def load_model(config: dict[str, object], device: str):
    from opentslm import OpenTSLM
    from opentslm.model.llm.OpenTSLMSP import OpenTSLMSP

    warm_start = config.get("warm_start")
    if warm_start:
        return OpenTSLM.load_pretrained(str(warm_start), device=device, enable_lora=True)
    model = OpenTSLMSP(llm_id=str(config["base_model"]), device=device)
    lora = config["lora"]
    model.enable_lora(
        lora_r=int(lora["rank"]),
        lora_alpha=int(lora["alpha"]),
        lora_dropout=float(lora["dropout"]),
    )
    return model


def optimizer_for(model, config: dict[str, object]) -> torch.optim.Optimizer:
    rates = config["learning_rates"]
    groups = [
        {"params": [p for p in model.encoder.parameters() if p.requires_grad], "lr": float(rates["encoder"])},
        {
            "params": [p for p in model.projector.parameters() if p.requires_grad],
            "lr": float(rates["projector"]),
        },
        {"params": model.get_lora_parameters(), "lr": float(rates["lora"])},
    ]
    return torch.optim.AdamW(groups, weight_decay=0.01)


def scheduler_for(optimizer: torch.optim.Optimizer, total_steps: int, warmup_fraction: float = 0.03):
    warmup = max(1, round(total_steps * warmup_fraction))

    def factor(step: int) -> float:
        if step < warmup:
            return step / warmup
        return max(0.0, (total_steps - step) / max(1, total_steps - warmup))

    return torch.optim.lr_scheduler.LambdaLR(optimizer, factor)


@torch.no_grad()
def mean_loss(model, loader: DataLoader, device_type: str) -> float:
    model.eval()
    losses = []
    context = (
        torch.autocast(device_type="cuda", dtype=torch.bfloat16) if device_type == "cuda" else nullcontext()
    )
    with context:
        for batch in loader:
            losses.append(float(model.compute_loss(batch).detach().cpu()))
    return float(np.mean(losses)) if losses else math.nan


@torch.no_grad()
def generation_eval(
    model, dataset: Dataset, output_path: Path, batch_size: int = 2
) -> tuple[dict[str, float | int], list[dict[str, object]]]:
    model.eval()
    rows = []
    loader = DataLoader(dataset, batch_size=batch_size, shuffle=False, collate_fn=collate)
    with output_path.open("w", encoding="utf-8") as handle:
        for batch in loader:
            outputs = model.generate(batch, max_new_tokens=128, do_sample=False)
            for sample, output in zip(batch, outputs):
                metadata = sample["metadata"]
                row = {
                    "record_id": sample["record_id"],
                    "intent": sample["intent"],
                    "target": answer_payload(metadata, sample["intent"]),
                    "output": output,
                    "prediction": parse_answer(output),
                }
                rows.append(row)
                handle.write(json.dumps(row, sort_keys=True) + "\n")
    return evaluate_rows(rows), rows


class ZeroSignalDataset:
    def __init__(self, dataset: Dataset) -> None:
        self.dataset = dataset

    def __len__(self) -> int:
        return len(self.dataset)

    def __getitem__(self, index: int) -> dict[str, object]:
        sample = dict(self.dataset[index])
        sample["time_series"] = torch.zeros_like(sample["time_series"])
        return sample


def stratified_subset(dataset: Dataset, size: int, seed: int) -> Dataset:
    groups: dict[str, list[int]] = {}
    for index in range(len(dataset)):
        sample = dataset[index]
        groups.setdefault(str(sample["metadata"]["event_type"]), []).append(index)
    rng = np.random.default_rng(seed)
    classes = sorted(groups)
    selected = []
    for class_index, label in enumerate(classes):
        requested = size // len(classes) + (class_index < size % len(classes))
        selected.extend(rng.choice(groups[label], size=requested, replace=False).tolist())
    return Subset(dataset, sorted(selected))


def run(args: argparse.Namespace) -> None:
    config = yaml.safe_load(args.config.read_text(encoding="utf-8"))
    run_root = args.output / args.run_name
    run_root.mkdir(parents=True, exist_ok=False)
    metrics_path = run_root / "metrics.jsonl"
    status_path = run_root / "status.json"
    writer = SummaryWriter(run_root / "tensorboard")
    write_status(status_path, state="initializing", run_name=args.run_name)
    wandb_run = None
    wandb_config = config["observability"].get("wandb", {})
    if wandb_config.get("enabled", False):
        try:
            import wandb

            wandb_run = wandb.init(
                project=str(wandb_config.get("project", "robot-observability")),
                entity=os.environ.get("WANDB_ENTITY") or wandb_config.get("entity"),
                name=args.run_name,
                config=config,
                dir=str(run_root),
                tags=["opentslm", "robotics", "soft-prompt", "smoke" if args.smoke else "full"],
            )
            wandb_run.define_metric("trainer/global_step")
            for namespace in ("train/*", "validation/*", "validation_zero_signal/*"):
                wandb_run.define_metric(namespace, step_metric="trainer/global_step")
        except Exception as error:  # noqa: BLE001 - observability must not abort training
            emit(metrics_path, "wandb_unavailable", error=repr(error))

    seed = int(args.seed)
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)
    device = "cuda" if torch.cuda.is_available() else "cpu"
    if device != "cuda" and not args.allow_cpu:
        raise RuntimeError("CUDA is required unless --allow-cpu is explicitly supplied")
    model = load_model(config, device)
    eos = model.get_eos_token() or ""
    train_dataset: Dataset = RobotQADataset(
        args.prepared_root, "train", mode="mixed", eos_token=eos, seed=seed
    )
    validation_dataset: Dataset = RobotQADataset(
        args.prepared_root, "validation", mode="all_intents", eos_token=eos, seed=seed
    )
    validation_summary_dataset: Dataset = RobotQADataset(
        args.prepared_root, "validation", mode="summary", eos_token=eos, seed=seed
    )
    if args.smoke:
        train_dataset = fixed_subset(train_dataset, 32, seed)
        validation_dataset = fixed_subset(validation_dataset, 32, seed + 1)
        validation_summary_dataset = fixed_subset(validation_summary_dataset, 32, seed + 1)
    elif config.get("training", {}).get("max_examples"):
        train_dataset = fixed_subset(
            train_dataset,
            min(len(train_dataset), int(config["training"]["max_examples"])),
            seed,
        )
    validation_loss_dataset = fixed_subset(
        validation_dataset,
        min(len(validation_dataset), int(config["validation"]["loss_subset"])),
        seed + 2,
    )
    generation_size = min(len(validation_summary_dataset), int(config["validation"]["generation_subset"]))
    generation_dataset = (
        fixed_subset(validation_summary_dataset, generation_size, seed + 3)
        if args.smoke
        else stratified_summary_subset(validation_summary_dataset, generation_size, seed + 3)
    )
    zero_signal_dataset: Dataset = ZeroSignalDataset(
        stratified_subset(
            generation_dataset,
            min(len(generation_dataset), int(config["validation"].get("zero_signal_subset", 0))),
            seed + 4,
        )
    )

    batch_size = 1 if args.smoke else int(config["batch_size"])
    validation_batch_size = 1 if args.smoke else int(config["validation"].get("batch_size", batch_size))
    accumulation = 1 if args.smoke else int(config["gradient_accumulation_steps"])
    train_loader = DataLoader(
        train_dataset, batch_size=batch_size, shuffle=True, collate_fn=collate, num_workers=0
    )
    validation_loader = DataLoader(
        validation_loss_dataset,
        batch_size=validation_batch_size,
        shuffle=False,
        collate_fn=collate,
        num_workers=0,
    )
    epochs = 20 if args.smoke else int(config["epochs"])
    steps_per_epoch = math.ceil(len(train_loader) / accumulation)
    total_steps = (
        min(args.max_steps, epochs * steps_per_epoch) if args.max_steps else epochs * steps_per_epoch
    )
    optimizer = optimizer_for(model, config)
    scheduler = scheduler_for(optimizer, total_steps)

    manifest = {
        "run_name": args.run_name,
        "config": config,
        "seed": seed,
        "smoke": args.smoke,
        "prepared_root": str(args.prepared_root.resolve()),
        "opentslm_commit": OPENTSLM_COMMIT,
        "timenet_commit": TIMENET_COMMIT,
        "python": platform.python_version(),
        "torch": torch.__version__,
        "cuda": torch.version.cuda,
        "gpu": torch.cuda.get_device_name(0) if torch.cuda.is_available() else None,
        "train_examples": len(train_dataset),
        "validation_examples": len(validation_dataset),
        "generation_canary_class_counts": dict(
            Counter(
                str(generation_dataset[index]["metadata"]["event_type"])
                for index in range(len(generation_dataset))
            )
        ),
        "command": " ".join(os.sys.argv),
    }
    (run_root / "run_manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    emit(metrics_path, "run_start", **manifest)

    # Fail early on shape, checkpoint, and forward incompatibilities.
    first_batch = next(iter(train_loader))
    with torch.no_grad():
        initial_loss = float(model.compute_loss(first_batch).detach().cpu())
    emit(metrics_path, "forward_smoke", loss=initial_loss)

    start_time = time.monotonic()
    deadline = start_time + float(config["max_wall_time_hours"]) * 3600
    global_step = 0
    best_validation = math.inf
    patience = 0
    stop_reason = "epochs_complete"
    validation_config = config["validation"]
    validation_every_steps = int(validation_config.get("every_steps", 0))

    def validate(phase: str, epoch: int, step: int) -> tuple[float, bool]:
        nonlocal best_validation
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
        write_status(
            status_path,
            state="validating",
            phase=phase,
            epoch=epoch,
            global_step=step,
            best_validation_loss=best_validation if math.isfinite(best_validation) else None,
        )
        validation_loss = mean_loss(model, validation_loader, "cuda" if device == "cuda" else "cpu")
        improved = validation_loss < best_validation
        emit(
            metrics_path,
            "validation_check",
            phase=phase,
            epoch=epoch,
            step=step,
            loss=validation_loss,
            improved=improved,
        )
        writer.add_scalar("validation/loss", validation_loss, step)
        if wandb_run is not None:
            wandb_run.log(
                {
                    "trainer/global_step": step,
                    "validation/loss": validation_loss,
                    "validation/phase": phase,
                    "epoch": epoch,
                }
            )
        if improved:
            best_validation = validation_loss
            model.store_to_file(str(run_root / "best_model.pt"))
        generation_metrics, generation_rows = generation_eval(
            model,
            generation_dataset,
            run_root / f"generation_{phase}_step_{step:06d}.jsonl",
            batch_size=validation_batch_size,
        )
        emit(
            metrics_path,
            "generation_eval",
            phase=phase,
            epoch=epoch,
            step=step,
            **generation_metrics,
        )
        if wandb_run is not None:
            wandb_run.log(
                {
                    "trainer/global_step": step,
                    **{f"validation/{key}": value for key, value in generation_metrics.items()},
                }
            )
            import wandb

            sample_table = wandb.Table(
                columns=["record_id", "target", "prediction", "output"],
                data=[
                    [
                        row["record_id"],
                        json.dumps(row["target"], sort_keys=True),
                        json.dumps(row["prediction"], sort_keys=True),
                        row["output"],
                    ]
                    for row in generation_rows[:8]
                ],
            )
            wandb_run.log({"trainer/global_step": step, "validation/samples": sample_table})
        if len(zero_signal_dataset):
            zero_metrics, zero_rows = generation_eval(
                model,
                zero_signal_dataset,
                run_root / f"generation_zero_signal_{phase}_step_{step:06d}.jsonl",
                batch_size=validation_batch_size,
            )
            real_predictions = {row["record_id"]: row["prediction"] for row in generation_rows}
            changed = [real_predictions.get(zero["record_id"]) != zero["prediction"] for zero in zero_rows]
            zero_metrics["prediction_change_rate"] = float(np.mean(changed)) if changed else 0.0
            emit(
                metrics_path,
                "zero_signal_eval",
                phase=phase,
                epoch=epoch,
                step=step,
                **zero_metrics,
            )
            if wandb_run is not None:
                wandb_run.log(
                    {
                        "trainer/global_step": step,
                        **{f"validation_zero_signal/{key}": value for key, value in zero_metrics.items()},
                    }
                )
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
        return validation_loss, improved

    if validation_config.get("at_start", False):
        validate("start", -1, 0)
    optimizer.zero_grad(set_to_none=True)
    write_status(
        status_path,
        state="training",
        global_step=0,
        best_validation_loss=best_validation if math.isfinite(best_validation) else None,
    )

    for epoch in range(epochs):
        model.train()
        epoch_losses = []
        for batch_index, batch in enumerate(train_loader):
            if time.monotonic() >= deadline:
                stop_reason = "wall_time_limit"
                break
            loss = model.compute_loss(batch) / accumulation
            loss.backward()
            epoch_losses.append(float(loss.detach().cpu()) * accumulation)
            should_step = (batch_index + 1) % accumulation == 0 or batch_index + 1 == len(train_loader)
            if not should_step:
                continue
            grad_norm = float(torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0).detach().cpu())
            optimizer.step()
            scheduler.step()
            optimizer.zero_grad(set_to_none=True)
            global_step += 1
            if global_step % int(config["observability"]["log_every_steps"]) == 0 or global_step == 1:
                elapsed = time.monotonic() - start_time
                fields = {
                    "epoch": epoch,
                    "step": global_step,
                    "loss": float(np.mean(epoch_losses[-max(1, accumulation) :])),
                    "learning_rate": scheduler.get_last_lr()[0],
                    "grad_norm": grad_norm,
                    "elapsed_seconds": elapsed,
                    "examples_per_second": (global_step * batch_size * accumulation) / max(elapsed, 1e-6),
                    "gpu_allocated_gb": torch.cuda.memory_allocated() / 2**30
                    if torch.cuda.is_available()
                    else 0,
                    "gpu_reserved_gb": torch.cuda.memory_reserved() / 2**30
                    if torch.cuda.is_available()
                    else 0,
                }
                emit(metrics_path, "train_step", **fields)
                for key, value in fields.items():
                    if isinstance(value, (int, float)):
                        writer.add_scalar(f"train/{key}", value, global_step)
                if wandb_run is not None:
                    wandb_run.log(
                        {
                            "trainer/global_step": global_step,
                            **{f"train/{key}": value for key, value in fields.items()},
                        }
                    )
                write_status(
                    status_path,
                    state="training",
                    epoch=epoch,
                    global_step=global_step,
                    best_validation_loss=best_validation if math.isfinite(best_validation) else None,
                    **{key: fields[key] for key in ("loss", "elapsed_seconds", "examples_per_second")},
                )
            if args.max_steps and global_step >= args.max_steps:
                stop_reason = "max_steps"
                break

            if validation_every_steps and global_step % validation_every_steps == 0:
                validate("step", epoch, global_step)
                model.train()
                write_status(
                    status_path,
                    state="training",
                    epoch=epoch,
                    global_step=global_step,
                    best_validation_loss=best_validation,
                )

        model.store_to_file(str(run_root / "last_model.pt"))
        _, improved = validate("epoch", epoch, global_step)
        if improved:
            patience = 0
        else:
            patience += 1
        if patience >= int(config["early_stopping_patience"]):
            stop_reason = "early_stopping"
            break
        if stop_reason != "epochs_complete":
            break

    elapsed = time.monotonic() - start_time
    writer.close()
    write_status(
        status_path,
        state="complete",
        stop_reason=stop_reason,
        global_step=global_step,
        best_validation_loss=best_validation,
        elapsed_seconds=elapsed,
    )
    emit(
        metrics_path,
        "run_complete",
        stop_reason=stop_reason,
        step=global_step,
        best_validation_loss=best_validation,
        elapsed_seconds=elapsed,
    )
    if wandb_run is not None:
        if wandb_config.get("log_adapter_artifact", True):
            import wandb

            artifact = wandb.Artifact(f"{args.run_name}-adapter", type="model")
            for name in ("best_model.pt", "run_manifest.json", "metrics.jsonl", "status.json"):
                candidate = run_root / name
                if candidate.exists():
                    artifact.add_file(str(candidate))
            wandb_run.log_artifact(artifact)
        wandb_run.summary.update(
            {
                "stop_reason": stop_reason,
                "global_step": global_step,
                "best_validation_loss": best_validation,
                "elapsed_seconds": elapsed,
            }
        )
        wandb_run.finish()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=Path("configs/opentslm_sp.yaml"))
    parser.add_argument("--prepared-root", type=Path, default=Path("data/prepared/v1"))
    parser.add_argument("--output", type=Path, default=Path("runs"))
    parser.add_argument("--run-name", required=True)
    parser.add_argument("--seed", type=int, default=20260912)
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument("--max-steps", type=int)
    parser.add_argument("--allow-cpu", action="store_true")
    args = parser.parse_args()
    try:
        run(args)
    except Exception as error:
        run_root = args.output / args.run_name
        if run_root.exists():
            write_status(
                run_root / "status.json",
                state="failed",
                run_name=args.run_name,
                error=repr(error),
            )
        raise


if __name__ == "__main__":
    main()

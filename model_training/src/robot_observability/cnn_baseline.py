"""Multi-task 1D CNN baseline for prepared KUKA torque windows."""

from __future__ import annotations

import json
import math
import os
import platform
import random
import time
from contextlib import nullcontext
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np
import torch
import yaml
from torch import nn
from torch.utils.data import DataLoader, Dataset, Subset

from robot_observability.baseline import evidence_from_prediction
from robot_observability.constants import JOINT_NAMES
from robot_observability.metrics import evaluate_rows
from robot_observability.prepared import PreparedSplit
from robot_observability.qa import answer_payload

EVENT_TYPES = ("free", "intentional", "accidental")
EVENT_TO_INDEX = {label: index for index, label in enumerate(EVENT_TYPES)}
JOINT_TO_INDEX = {label: index for index, label in enumerate(JOINT_NAMES)}


@dataclass(frozen=True)
class CnnConfig:
    channels: tuple[int, ...]
    kernel_sizes: tuple[int, ...]
    dropout: float
    batch_size: int
    epochs: int
    learning_rate: float
    weight_decay: float
    warmup_fraction: float
    early_stopping_patience: int
    precision: str
    num_workers: int
    evaluation_samples: int
    evaluation_seed: int
    class_weighting: bool
    semantics_loss_weight: float
    onset_loss_weight: float
    strongest_joint_loss_weight: float
    affected_joints_loss_weight: float
    max_grad_norm: float
    wandb_enabled: bool
    wandb_project: str

    @classmethod
    def from_yaml(cls, path: Path) -> CnnConfig:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
        model = payload["model"]
        training = payload["training"]
        evaluation = payload["evaluation"]
        losses = training["loss_weights"]
        wandb = payload.get("observability", {}).get("wandb", {})
        channels = tuple(int(value) for value in model["channels"])
        kernels = tuple(int(value) for value in model["kernel_sizes"])
        if not channels or len(channels) != len(kernels):
            raise ValueError("model.channels and model.kernel_sizes must be non-empty and equal length")
        return cls(
            channels=channels,
            kernel_sizes=kernels,
            dropout=float(model["dropout"]),
            batch_size=int(training["batch_size"]),
            epochs=int(training["epochs"]),
            learning_rate=float(training["learning_rate"]),
            weight_decay=float(training["weight_decay"]),
            warmup_fraction=float(training["warmup_fraction"]),
            early_stopping_patience=int(training["early_stopping_patience"]),
            precision=str(training["precision"]),
            num_workers=int(training["num_workers"]),
            evaluation_samples=int(evaluation["samples"]),
            evaluation_seed=int(evaluation["seed"]),
            class_weighting=bool(training["class_weighting"]),
            semantics_loss_weight=float(losses["semantics"]),
            onset_loss_weight=float(losses["onset"]),
            strongest_joint_loss_weight=float(losses["strongest_joint"]),
            affected_joints_loss_weight=float(losses["affected_joints"]),
            max_grad_norm=float(training["max_grad_norm"]),
            wandb_enabled=bool(wandb.get("enabled", False)),
            wandb_project=str(wandb.get("project", "robot-observability")),
        )


class PreparedCnnDataset(Dataset):
    """Tensor targets over the canonical memory-mapped prepared split."""

    def __init__(self, root: Path, split: str) -> None:
        self.prepared = PreparedSplit(root, split)

    def __len__(self) -> int:
        return len(self.prepared)

    def __getitem__(self, index: int) -> dict[str, Any]:
        signal, metadata = self.prepared[index]
        affected = torch.zeros(len(JOINT_NAMES), dtype=torch.float32)
        for joint in metadata["affected_joints"]:
            affected[JOINT_TO_INDEX[str(joint)]] = 1.0
        strongest = metadata["strongest_joint"]
        onset = metadata["onset_sample"]
        return {
            "signal": torch.from_numpy(np.asarray(signal, dtype=np.float32).copy()),
            "event": torch.tensor(EVENT_TO_INDEX[str(metadata["event_type"])], dtype=torch.long),
            "onset": torch.tensor(-1 if onset is None else int(onset), dtype=torch.long),
            "strongest_joint": torch.tensor(
                -1 if strongest is None else JOINT_TO_INDEX[str(strongest)], dtype=torch.long
            ),
            "affected_joints": affected,
            "index": index,
        }


class MultiTaskCnn1D(nn.Module):
    """Shared temporal encoder with semantics, timing, and joint-evidence heads."""

    def __init__(
        self,
        channels: tuple[int, ...] = (32, 64, 128),
        kernel_sizes: tuple[int, ...] = (9, 7, 5),
        dropout: float = 0.15,
    ) -> None:
        super().__init__()
        blocks: list[nn.Module] = []
        input_channels = len(JOINT_NAMES)
        for output_channels, kernel_size in zip(channels, kernel_sizes, strict=True):
            blocks.extend(
                [
                    nn.Conv1d(
                        input_channels,
                        output_channels,
                        kernel_size=kernel_size,
                        padding=kernel_size // 2,
                        bias=False,
                    ),
                    nn.BatchNorm1d(output_channels),
                    nn.GELU(),
                    nn.MaxPool1d(kernel_size=2, stride=2),
                ]
            )
            input_channels = output_channels
        self.encoder = nn.Sequential(*blocks)
        self.dropout = nn.Dropout(dropout)
        self.semantics_head = nn.Linear(input_channels, len(EVENT_TYPES))
        self.onset_head = nn.Conv1d(input_channels, 1, kernel_size=1)
        self.strongest_joint_head = nn.Linear(input_channels, len(JOINT_NAMES))
        self.affected_joints_head = nn.Linear(input_channels, len(JOINT_NAMES))

    def forward(self, signal: torch.Tensor) -> dict[str, torch.Tensor]:
        temporal = self.encoder(signal)
        pooled = self.dropout(temporal.mean(dim=-1))
        return {
            "semantics": self.semantics_head(pooled),
            "onset": self.onset_head(temporal).squeeze(1),
            "strongest_joint": self.strongest_joint_head(pooled),
            "affected_joints": self.affected_joints_head(pooled),
        }


def sample_to_bin(samples: torch.Tensor, bins: int, window_samples: int) -> torch.Tensor:
    scale = (bins - 1) / max(1, window_samples - 1)
    return torch.round(samples.float() * scale).long().clamp(0, bins - 1)


def bin_to_sample(bins: torch.Tensor, number_of_bins: int, window_samples: int) -> torch.Tensor:
    scale = (window_samples - 1) / max(1, number_of_bins - 1)
    return torch.round(bins.float() * scale).long().clamp(0, window_samples - 1)


def fixed_subset(dataset: Dataset, size: int, seed: int) -> Dataset:
    if size >= len(dataset):
        return dataset
    rng = np.random.default_rng(seed)
    indices = sorted(rng.choice(len(dataset), size=size, replace=False).tolist())
    return Subset(dataset, indices)


def _class_weights(dataset: PreparedCnnDataset) -> torch.Tensor:
    counts = np.zeros(len(EVENT_TYPES), dtype=np.float64)
    for metadata in dataset.prepared.records:
        counts[EVENT_TO_INDEX[str(metadata["event_type"])]] += 1
    if np.any(counts == 0):
        raise ValueError(f"Every event class must appear in train; observed counts: {counts.tolist()}")
    weights = len(dataset) / (len(EVENT_TYPES) * counts)
    return torch.tensor(weights, dtype=torch.float32)


def _loss(
    outputs: dict[str, torch.Tensor],
    batch: dict[str, torch.Tensor],
    config: CnnConfig,
    class_weights: torch.Tensor | None,
    window_samples: int,
) -> tuple[torch.Tensor, dict[str, float]]:
    semantics = nn.functional.cross_entropy(
        outputs["semantics"], batch["event"], weight=class_weights
    )
    event_mask = batch["event"] != EVENT_TO_INDEX["free"]
    if event_mask.any():
        onset_targets = sample_to_bin(
            batch["onset"][event_mask], outputs["onset"].shape[-1], window_samples
        )
        onset = nn.functional.cross_entropy(outputs["onset"][event_mask], onset_targets)
        strongest = nn.functional.cross_entropy(
            outputs["strongest_joint"][event_mask], batch["strongest_joint"][event_mask]
        )
        affected = nn.functional.binary_cross_entropy_with_logits(
            outputs["affected_joints"][event_mask], batch["affected_joints"][event_mask]
        )
    else:
        zero = outputs["semantics"].sum() * 0.0
        onset = strongest = affected = zero
    total = (
        config.semantics_loss_weight * semantics
        + config.onset_loss_weight * onset
        + config.strongest_joint_loss_weight * strongest
        + config.affected_joints_loss_weight * affected
    )
    parts = {
        "semantics_loss": float(semantics.detach().cpu()),
        "onset_loss": float(onset.detach().cpu()),
        "strongest_joint_loss": float(strongest.detach().cpu()),
        "affected_joints_loss": float(affected.detach().cpu()),
    }
    return total, parts


def _autocast(device: torch.device, precision: str):
    if device.type != "cuda" or precision == "float32":
        return nullcontext()
    dtype = torch.bfloat16 if precision == "bfloat16" else torch.float16
    return torch.autocast(device_type="cuda", dtype=dtype)


def _move(batch: dict[str, Any], device: torch.device) -> dict[str, Any]:
    return {
        key: value.to(device, non_blocking=True) if isinstance(value, torch.Tensor) else value
        for key, value in batch.items()
    }


def _epoch(
    model: MultiTaskCnn1D,
    loader: DataLoader,
    optimizer: torch.optim.Optimizer | None,
    device: torch.device,
    config: CnnConfig,
    class_weights: torch.Tensor | None,
    window_samples: int,
) -> tuple[float, dict[str, float]]:
    training = optimizer is not None
    model.train(training)
    loss_sum = 0.0
    examples = 0
    part_sums: dict[str, float] = {}
    for raw_batch in loader:
        batch = _move(raw_batch, device)
        if training:
            optimizer.zero_grad(set_to_none=True)
        with torch.set_grad_enabled(training), _autocast(device, config.precision):
            outputs = model(batch["signal"])
            loss, parts = _loss(outputs, batch, config, class_weights, window_samples)
        if training:
            loss.backward()
            nn.utils.clip_grad_norm_(model.parameters(), config.max_grad_norm)
            optimizer.step()
        count = int(batch["signal"].shape[0])
        loss_sum += float(loss.detach().cpu()) * count
        examples += count
        for key, value in parts.items():
            part_sums[key] = part_sums.get(key, 0.0) + value * count
    return loss_sum / max(1, examples), {
        key: value / max(1, examples) for key, value in part_sums.items()
    }


def _prediction_rows(
    model: MultiTaskCnn1D,
    dataset: Dataset,
    source: PreparedCnnDataset,
    loader: DataLoader,
    device: torch.device,
    precision: str,
    affected_thresholds: np.ndarray,
    window_samples: int,
) -> list[dict[str, object]]:
    model.eval()
    rows: list[dict[str, object]] = []
    with torch.no_grad():
        for raw_batch in loader:
            batch = _move(raw_batch, device)
            with _autocast(device, precision):
                outputs = model(batch["signal"])
            semantics_probabilities = outputs["semantics"].softmax(dim=-1).float().cpu()
            semantics_indices = semantics_probabilities.argmax(dim=-1)
            onset_bins = outputs["onset"].argmax(dim=-1)
            onset_samples = bin_to_sample(
                onset_bins, outputs["onset"].shape[-1], window_samples
            ).cpu()
            strongest_indices = outputs["strongest_joint"].argmax(dim=-1).cpu()
            affected_probabilities = outputs["affected_joints"].sigmoid().float().cpu()
            source_indices = raw_batch["index"].tolist()
            for batch_index, source_index in enumerate(source_indices):
                metadata = source.prepared.records[int(source_index)]
                predicted_event = EVENT_TYPES[int(semantics_indices[batch_index])]
                if predicted_event == "free":
                    prediction = {
                        "contact": False,
                        "event_type": "free",
                        "onset_ms": None,
                        "strongest_joint": None,
                        "affected_joints": [],
                        "evidence_start_ms": None,
                        "evidence_end_ms": None,
                    }
                else:
                    # A zero-sample onset has no pre-event baseline for evidence extraction.
                    onset = max(1, int(onset_samples[batch_index]))
                    strongest_index = int(strongest_indices[batch_index])
                    joint_probabilities = affected_probabilities[batch_index].numpy()
                    ranked = np.argsort(-joint_probabilities)
                    affected = [
                        JOINT_NAMES[int(index)]
                        for index in ranked
                        if joint_probabilities[int(index)] >= 0.5
                    ]
                    strongest_joint = JOINT_NAMES[strongest_index]
                    if strongest_joint not in affected:
                        affected.insert(0, strongest_joint)
                    signal = np.asarray(source.prepared.signals[int(source_index)])
                    signal_evidence = evidence_from_prediction(
                        signal, onset, affected_thresholds
                    )
                    prediction = {
                        "contact": True,
                        "event_type": predicted_event,
                        "onset_ms": onset,
                        "strongest_joint": strongest_joint,
                        "affected_joints": affected,
                        "evidence_start_ms": signal_evidence["evidence_start_ms"],
                        "evidence_end_ms": signal_evidence["evidence_end_ms"],
                    }
                rows.append(
                    {
                        "record_id": metadata["record_id"],
                        "session_id": metadata["session_id"],
                        "target": answer_payload(metadata, "summary"),
                        "prediction": prediction,
                        "event_probabilities": {
                            label: float(semantics_probabilities[batch_index, index])
                            for index, label in enumerate(EVENT_TYPES)
                        },
                    }
                )
    if len(rows) != len(dataset):
        raise RuntimeError(f"Prediction count mismatch: produced {len(rows)} for {len(dataset)} rows")
    return rows


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")


def _write_status(path: Path, **payload: object) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def _emit(path: Path, event: str, **payload: object) -> None:
    row = {"timestamp": time.time(), "event": event, **payload}
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, sort_keys=True) + "\n")
    print(json.dumps(row, sort_keys=True), flush=True)


def _scheduler(
    optimizer: torch.optim.Optimizer, epochs: int, warmup_fraction: float
) -> torch.optim.lr_scheduler.LambdaLR:
    warmup = max(1, round(epochs * warmup_fraction))

    def factor(epoch: int) -> float:
        if epoch < warmup:
            return (epoch + 1) / warmup
        progress = (epoch - warmup) / max(1, epochs - warmup)
        return 0.5 * (1.0 + math.cos(math.pi * min(1.0, progress)))

    return torch.optim.lr_scheduler.LambdaLR(optimizer, factor)


def train_cnn(
    prepared_root: Path,
    run_root: Path,
    config_path: Path,
    *,
    seed: int = 20260912,
    allow_cpu: bool = False,
) -> dict[str, object]:
    """Train on train, select on validation, and evaluate once on locked test."""
    config = CnnConfig.from_yaml(config_path)
    if run_root.exists():
        raise FileExistsError(f"Refusing to overwrite CNN run directory: {run_root}")
    run_root.mkdir(parents=True)
    status_path = run_root / "status.json"
    metrics_path = run_root / "metrics.jsonl"
    _write_status(status_path, state="initializing")

    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    if device.type != "cuda" and not allow_cpu:
        raise RuntimeError("CUDA is required unless --allow-cpu is explicitly supplied")
    if config.precision == "bfloat16" and device.type == "cuda" and not torch.cuda.is_bf16_supported():
        raise RuntimeError("Configured bfloat16 precision is unsupported by this CUDA device")

    train_dataset = PreparedCnnDataset(prepared_root, "train")
    validation_source = PreparedCnnDataset(prepared_root, "validation")
    test_source = PreparedCnnDataset(prepared_root, "test")
    if not all((len(train_dataset), len(validation_source), len(test_source))):
        raise ValueError("Train, validation, and test splits must all be non-empty")
    window_samples = int(train_dataset.prepared.signals.shape[-1])
    validation_dataset = fixed_subset(
        validation_source,
        min(config.evaluation_samples, len(validation_source)),
        config.evaluation_seed,
    )
    test_dataset = fixed_subset(
        test_source,
        min(config.evaluation_samples, len(test_source)),
        config.evaluation_seed,
    )
    generator = torch.Generator().manual_seed(seed)
    pin_memory = device.type == "cuda"
    train_loader = DataLoader(
        train_dataset,
        batch_size=config.batch_size,
        shuffle=True,
        generator=generator,
        num_workers=config.num_workers,
        pin_memory=pin_memory,
    )
    validation_loader = DataLoader(
        validation_dataset,
        batch_size=config.batch_size,
        shuffle=False,
        num_workers=config.num_workers,
        pin_memory=pin_memory,
    )
    test_loader = DataLoader(
        test_dataset,
        batch_size=config.batch_size,
        shuffle=False,
        num_workers=config.num_workers,
        pin_memory=pin_memory,
    )
    model = MultiTaskCnn1D(config.channels, config.kernel_sizes, config.dropout).to(device)
    weights = _class_weights(train_dataset).to(device) if config.class_weighting else None
    optimizer = torch.optim.AdamW(
        model.parameters(), lr=config.learning_rate, weight_decay=config.weight_decay
    )
    scheduler = _scheduler(optimizer, config.epochs, config.warmup_fraction)
    normalization = json.loads(
        (prepared_root / "normalization.json").read_text(encoding="utf-8")
    )
    affected_thresholds = np.asarray(
        normalization["affected_joint_train_free_q99"], dtype=np.float32
    )
    manifest = {
        "model": "multi-task-1d-cnn",
        "config": asdict(config),
        "seed": seed,
        "prepared_root": str(prepared_root.resolve()),
        "python": platform.python_version(),
        "torch": torch.__version__,
        "cuda": torch.version.cuda,
        "gpu": torch.cuda.get_device_name(0) if torch.cuda.is_available() else None,
        "device": str(device),
        "train_examples": len(train_dataset),
        "validation_examples": len(validation_dataset),
        "test_examples": len(test_dataset),
        "trainable_parameters": sum(parameter.numel() for parameter in model.parameters()),
        "test_selection": "fixed random subset without replacement; locked until final evaluation",
        "command": " ".join(os.sys.argv),
    }
    (run_root / "run_manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    _emit(metrics_path, "run_start", **manifest)

    wandb_run = None
    if config.wandb_enabled:
        try:
            import wandb

            wandb_run = wandb.init(
                project=config.wandb_project,
                name=run_root.name,
                config=manifest,
                dir=str(run_root),
                tags=["cnn-1d", "robotics", "baseline"],
            )
        except Exception as error:  # noqa: BLE001 - local artifacts remain authoritative
            _emit(metrics_path, "wandb_unavailable", error=repr(error))

    best_score = (-math.inf, -math.inf)
    stale_epochs = 0
    started = time.monotonic()
    _write_status(status_path, state="training", epoch=0, best_validation_macro_f1=None)
    for epoch in range(1, config.epochs + 1):
        train_loss, loss_parts = _epoch(
            model,
            train_loader,
            optimizer,
            device,
            config,
            weights,
            window_samples,
        )
        validation_loss, _ = _epoch(
            model,
            validation_loader,
            None,
            device,
            config,
            weights,
            window_samples,
        )
        validation_rows = _prediction_rows(
            model,
            validation_dataset,
            validation_source,
            validation_loader,
            device,
            config.precision,
            affected_thresholds,
            window_samples,
        )
        validation_metrics = evaluate_rows(validation_rows)
        macro_f1 = float(validation_metrics["semantics_macro_f1"])
        onset_mae = float(validation_metrics.get("onset_mae_ms", math.inf))
        score = (macro_f1, -onset_mae)
        train_fields = {
            "epoch": epoch,
            "loss": train_loss,
            **loss_parts,
            "learning_rate": scheduler.get_last_lr()[0],
            "elapsed_seconds": time.monotonic() - started,
        }
        validation_fields = {
            "epoch": epoch,
            "validation_loss": validation_loss,
            **validation_metrics,
        }
        _emit(metrics_path, "train_step", **train_fields)
        _emit(metrics_path, "generation_eval", **validation_fields)
        if wandb_run is not None:
            wandb_run.log(
                {
                    **train_fields,
                    "validation/loss": validation_loss,
                    **{f"validation/{key}": value for key, value in validation_metrics.items()},
                },
                step=epoch,
            )
        torch.save(
            {"model_state": model.state_dict(), "manifest": manifest, "epoch": epoch},
            run_root / "last_model.pt",
        )
        if score > best_score:
            best_score = score
            stale_epochs = 0
            torch.save(
                {"model_state": model.state_dict(), "manifest": manifest, "epoch": epoch},
                run_root / "best_model.pt",
            )
            _write_jsonl(run_root / "validation_predictions.jsonl", validation_rows)
        else:
            stale_epochs += 1
        _write_status(
            status_path,
            state="training",
            epoch=epoch,
            best_validation_macro_f1=best_score[0],
            stale_epochs=stale_epochs,
        )
        scheduler.step()
        if stale_epochs >= config.early_stopping_patience:
            break

    checkpoint = torch.load(run_root / "best_model.pt", map_location=device, weights_only=False)
    model.load_state_dict(checkpoint["model_state"])
    validation_rows = _prediction_rows(
        model,
        validation_dataset,
        validation_source,
        validation_loader,
        device,
        config.precision,
        affected_thresholds,
        window_samples,
    )
    # The locked test split is touched once, after all model selection is complete.
    test_rows = _prediction_rows(
        model,
        test_dataset,
        test_source,
        test_loader,
        device,
        config.precision,
        affected_thresholds,
        window_samples,
    )
    _write_jsonl(run_root / "validation_predictions.jsonl", validation_rows)
    _write_jsonl(run_root / "test_predictions.jsonl", test_rows)
    report: dict[str, object] = {
        "model": "multi-task-1d-cnn",
        "selection_metric": "validation semantics_macro_f1; onset_mae_ms tie-breaker",
        "best_epoch": checkpoint["epoch"],
        "validation": evaluate_rows(validation_rows),
        "test": evaluate_rows(test_rows),
        "elapsed_seconds": time.monotonic() - started,
    }
    (run_root / "metrics.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    _write_status(
        status_path,
        state="complete",
        best_epoch=checkpoint["epoch"],
        best_validation_macro_f1=report["validation"]["semantics_macro_f1"],
        elapsed_seconds=report["elapsed_seconds"],
    )
    _emit(metrics_path, "run_complete", **report)
    if wandb_run is not None:
        wandb_run.summary.update(
            {f"test/{key}": value for key, value in report["test"].items()}
        )
        wandb_run.finish()
    return report

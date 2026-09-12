"""Observable single-H100 OpenTSLM SoftPrompt fine-tuning loop."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import platform
import random
import re
import time
from collections import Counter
from contextlib import nullcontext
from pathlib import Path

import numpy as np
import torch
import yaml
from torch.utils.data import DataLoader, Dataset, Subset
from torch.utils.tensorboard import SummaryWriter

from robot_observability.augmentation import JointAttributionCurriculumDataset
from robot_observability.checkpoints import store_runtime_checkpoint
from robot_observability.constants import JOINT_NAMES, OPENTSLM_COMMIT, TIMENET_COMMIT
from robot_observability.metrics import evaluate_rows, parse_answer, schema_value_valid
from robot_observability.opentslm_dataset import RobotQADataset
from robot_observability.qa import INTENTS, answer_payload


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


def stratified_grounding_subset(dataset: RobotQADataset, size: int, seed: int) -> Dataset:
    """Build a stable panel spanning free motion and every observable joint.

    Contact rows are spread across onset-time quintiles within each joint. Selection
    order is intentionally retained so a truncated W&B table still displays every
    group before repeating one.
    """
    if dataset.mode != "summary":
        raise ValueError("Grounding validation panel requires summary mode")
    group_order = ["free", *JOINT_NAMES]
    groups: dict[str, list[int]] = {key: [] for key in group_order}
    for index, record in enumerate(dataset.prepared.records):
        strongest_joint = record.get("strongest_joint")
        key = "free" if strongest_joint is None else str(strongest_joint)
        if key in groups:
            groups[key].append(index)

    rng = np.random.default_rng(seed)
    queues: dict[str, list[int]] = {}
    for key in group_order:
        indices = groups[key]
        if key == "free":
            queues[key] = rng.permutation(indices).tolist()
            continue
        ordered = sorted(indices, key=lambda index: int(dataset.prepared.records[index]["onset_sample"]))
        bins = [rng.permutation(chunk).tolist() for chunk in np.array_split(ordered, 5) if len(chunk)]
        queue: list[int] = []
        while any(bins):
            for bucket in bins:
                if bucket:
                    queue.append(int(bucket.pop()))
        queues[key] = queue

    selected: list[int] = []
    requested = min(size, len(dataset))
    while len(selected) < requested:
        previous_size = len(selected)
        for key in group_order:
            if queues[key] and len(selected) < requested:
                selected.append(int(queues[key].pop(0)))
        if len(selected) == previous_size:
            break
    return Subset(dataset, selected)


def grounding_selection_result(
    metrics: dict[str, float | int], config: dict[str, object]
) -> dict[str, float | bool]:
    """Score decoded grounding only when minimum safety/task gates are met."""
    joint_weight = float(config.get("joint_weight", 0.5))
    onset_weight = float(config.get("onset_weight", 0.5))
    joint_metric = str(config.get("joint_metric", "strongest_joint_macro_accuracy"))
    joint = float(metrics.get(joint_metric, 0.0))
    onset = float(metrics.get("onset_within_50ms", 0.0))
    score = joint_weight * joint + onset_weight * onset
    gates = config.get("gates", {})
    eligible = all(float(metrics.get(str(key), 0.0)) >= float(value) for key, value in gates.items())
    return {"score": score, "eligible": eligible}


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
        {
            "name": "encoder",
            "params": [p for p in model.encoder.parameters() if p.requires_grad],
            "lr": float(rates["encoder"]),
        },
        {
            "name": "projector",
            "params": [p for p in model.projector.parameters() if p.requires_grad],
            "lr": float(rates["projector"]),
        },
        {"name": "lora", "params": model.get_lora_parameters(), "lr": float(rates["lora"])},
    ]
    return torch.optim.AdamW(groups, weight_decay=0.01)


def optimizer_group_stats(optimizer: torch.optim.Optimizer) -> dict[str, float | int]:
    """Expose whether every trainable component receives gradients and updates."""

    def norm(tensors: list[torch.Tensor]) -> float:
        square_sum = None
        for tensor in tensors:
            value = tensor.detach().float().square().sum()
            square_sum = value if square_sum is None else square_sum + value
        return math.sqrt(float(square_sum.cpu())) if square_sum is not None else 0.0

    result: dict[str, float | int] = {}
    for index, group in enumerate(optimizer.param_groups):
        name = str(group.get("name", f"group_{index}"))
        parameters = list(group["params"])
        gradients = [parameter.grad for parameter in parameters if parameter.grad is not None]
        result[f"{name}_learning_rate_prestep"] = float(group["lr"])
        result[f"{name}_parameter_norm_prestep"] = norm(parameters)
        result[f"{name}_gradient_norm_preclip"] = norm(gradients)
        result[f"{name}_parameters"] = sum(parameter.numel() for parameter in parameters)
        result[f"{name}_parameters_with_grad"] = sum(
            parameter.numel() for parameter in parameters if parameter.grad is not None
        )
    return result


def optimizer_group_snapshots(optimizer: torch.optim.Optimizer) -> dict[str, list[torch.Tensor]]:
    return {
        str(group.get("name", f"group_{index}")): [
            parameter.detach().clone() for parameter in group["params"]
        ]
        for index, group in enumerate(optimizer.param_groups)
    }


def optimizer_group_update_stats(
    optimizer: torch.optim.Optimizer,
    snapshots: dict[str, list[torch.Tensor]],
    before: dict[str, float | int],
) -> dict[str, float]:
    result = {}
    for index, group in enumerate(optimizer.param_groups):
        name = str(group.get("name", f"group_{index}"))
        square_sum = None
        for parameter, previous in zip(group["params"], snapshots[name]):
            value = (parameter.detach() - previous).float().square().sum()
            square_sum = value if square_sum is None else square_sum + value
        update_norm = math.sqrt(float(square_sum.cpu())) if square_sum is not None else 0.0
        parameter_norm = float(before[f"{name}_parameter_norm_prestep"])
        result[f"{name}_update_norm"] = update_norm
        result[f"{name}_relative_update"] = update_norm / max(parameter_norm, 1e-12)
    return result


def signal_preview(
    signal: object,
    width: int = 512,
    channel_height: int = 40,
    onset_sample: int | None = None,
    evidence_start: int | None = None,
    evidence_end: int | None = None,
) -> np.ndarray:
    """Render seven compact signal strips without adding a plotting dependency."""
    if hasattr(signal, "detach"):
        signal = signal.detach().float().cpu().numpy()
    values = np.asarray(signal, dtype=np.float32)
    if values.ndim != 2:
        raise ValueError(f"Expected channels x time signal, got {values.shape}")
    channels, samples = values.shape
    height = channels * channel_height
    canvas = np.full((height, width, 3), 250, dtype=np.uint8)
    colors = np.asarray(
        [
            (31, 119, 180),
            (255, 127, 14),
            (44, 160, 44),
            (214, 39, 40),
            (148, 103, 189),
            (140, 86, 75),
            (227, 119, 194),
        ],
        dtype=np.uint8,
    )
    boundaries = np.linspace(0, samples, width + 1, dtype=int)
    scale = max(float(np.quantile(np.abs(values), 0.995)), 1e-6)
    for channel in range(channels):
        top = channel * channel_height
        center = top + channel_height // 2
        canvas[top : top + 1, :, :] = 210
        canvas[center : center + 1, :, :] = 225
        half_height = channel_height // 2 - 3
        for x in range(width):
            start = min(boundaries[x], samples - 1)
            stop = max(start + 1, boundaries[x + 1])
            segment = values[channel, start:stop]
            low = round(center - np.clip(segment.max() / scale, -1, 1) * half_height)
            high = round(center - np.clip(segment.min() / scale, -1, 1) * half_height)
            low, high = sorted((max(top + 2, low), min(top + channel_height - 2, high)))
            canvas[low : high + 1, x, :] = colors[channel % len(colors)]
    for marker, color in (
        (evidence_start, (90, 90, 90)),
        (evidence_end, (90, 90, 90)),
        (onset_sample, (0, 0, 0)),
    ):
        if marker is not None:
            x = round(np.clip(marker / max(samples - 1, 1), 0, 1) * (width - 1))
            canvas[:, max(0, x - 1) : min(width, x + 1), :] = color
    return canvas


def write_probe_manifest(path: Path, dataset: Dataset) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for index in range(len(dataset)):
            sample = dataset[index]
            metadata = sample["metadata"]
            row = {
                "record_id": sample["record_id"],
                "session_id": metadata["session_id"],
                "intent": sample["intent"],
                "event_type": metadata["event_type"],
                "pre_prompt": sample["pre_prompt"],
                "channel_descriptions": sample["time_series_text"],
                "post_prompt": sample["post_prompt"],
                "target": answer_payload(metadata, sample["intent"]),
                "supervised_answer": sample["answer"],
            }
            handle.write(json.dumps(row, sort_keys=True) + "\n")


def source_index(dataset: Dataset, index: int) -> int:
    while isinstance(dataset, Subset) or hasattr(dataset, "source_index"):
        if isinstance(dataset, Subset):
            index = int(dataset.indices[index])
        else:
            index = int(dataset.source_index(index))
        dataset = dataset.dataset
    return index


def training_manifest_rows(dataset: Dataset) -> list[dict[str, object]]:
    rows = []
    for index in range(len(dataset)):
        sample = dataset[index]
        metadata = sample["metadata"]
        rows.append(
            {
                "selected_position": index,
                "source_index": source_index(dataset, index),
                "record_id": sample["record_id"],
                "source_record_id": metadata.get("source_record_id", sample["record_id"]),
                "session_id": metadata["session_id"],
                "intent": sample["intent"],
                "event_type": metadata["event_type"],
                "augmentation": metadata.get("augmentation", {"type": "identity"}),
                "post_prompt": sample["post_prompt"],
                "target": answer_payload(metadata, sample["intent"]),
                "supervised_answer": sample["answer"],
            }
        )
    return rows


def write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def prepared_hashes(root: Path) -> dict[str, str]:
    relative_paths = (
        "dataset_summary.json",
        "normalization.json",
        "source_receipt.json",
        "splits.json",
        "train/records.jsonl",
        "train/signals.npy",
        "validation/records.jsonl",
        "validation/signals.npy",
        "test/records.jsonl",
        "test/signals.npy",
    )
    return {
        relative: sha256_file(root / relative) for relative in relative_paths if (root / relative).exists()
    }


def wandb_manifest_table(wandb_module, rows: list[dict[str, object]], maximum: int = 256):
    columns = [
        "selected_position",
        "source_index",
        "record_id",
        "source_record_id",
        "session_id",
        "intent",
        "event_type",
        "augmentation",
        "post_prompt",
        "target",
        "supervised_answer",
    ]
    return wandb_module.Table(
        columns=columns,
        data=[
            [
                row["selected_position"],
                row["source_index"],
                row["record_id"],
                row["source_record_id"],
                row["session_id"],
                row["intent"],
                row["event_type"],
                json.dumps(row["augmentation"], sort_keys=True),
                row["post_prompt"],
                json.dumps(row["target"], sort_keys=True),
                row["supervised_answer"],
            ]
            for row in rows[:maximum]
        ],
    )


def wandb_examples_table(
    wandb_module,
    dataset: Dataset,
    *,
    prediction_rows: list[dict[str, object]] | None = None,
    step: int = 0,
    maximum: int = 24,
):
    columns = [
        "step",
        "record_id",
        "session_id",
        "intent",
        "event_type",
        "question_and_contract",
        "target_reasoning",
        "generated_reasoning",
        "target",
        "prediction",
        "strict_schema_valid",
        "answer_exact",
        "onset_error_ms",
        "retry_used",
        "first_pass_output",
        "final_model_output",
    ]

    def reasoning(text: object) -> str | None:
        value = str(text or "")
        rationale = value.lower().find("rationale:")
        answer = value.lower().rfind("answer:")
        if rationale < 0 or answer <= rationale:
            return None
        return value[rationale + len("rationale:") : answer].strip()

    data = []
    for index in range(min(len(dataset), maximum)):
        sample = dataset[index]
        metadata = sample["metadata"]
        target = answer_payload(metadata, sample["intent"])
        row = prediction_rows[index] if prediction_rows is not None else {}
        prediction = row.get("prediction")
        schema_exact = schema_value_valid(prediction, target)
        answer_exact = isinstance(prediction, dict) and prediction == target
        onset_error = None
        if target.get("onset_ms") is not None and isinstance(prediction, dict):
            try:
                onset_error = abs(float(prediction.get("onset_ms")) - float(target["onset_ms"]))
            except (TypeError, ValueError):
                pass
        data.append(
            [
                step,
                sample["record_id"],
                metadata["session_id"],
                sample["intent"],
                metadata["event_type"],
                str(sample["post_prompt"]).strip(),
                reasoning(sample["answer"]),
                reasoning(row.get("output")),
                json.dumps(target, sort_keys=True),
                json.dumps(prediction, sort_keys=True) if isinstance(prediction, dict) else None,
                schema_exact,
                answer_exact,
                onset_error,
                row.get("retry_used"),
                row.get("first_pass_output"),
                row.get("output"),
            ]
        )
    return wandb_module.Table(columns=columns, data=data)


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
    weighted_loss = 0.0
    supervised_tokens = 0
    context = (
        torch.autocast(device_type="cuda", dtype=torch.bfloat16) if device_type == "cuda" else nullcontext()
    )
    with context:
        for batch in loader:
            loss = float(model.compute_loss(batch).detach().cpu())
            token_count = int(
                model.tokenizer(
                    [sample["answer"] for sample in batch],
                    padding=True,
                    truncation=True,
                    return_tensors="pt",
                ).attention_mask.sum()
            )
            weighted_loss += loss * token_count
            supervised_tokens += token_count
    return weighted_loss / supervised_tokens if supervised_tokens else math.nan


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
            first_predictions = [parse_answer(output) for output in outputs]
            retry_positions = [
                index
                for index, (output, prediction) in enumerate(zip(outputs, first_predictions))
                if not str(output).strip() or prediction is None
            ]
            final_outputs = list(outputs)
            final_predictions = list(first_predictions)
            if retry_positions:
                retry_outputs = model.generate(
                    [batch[index] for index in retry_positions],
                    max_new_tokens=128,
                    min_new_tokens=16,
                    do_sample=False,
                )
                for index, retry_output in zip(retry_positions, retry_outputs):
                    final_outputs[index] = retry_output
                    final_predictions[index] = parse_answer(retry_output)
            for index, (sample, output) in enumerate(zip(batch, outputs)):
                metadata = sample["metadata"]
                row = {
                    "record_id": sample["record_id"],
                    "intent": sample["intent"],
                    "target": answer_payload(metadata, sample["intent"]),
                    "first_pass_output": output,
                    "first_pass_prediction": first_predictions[index],
                    "retry_used": index in retry_positions,
                    "output": final_outputs[index],
                    "prediction": final_predictions[index],
                }
                rows.append(row)
                handle.write(json.dumps(row, sort_keys=True) + "\n")
    metrics = evaluate_rows(rows)
    first_pass_rows = [
        {**row, "output": row["first_pass_output"], "prediction": row["first_pass_prediction"]}
        for row in rows
    ]
    metrics.update({f"first_pass/{key}": value for key, value in evaluate_rows(first_pass_rows).items()})
    metrics["retry_rate"] = float(np.mean([bool(row["retry_used"]) for row in rows])) if rows else 0.0
    metrics["first_pass_blank_rate"] = (
        float(np.mean([not str(row["first_pass_output"]).strip() for row in rows])) if rows else 0.0
    )
    metrics["final_blank_rate"] = (
        float(np.mean([not str(row["output"]).strip() for row in rows])) if rows else 0.0
    )
    rationale_texts = []
    for row in rows:
        output = str(row["output"])
        rationale_at = output.casefold().find("rationale:")
        answer_at = output.casefold().rfind("answer:")
        rationale_texts.append(
            output[rationale_at + len("rationale:") : answer_at].strip()
            if 0 <= rationale_at < answer_at
            else ""
        )
    metrics["rationale_presence"] = (
        float(np.mean([bool(rationale) for rationale in rationale_texts])) if rows else 0.0
    )
    nonblank_rationales = [rationale for rationale in rationale_texts if rationale]
    normalized_rationales = [
        re.sub(
            r"\b\d+(?:\.\d+)?\b",
            "#",
            re.sub(r"\bJ[1-7]\b", "J#", rationale, flags=re.IGNORECASE),
        )
        for rationale in nonblank_rationales
    ]
    metrics["rationale_unique_count"] = len(set(nonblank_rationales))
    metrics["rationale_unique_rate"] = (
        len(set(nonblank_rationales)) / len(nonblank_rationales) if nonblank_rationales else 0.0
    )
    metrics["rationale_normalized_unique_count"] = len(set(normalized_rationales))
    metrics["rationale_normalized_unique_rate"] = (
        len(set(normalized_rationales)) / len(normalized_rationales) if normalized_rationales else 0.0
    )
    class_labels = ("free", "intentional", "accidental")
    metrics["rationale_premature_label_rate"] = (
        float(
            np.mean(
                [
                    any(re.search(rf"\b{label}\b", rationale, re.IGNORECASE) for label in class_labels)
                    for rationale in rationale_texts
                    if rationale
                ]
            )
        )
        if any(rationale_texts)
        else 0.0
    )
    target_joint_checks = []
    prediction_joint_checks = []
    target_onset_checks = []
    for row, rationale in zip(rows, rationale_texts):
        target_joint = row["target"].get("strongest_joint")
        target_onset = row["target"].get("onset_ms")
        prediction = row["prediction"]
        predicted_joint = prediction.get("strongest_joint") if isinstance(prediction, dict) else None
        if target_joint is not None:
            target_joint_checks.append(str(target_joint) in rationale)
        if predicted_joint is not None:
            prediction_joint_checks.append(str(predicted_joint) in rationale)
        if target_onset is not None:
            target_onset_checks.append(f"{target_onset} ms" in rationale)
    metrics["rationale_target_joint_consistency"] = (
        float(np.mean(target_joint_checks)) if target_joint_checks else 0.0
    )
    metrics["rationale_prediction_joint_consistency"] = (
        float(np.mean(prediction_joint_checks)) if prediction_joint_checks else 0.0
    )
    metrics["rationale_target_onset_consistency"] = (
        float(np.mean(target_onset_checks)) if target_onset_checks else 0.0
    )
    return metrics, rows


def intent_fit_metrics(rows: list[dict[str, object]]) -> dict[str, float | int]:
    """Keep aggregate fit from hiding a prompt family that the model cannot learn."""
    grouped: dict[str, list[dict[str, object]]] = {}
    for row in rows:
        grouped.setdefault(str(row["intent"]), []).append(row)
    result: dict[str, float | int] = {}
    for intent, intent_rows in sorted(grouped.items()):
        metrics = evaluate_rows(intent_rows)
        for key in sorted(metrics):
            result[f"intent/{intent}/{key}"] = metrics[key]
    return result


def should_run_generation(phase: str, step: int, cadence: int, *, generation_at_start: bool) -> bool:
    """Schedule expensive decoded evaluation consistently for smoke and full runs."""
    if phase in {"epoch", "final"}:
        return True
    if phase == "start":
        return generation_at_start
    return phase == "step" and cadence > 0 and step % cadence == 0


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
    return balanced_group_subset(dataset, groups, size, seed)


def stratified_training_probe_subset(dataset: Dataset, size: int, seed: int) -> Dataset:
    """Balance fit probes over both event semantics and prompt intent."""
    groups: dict[str, list[int]] = {}
    for index in range(len(dataset)):
        sample = dataset[index]
        key = f"{sample['metadata']['event_type']}::{sample['intent']}"
        groups.setdefault(key, []).append(index)
    return balanced_group_subset(dataset, groups, size, seed)


def balanced_group_subset(dataset: Dataset, groups: dict[str, list[int]], size: int, seed: int) -> Dataset:
    rng = np.random.default_rng(seed)
    shuffled = {key: rng.permutation(indices).tolist() for key, indices in groups.items()}
    selected: list[int] = []
    while len(selected) < min(size, len(dataset)):
        previous_size = len(selected)
        for key in sorted(shuffled):
            if shuffled[key] and len(selected) < size:
                selected.append(shuffled[key].pop())
        if len(selected) == previous_size:
            break
    return Subset(dataset, selected)


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
            for namespace in (
                "train/*",
                "optimization/*",
                "training_probe/*",
                "training_probe_zero_signal/*",
                "training_probe_ablation/*",
                "validation/*",
                "validation_matched/*",
                "validation_zero_signal/*",
                "validation_signal_ablation/*",
                "diagnostics/*",
            ):
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
    output_format = str(config.get("targets", {}).get("format", "answer_then_evidence"))
    train_dataset: Dataset = RobotQADataset(
        args.prepared_root,
        "train",
        mode="mixed",
        eos_token=eos,
        seed=seed,
        output_format=output_format,
    )
    validation_dataset: Dataset = RobotQADataset(
        args.prepared_root,
        "validation",
        mode="all_intents",
        eos_token=eos,
        seed=seed,
        output_format=output_format,
    )
    validation_summary_dataset: Dataset = RobotQADataset(
        args.prepared_root,
        "validation",
        mode="summary",
        eos_token=eos,
        seed=seed,
        output_format=output_format,
    )
    validation_matched_dataset: Dataset = RobotQADataset(
        args.prepared_root,
        "validation",
        mode="mixed",
        eos_token=eos,
        seed=seed,
        output_format=output_format,
    )
    if args.smoke:
        fit_probe_config = config.get("fit_probe", {})
        train_dataset = stratified_training_probe_subset(
            train_dataset,
            min(len(train_dataset), int(fit_probe_config.get("train_examples", 48))),
            seed,
        )
        validation_examples = int(fit_probe_config.get("validation_examples", 48))
        validation_dataset = fixed_subset(
            validation_dataset, min(len(validation_dataset), validation_examples), seed + 1
        )
        validation_summary_dataset = stratified_summary_subset(
            validation_summary_dataset,
            min(len(validation_summary_dataset), validation_examples),
            seed + 1,
        )
    elif config.get("training", {}).get("max_examples"):
        train_dataset = fixed_subset(
            train_dataset,
            min(len(train_dataset), int(config["training"]["max_examples"])),
            seed,
        )
    if config.get("training", {}).get("joint_attribution_curriculum", False):
        train_dataset = JointAttributionCurriculumDataset(
            train_dataset,
            seed=seed,
            output_format=output_format,
            eos_token=eos,
            permute_strongest=bool(config.get("training", {}).get("balanced_joint_permutation", True)),
            views=(tuple(INTENTS) if config.get("training", {}).get("curriculum_intents") == "all" else None),
        )
    probe_config = config["observability"].get("training_probe", {})
    probe_enabled = bool(probe_config.get("enabled", True))
    train_probe_loss_dataset: Dataset = Subset(train_dataset, [])
    train_probe_generation_dataset: Dataset = Subset(train_dataset, [])
    train_probe_zero_dataset: Dataset = Subset(train_dataset, [])
    validation_matched_loss_dataset: Dataset = Subset(validation_matched_dataset, [])
    if probe_enabled:
        train_probe_loss_dataset = stratified_training_probe_subset(
            train_dataset,
            min(len(train_dataset), int(probe_config.get("loss_subset", 64))),
            seed + 10,
        )
        train_probe_generation_dataset = stratified_training_probe_subset(
            train_probe_loss_dataset,
            min(len(train_probe_loss_dataset), int(probe_config.get("generation_subset", 48))),
            seed + 11,
        )
        train_probe_zero_dataset = ZeroSignalDataset(
            stratified_subset(
                train_probe_generation_dataset,
                min(
                    len(train_probe_generation_dataset),
                    int(probe_config.get("zero_signal_subset", 12)),
                ),
                seed + 12,
            )
        )
        validation_matched_loss_dataset = stratified_training_probe_subset(
            validation_matched_dataset,
            min(
                len(validation_matched_dataset),
                int(probe_config.get("matched_validation_subset", 64)),
            ),
            seed + 13,
        )
    validation_loss_dataset = fixed_subset(
        validation_dataset,
        min(len(validation_dataset), int(config["validation"]["loss_subset"])),
        seed + 2,
    )
    generation_selection = str(config["validation"].get("generation_selection", "event_stratified"))
    generation_source_dataset = (
        validation_dataset
        if generation_selection == "event_intent_stratified"
        else validation_summary_dataset
    )
    generation_size = min(len(generation_source_dataset), int(config["validation"]["generation_subset"]))
    if args.smoke:
        generation_dataset = fixed_subset(generation_source_dataset, generation_size, seed + 3)
    elif generation_selection == "event_stratified":
        generation_dataset = stratified_summary_subset(validation_summary_dataset, generation_size, seed + 3)
    elif generation_selection == "joint_stratified":
        generation_dataset = stratified_grounding_subset(
            validation_summary_dataset, generation_size, seed + 3
        )
    elif generation_selection == "event_intent_stratified":
        generation_dataset = stratified_training_probe_subset(validation_dataset, generation_size, seed + 3)
    else:
        raise ValueError(f"Unknown validation generation selection: {generation_selection}")
    zero_signal_dataset: Dataset = ZeroSignalDataset(
        stratified_subset(
            generation_dataset,
            min(len(generation_dataset), int(config["validation"].get("zero_signal_subset", 0))),
            seed + 4,
        )
    )

    fit_probe_config = config.get("fit_probe", {})
    batch_size = int(fit_probe_config.get("batch_size", 1)) if args.smoke else int(config["batch_size"])
    validation_batch_size = (
        int(fit_probe_config.get("validation_batch_size", batch_size))
        if args.smoke
        else int(config["validation"].get("batch_size", batch_size))
    )
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
    train_probe_loss_loader = DataLoader(
        train_probe_loss_dataset,
        batch_size=validation_batch_size,
        shuffle=False,
        collate_fn=collate,
        num_workers=0,
    )
    train_probe_zero_loss_loader = DataLoader(
        ZeroSignalDataset(train_probe_loss_dataset),
        batch_size=validation_batch_size,
        shuffle=False,
        collate_fn=collate,
        num_workers=0,
    )
    validation_matched_loss_loader = DataLoader(
        validation_matched_loss_dataset,
        batch_size=validation_batch_size,
        shuffle=False,
        collate_fn=collate,
        num_workers=0,
    )
    epochs = int(fit_probe_config.get("epochs", 20)) if args.smoke else int(config["epochs"])
    steps_per_epoch = math.ceil(len(train_loader) / accumulation)
    total_steps = (
        min(args.max_steps, epochs * steps_per_epoch) if args.max_steps else epochs * steps_per_epoch
    )
    optimizer = optimizer_for(model, config)
    scheduler = scheduler_for(
        optimizer,
        total_steps,
        warmup_fraction=float(config.get("training", {}).get("warmup_fraction", 0.03)),
    )
    parameter_inventory = {
        key: value for key, value in optimizer_group_stats(optimizer).items() if key.endswith("_parameters")
    }
    selected_training_rows = training_manifest_rows(train_dataset)
    training_manifest_path = run_root / "training_selection_manifest.jsonl"
    write_jsonl(training_manifest_path, selected_training_rows)
    dataset_hashes = prepared_hashes(args.prepared_root)
    source_receipt_path = args.prepared_root / "source_receipt.json"
    dataset_source_receipt = (
        json.loads(source_receipt_path.read_text(encoding="utf-8")) if source_receipt_path.exists() else None
    )

    manifest = {
        "run_name": args.run_name,
        "config": config,
        "seed": seed,
        "smoke": args.smoke,
        "prepared_root": str(args.prepared_root.resolve()),
        "prepared_sha256": dataset_hashes,
        "dataset_source_receipt": dataset_source_receipt,
        "training_selection_manifest_sha256": sha256_file(training_manifest_path),
        "opentslm_commit": OPENTSLM_COMMIT,
        "timenet_commit": TIMENET_COMMIT,
        "python": platform.python_version(),
        "torch": torch.__version__,
        "cuda": torch.version.cuda,
        "gpu": torch.cuda.get_device_name(0) if torch.cuda.is_available() else None,
        "train_examples": len(train_dataset),
        "train_source_examples": (
            len(train_dataset.dataset)
            if isinstance(train_dataset, JointAttributionCurriculumDataset)
            else len(train_dataset)
        ),
        "train_views_per_source": (
            len(train_dataset.views) if isinstance(train_dataset, JointAttributionCurriculumDataset) else 1
        ),
        "validation_examples": len(validation_dataset),
        "training_probe_examples": len(train_probe_loss_dataset),
        "training_probe_generation_examples": len(train_probe_generation_dataset),
        "validation_matched_examples": len(validation_matched_loss_dataset),
        "trainable_parameter_inventory": parameter_inventory,
        "training_probe_class_counts": dict(
            Counter(
                str(train_probe_generation_dataset[index]["metadata"]["event_type"])
                for index in range(len(train_probe_generation_dataset))
            )
        ),
        "training_probe_intent_counts": dict(
            Counter(
                str(train_probe_generation_dataset[index]["intent"])
                for index in range(len(train_probe_generation_dataset))
            )
        ),
        "generation_canary_class_counts": dict(
            Counter(
                str(generation_dataset[index]["metadata"]["event_type"])
                for index in range(len(generation_dataset))
            )
        ),
        "generation_canary_joint_counts": dict(
            Counter(
                str(generation_dataset[index]["metadata"].get("strongest_joint") or "free")
                for index in range(len(generation_dataset))
            )
        ),
        "training_intent_counts": dict(Counter(str(row["intent"]) for row in selected_training_rows)),
        "training_strongest_joint_counts": dict(
            Counter(
                str(row["target"].get("strongest_joint"))
                for row in selected_training_rows
                if row["intent"] == "strongest_joint" and row["target"].get("strongest_joint") is not None
            )
        ),
        "command": " ".join(os.sys.argv),
    }
    (run_root / "run_manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    emit(metrics_path, "run_start", **manifest)
    if probe_enabled:
        write_probe_manifest(run_root / "training_probe_manifest.jsonl", train_probe_generation_dataset)
        if wandb_run is not None:
            import wandb

            wandb_run.log(
                {
                    "trainer/global_step": 0,
                    "data/training_manifest": wandb_manifest_table(
                        wandb,
                        selected_training_rows,
                        maximum=int(probe_config.get("manifest_table_rows", 256)),
                    ),
                    "data/training_probe_examples": wandb_examples_table(
                        wandb,
                        train_probe_generation_dataset,
                        maximum=int(probe_config.get("dataset_table_rows", 48)),
                    ),
                }
            )
            wandb_run.summary.update(parameter_inventory)

    precision = str(config.get("precision", "float32"))
    if precision not in {"float32", "bfloat16"}:
        raise ValueError(f"Unsupported precision: {precision}")

    def training_precision_context():
        if device == "cuda" and precision == "bfloat16":
            return torch.autocast(device_type="cuda", dtype=torch.bfloat16)
        return nullcontext()

    # Fail early on shape, checkpoint, and forward incompatibilities.
    first_batch = next(iter(train_loader))
    with torch.no_grad(), training_precision_context():
        initial_loss = float(model.compute_loss(first_batch).detach().cpu())
    emit(metrics_path, "forward_smoke", loss=initial_loss)

    start_time = time.monotonic()
    deadline = start_time + float(config["max_wall_time_hours"]) * 3600
    global_step = 0
    best_validation = math.inf
    best_grounding_score = -math.inf
    best_grounding_step: int | None = None
    patience = 0
    stop_reason = "epochs_complete"
    validation_config = config["validation"]
    generation_at_start = bool(config["observability"].get("generation_at_start", False))
    validation_every_steps = int(validation_config.get("every_steps", 0))
    initial_train_probe_loss: float | None = None

    def validate(phase: str, epoch: int, step: int) -> tuple[float, bool]:
        nonlocal best_grounding_score, best_grounding_step, best_validation, initial_train_probe_loss
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
            store_runtime_checkpoint(model, run_root / "best_model.pt")
        probe_every_steps = int(probe_config.get("every_steps", 100))
        probe_generation_every = int(probe_config.get("generation_every_steps", 250))
        should_generate_probe = should_run_generation(
            phase,
            step,
            probe_generation_every,
            generation_at_start=generation_at_start,
        )
        should_run_probe_loss = probe_enabled and (
            phase != "step" or step % probe_every_steps == 0 or should_generate_probe
        )
        if should_run_probe_loss:
            train_probe_loss = mean_loss(
                model, train_probe_loss_loader, "cuda" if device == "cuda" else "cpu"
            )
            train_probe_zero_loss = mean_loss(
                model, train_probe_zero_loss_loader, "cuda" if device == "cuda" else "cpu"
            )
            matched_validation_loss = mean_loss(
                model,
                validation_matched_loss_loader,
                "cuda" if device == "cuda" else "cpu",
            )
            if initial_train_probe_loss is None:
                initial_train_probe_loss = train_probe_loss
            probe_metrics: dict[str, float | int] = {
                "loss": train_probe_loss,
                "zero_signal_loss": train_probe_zero_loss,
                "signal_loss_gap": train_probe_zero_loss - train_probe_loss,
                "relative_signal_loss_gap": (train_probe_zero_loss - train_probe_loss)
                / max(train_probe_loss, 1e-12),
                "loss_fraction_of_initial": train_probe_loss / max(initial_train_probe_loss, 1e-12),
                "matched_validation_loss": matched_validation_loss,
                "matched_generalization_gap": matched_validation_loss - train_probe_loss,
                "matched_relative_generalization_gap": (matched_validation_loss - train_probe_loss)
                / max(train_probe_loss, 1e-12),
            }
            emit(
                metrics_path,
                "training_probe_loss_eval",
                phase=phase,
                epoch=epoch,
                step=step,
                **probe_metrics,
            )
            zero_probe_metrics: dict[str, float | int] = {}
            ablation_metrics: dict[str, float | int] = {}
            probe_rows: list[dict[str, object]] = []
            if should_generate_probe:
                generated_metrics, probe_rows = generation_eval(
                    model,
                    train_probe_generation_dataset,
                    run_root / f"generation_training_probe_{phase}_step_{step:06d}.jsonl",
                    batch_size=validation_batch_size,
                )
                probe_metrics.update(generated_metrics)
                probe_metrics.update(intent_fit_metrics(probe_rows))
                emit(
                    metrics_path,
                    "training_probe_eval",
                    phase=phase,
                    epoch=epoch,
                    step=step,
                    **probe_metrics,
                )
            if should_generate_probe and len(train_probe_zero_dataset):
                zero_probe_metrics, zero_probe_rows = generation_eval(
                    model,
                    train_probe_zero_dataset,
                    run_root / f"generation_training_probe_zero_{phase}_step_{step:06d}.jsonl",
                    batch_size=validation_batch_size,
                )
                real_probe_predictions = {row["record_id"]: row["prediction"] for row in probe_rows}
                real_probe_rows = {row["record_id"]: row for row in probe_rows}
                changed = [
                    real_probe_predictions.get(row["record_id"]) != row["prediction"]
                    for row in zero_probe_rows
                ]
                zero_probe_metrics["prediction_change_rate"] = float(np.mean(changed)) if changed else 0.0
                paired_real_metrics = evaluate_rows(
                    [real_probe_rows[row["record_id"]] for row in zero_probe_rows]
                )
                for key, value in paired_real_metrics.items():
                    ablation_metrics[f"real/{key}"] = value
                for key, value in zero_probe_metrics.items():
                    ablation_metrics[f"zero/{key}"] = value
                    if key in paired_real_metrics and key != "n":
                        ablation_metrics[f"delta/{key}"] = float(paired_real_metrics[key]) - float(value)
                emit(
                    metrics_path,
                    "training_probe_zero_signal_eval",
                    phase=phase,
                    epoch=epoch,
                    step=step,
                    **zero_probe_metrics,
                )
                emit(
                    metrics_path,
                    "training_probe_signal_ablation",
                    phase=phase,
                    epoch=epoch,
                    step=step,
                    **ablation_metrics,
                )
            for key, value in probe_metrics.items():
                if isinstance(value, (int, float)):
                    writer.add_scalar(f"training_probe/{key}", value, step)
            writer.add_scalar("validation_matched/loss", matched_validation_loss, step)
            if wandb_run is not None:
                import wandb

                wandb_run.log(
                    {
                        "trainer/global_step": step,
                        **{f"training_probe/{key}": value for key, value in probe_metrics.items()},
                        **{
                            f"training_probe_zero_signal/{key}": value
                            for key, value in zero_probe_metrics.items()
                        },
                        **{
                            f"training_probe_ablation/{key}": value for key, value in ablation_metrics.items()
                        },
                        "validation_matched/loss": matched_validation_loss,
                        "diagnostics/matched_generalization_gap": (
                            matched_validation_loss - train_probe_loss
                        ),
                        **(
                            {
                                "training_probe/predictions": wandb_examples_table(
                                    wandb,
                                    train_probe_generation_dataset,
                                    prediction_rows=probe_rows,
                                    step=step,
                                    maximum=int(probe_config.get("examples_table_rows", 48)),
                                )
                            }
                            if probe_rows
                            else {}
                        ),
                    }
                )
        validation_generation_every = int(config["observability"].get("sample_generations_every_steps", 250))
        should_generate_validation = should_run_generation(
            phase,
            step,
            validation_generation_every,
            generation_at_start=generation_at_start,
        )
        if should_generate_validation:
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
            selection_config = config.get("checkpoint_selection", {})
            selection_enabled = bool(selection_config.get("enabled", False))
            if selection_enabled:
                selection = grounding_selection_result(generation_metrics, selection_config)
                grounding_improved = (
                    bool(selection["eligible"]) and float(selection["score"]) > best_grounding_score
                )
                if grounding_improved:
                    best_grounding_score = float(selection["score"])
                    best_grounding_step = step
                    store_runtime_checkpoint(model, run_root / "best_grounding_model.pt")
                emit(
                    metrics_path,
                    "grounding_checkpoint_selection",
                    phase=phase,
                    epoch=epoch,
                    step=step,
                    score=float(selection["score"]),
                    eligible=bool(selection["eligible"]),
                    improved=grounding_improved,
                    best_score=(best_grounding_score if math.isfinite(best_grounding_score) else None),
                )
            if wandb_run is not None:
                wandb_run.log(
                    {
                        "trainer/global_step": step,
                        **{f"validation/{key}": value for key, value in generation_metrics.items()},
                        **(
                            {
                                "validation/grounding_selection_score": selection["score"],
                                "validation/grounding_selection_eligible": selection["eligible"],
                                "validation/grounding_selection_improved": grounding_improved,
                            }
                            if selection_enabled
                            else {}
                        ),
                    }
                )
                import wandb

                sample_table = wandb_examples_table(
                    wandb,
                    generation_dataset,
                    prediction_rows=generation_rows,
                    step=step,
                    maximum=int(validation_config.get("examples_table_rows", 12)),
                )
                wandb_run.log({"trainer/global_step": step, "validation/reasoning_traces": sample_table})
            if len(zero_signal_dataset):
                zero_metrics, zero_rows = generation_eval(
                    model,
                    zero_signal_dataset,
                    run_root / f"generation_zero_signal_{phase}_step_{step:06d}.jsonl",
                    batch_size=validation_batch_size,
                )
                real_rows = {row["record_id"]: row for row in generation_rows}
                changed = [
                    real_rows[zero["record_id"]]["prediction"] != zero["prediction"] for zero in zero_rows
                ]
                zero_metrics["prediction_change_rate"] = float(np.mean(changed)) if changed else 0.0
                paired_real_metrics = evaluate_rows([real_rows[row["record_id"]] for row in zero_rows])
                validation_ablation = {
                    **{f"real/{key}": value for key, value in paired_real_metrics.items()},
                    **{f"zero/{key}": value for key, value in zero_metrics.items()},
                    **{
                        f"delta/{key}": float(paired_real_metrics[key]) - float(value)
                        for key, value in zero_metrics.items()
                        if key in paired_real_metrics and key != "n"
                    },
                }
                emit(
                    metrics_path,
                    "zero_signal_eval",
                    phase=phase,
                    epoch=epoch,
                    step=step,
                    **zero_metrics,
                )
                emit(
                    metrics_path,
                    "validation_signal_ablation",
                    phase=phase,
                    epoch=epoch,
                    step=step,
                    **validation_ablation,
                )
                if wandb_run is not None:
                    wandb_run.log(
                        {
                            "trainer/global_step": step,
                            **{f"validation_zero_signal/{key}": value for key, value in zero_metrics.items()},
                            **{
                                f"validation_signal_ablation/{key}": value
                                for key, value in validation_ablation.items()
                            },
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

    loss_ema: float | None = None
    clipped_steps = 0
    for epoch in range(epochs):
        model.train()
        epoch_losses = []
        for batch_index, batch in enumerate(train_loader):
            if time.monotonic() >= deadline:
                stop_reason = "wall_time_limit"
                break
            with training_precision_context():
                loss = model.compute_loss(batch) / accumulation
            loss.backward()
            epoch_losses.append(float(loss.detach().cpu()) * accumulation)
            should_step = (batch_index + 1) % accumulation == 0 or batch_index + 1 == len(train_loader)
            if not should_step:
                continue
            step_loss = float(np.mean(epoch_losses[-max(1, accumulation) :]))
            if not math.isfinite(step_loss):
                raise FloatingPointError(f"Non-finite training loss at step {global_step + 1}: {step_loss}")
            loss_ema = step_loss if loss_ema is None else 0.9 * loss_ema + 0.1 * step_loss
            next_step = global_step + 1
            log_every_steps = int(config["observability"]["log_every_steps"])
            should_log = next_step % log_every_steps == 0 or next_step == 1
            optimization_fields = optimizer_group_stats(optimizer) if should_log else {}
            parameter_snapshots = optimizer_group_snapshots(optimizer) if should_log else {}
            if should_log and not all(math.isfinite(float(value)) for value in optimization_fields.values()):
                raise FloatingPointError(f"Non-finite optimizer statistics at step {next_step}")
            grad_norm = float(torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0).detach().cpu())
            if not math.isfinite(grad_norm):
                raise FloatingPointError(f"Non-finite gradient norm at step {next_step}: {grad_norm}")
            clipped_steps += int(grad_norm > 1.0)
            optimizer.step()
            scheduler.step()
            if should_log:
                optimization_fields.update(
                    optimizer_group_update_stats(optimizer, parameter_snapshots, optimization_fields)
                )
                if not all(math.isfinite(float(value)) for value in optimization_fields.values()):
                    raise FloatingPointError(f"Non-finite parameter update statistics at step {next_step}")
            for index, group in enumerate(optimizer.param_groups):
                name = str(group.get("name", f"group_{index}"))
                optimization_fields[f"{name}_learning_rate_poststep"] = float(group["lr"])
            optimizer.zero_grad(set_to_none=True)
            global_step += 1
            if should_log:
                elapsed = time.monotonic() - start_time
                fields = {
                    "epoch": epoch,
                    "step": global_step,
                    "loss": step_loss,
                    "loss_ema": loss_ema,
                    "loss_fraction_of_initial_batch": step_loss / max(initial_loss, 1e-12),
                    "learning_rate": scheduler.get_last_lr()[0],
                    "grad_norm": grad_norm,
                    "gradient_clipped": int(grad_norm > 1.0),
                    "gradient_clip_fraction": clipped_steps / global_step,
                    "examples_seen": global_step * batch_size * accumulation,
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
                for key, value in optimization_fields.items():
                    writer.add_scalar(f"optimization/{key}", value, global_step)
                if wandb_run is not None:
                    wandb_run.log(
                        {
                            "trainer/global_step": global_step,
                            **{f"train/{key}": value for key, value in fields.items()},
                            **{f"optimization/{key}": value for key, value in optimization_fields.items()},
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

        store_runtime_checkpoint(model, run_root / "last_model.pt")
        if not args.smoke:
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

    if args.smoke:
        validate("final", epoch, global_step)

    elapsed = time.monotonic() - start_time
    writer.close()
    write_status(
        status_path,
        state="complete",
        stop_reason=stop_reason,
        global_step=global_step,
        best_validation_loss=best_validation,
        best_grounding_score=(best_grounding_score if math.isfinite(best_grounding_score) else None),
        best_grounding_step=best_grounding_step,
        elapsed_seconds=elapsed,
    )
    emit(
        metrics_path,
        "run_complete",
        stop_reason=stop_reason,
        step=global_step,
        best_validation_loss=best_validation,
        best_grounding_score=(best_grounding_score if math.isfinite(best_grounding_score) else None),
        best_grounding_step=best_grounding_step,
        elapsed_seconds=elapsed,
    )
    if wandb_run is not None:
        if wandb_config.get("log_adapter_artifact", True):
            import wandb

            artifact = wandb.Artifact(f"{args.run_name}-adapter", type="model")
            for name in (
                "best_model.pt",
                "best_grounding_model.pt",
                "run_manifest.json",
                "training_selection_manifest.jsonl",
                "training_probe_manifest.jsonl",
                "metrics.jsonl",
                "status.json",
            ):
                candidate = run_root / name
                if candidate.exists():
                    artifact.add_file(str(candidate))
            wandb_run.log_artifact(artifact)
        wandb_run.summary.update(
            {
                "stop_reason": stop_reason,
                "global_step": global_step,
                "best_validation_loss": best_validation,
                "best_grounding_score": (
                    best_grounding_score if math.isfinite(best_grounding_score) else None
                ),
                "best_grounding_step": best_grounding_step,
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

"""Fair plot-based VLM baseline using the same windows and answer schema."""

from __future__ import annotations

import base64
import json
import urllib.request
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from robot_observability.constants import JOINT_NAMES
from robot_observability.metrics import evaluate_rows, parse_answer
from robot_observability.prepared import PreparedSplit
from robot_observability.qa import answer_payload, channel_descriptions, target_text


@dataclass(frozen=True)
class Demonstration:
    """One train-split multimodal in-context example."""

    image_path: Path
    prompt: str
    answer: str
    record_id: str


def render_plot(signal: np.ndarray, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    time_ms = np.arange(signal.shape[1])
    figure, axes = plt.subplots(7, 1, figsize=(16, 12), sharex=True)
    for index, axis in enumerate(axes):
        axis.plot(time_ms, signal[index], linewidth=0.8, color="#2563eb")
        axis.axhline(0, linewidth=0.5, color="#64748b")
        axis.set_ylabel(JOINT_NAMES[index], rotation=0, labelpad=18)
        axis.grid(alpha=0.18)
    axes[-1].set_xlabel("Time from window start (ms)")
    figure.supylabel("Train-robust-scaled external torque")
    figure.suptitle("KUKA LWR4+ synchronized joint telemetry (1 kHz, 1.024 s)")
    figure.tight_layout()
    figure.savefig(path, dpi=100)
    plt.close(figure)


def _user_content(image_path: Path, prompt: str) -> list[dict[str, object]]:
    encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
    return [
        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{encoded}"}},
        {"type": "text", "text": prompt},
    ]


def build_messages(
    image_path: Path,
    prompt: str,
    demonstrations: Sequence[Demonstration] = (),
) -> list[dict[str, object]]:
    """Build zero- or one-shot chat messages without exposing test labels."""
    messages: list[dict[str, object]] = []
    for demonstration in demonstrations:
        messages.extend(
            [
                {
                    "role": "user",
                    "content": _user_content(demonstration.image_path, demonstration.prompt),
                },
                {"role": "assistant", "content": demonstration.answer},
            ]
        )
    messages.append({"role": "user", "content": _user_content(image_path, prompt)})
    return messages


def call_vllm(
    endpoint: str,
    model: str,
    image_path: Path,
    prompt: str,
    max_tokens: int = 256,
    demonstrations: Sequence[Demonstration] = (),
) -> str:
    body = {
        "model": model,
        "temperature": 0,
        "max_tokens": max_tokens,
        "messages": build_messages(image_path, prompt, demonstrations),
    }
    request = urllib.request.Request(
        f"{endpoint.rstrip('/')}/v1/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=180) as response:
        result = json.load(response)
    return str(result["choices"][0]["message"]["content"])


def _diagnosis_prompt(metadata: dict[str, object]) -> str:
    stats = "\n".join(channel_descriptions(metadata))
    return (
        "Use the plot and supplied signal statistics as evidence. Diagnose the complete telemetry window. "
        "Return one short evidence sentence, then `Answer:` and compact valid JSON with exactly these keys: "
        "contact, event_type, onset_ms, strongest_joint, affected_joints, evidence_start_ms, evidence_end_ms. "
        "Use null where no event exists. Event types are free, intentional, or accidental.\n\n"
        f"Signal metadata:\n{stats}"
    )


def _one_shot_demonstration(
    prepared_root: Path,
    output_root: Path,
    seed: int,
) -> Demonstration:
    """Select one example independently of all validation/test labels."""
    train = PreparedSplit(prepared_root, "train")
    if not len(train):
        raise ValueError(
            "The train split is empty; one-shot evaluation needs one training example"
        )
    index = int(np.random.default_rng(seed).integers(0, len(train)))
    signal, metadata = train[index]
    image_path = output_root / "demonstration" / f"train-{index:06d}.png"
    render_plot(signal, image_path)
    return Demonstration(
        image_path=image_path,
        prompt=_diagnosis_prompt(metadata),
        answer=target_text(metadata, "summary"),
        record_id=str(metadata["record_id"]),
    )


def evaluate_plot_vlm(
    prepared_root: Path,
    output_root: Path,
    *,
    endpoint: str,
    model: str,
    limit: int = 512,
    shots: int = 0,
    demonstration_seed: int = 20260912,
) -> dict[str, float | int]:
    if shots not in (0, 1):
        raise ValueError("Only zero-shot and one-shot evaluation are supported")
    dataset = PreparedSplit(prepared_root, "test")
    output_root.mkdir(parents=True, exist_ok=False)
    if limit < len(dataset):
        rng = np.random.default_rng(20260912)
        indices = sorted(rng.choice(len(dataset), size=limit, replace=False).tolist())
    else:
        indices = list(range(len(dataset)))
    demonstrations = (
        [_one_shot_demonstration(prepared_root, output_root, demonstration_seed)]
        if shots == 1
        else []
    )
    manifest = {
        "model": model,
        "split": "test",
        "prompting": "one-shot" if shots else "zero-shot",
        "shots": shots,
        "selection": "fixed random subset without replacement",
        "selection_seed": 20260912,
        "requested_samples": limit,
        "demonstration_split": "train" if demonstrations else None,
        "demonstration_seed": demonstration_seed if demonstrations else None,
        "demonstration_record_id": demonstrations[0].record_id if demonstrations else None,
    }
    (output_root / "run_manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    rows = []
    with (output_root / "predictions.jsonl").open("w", encoding="utf-8") as handle:
        for completed, index in enumerate(indices, start=1):
            signal, metadata = dataset[index]
            image_path = output_root / "plots" / f"{index:06d}.png"
            render_plot(signal, image_path)
            prompt = _diagnosis_prompt(metadata)
            output = call_vllm(
                endpoint,
                model,
                image_path,
                prompt,
                demonstrations=demonstrations,
            )
            row = {
                "record_id": metadata["record_id"],
                "target": answer_payload(metadata, "summary"),
                "output": output,
                "prediction": parse_answer(output),
                "prompting": manifest["prompting"],
                "demonstration_record_id": manifest["demonstration_record_id"],
            }
            rows.append(row)
            handle.write(json.dumps(row, sort_keys=True) + "\n")
            print(
                json.dumps(
                    {"completed": completed, "total": len(indices), "record_id": metadata["record_id"]}
                )
            )
    metrics = evaluate_rows(rows)
    (output_root / "metrics.json").write_text(
        json.dumps(metrics, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return metrics

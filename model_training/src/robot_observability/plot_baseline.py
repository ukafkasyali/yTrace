"""Fair plot-based VLM baseline using the same windows and answer schema."""

from __future__ import annotations

import base64
import json
import urllib.request
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from robot_observability.constants import JOINT_NAMES
from robot_observability.metrics import evaluate_rows, parse_answer
from robot_observability.prepared import PreparedSplit
from robot_observability.qa import answer_payload, channel_descriptions


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


def call_vllm(endpoint: str, model: str, image_path: Path, prompt: str, max_tokens: int = 256) -> str:
    encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
    body = {
        "model": model,
        "temperature": 0,
        "max_tokens": max_tokens,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{encoded}"}},
                    {"type": "text", "text": prompt},
                ],
            }
        ],
    }
    request = urllib.request.Request(
        f"{endpoint.rstrip('/')}/v1/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=180) as response:
        result = json.load(response)
    return str(result["choices"][0]["message"]["content"])


def evaluate_plot_vlm(
    prepared_root: Path,
    output_root: Path,
    *,
    endpoint: str,
    model: str,
    limit: int = 512,
) -> dict[str, float | int]:
    dataset = PreparedSplit(prepared_root, "test")
    output_root.mkdir(parents=True, exist_ok=False)
    if limit < len(dataset):
        rng = np.random.default_rng(20260912)
        indices = sorted(rng.choice(len(dataset), size=limit, replace=False).tolist())
    else:
        indices = list(range(len(dataset)))
    rows = []
    with (output_root / "predictions.jsonl").open("w", encoding="utf-8") as handle:
        for completed, index in enumerate(indices, start=1):
            signal, metadata = dataset[index]
            image_path = output_root / "plots" / f"{index:06d}.png"
            render_plot(signal, image_path)
            stats = "\n".join(channel_descriptions(metadata))
            prompt = (
                "Use the plot and supplied signal statistics as evidence. Diagnose the complete telemetry window. "
                "Return one short evidence sentence, then `Answer:` and compact valid JSON with exactly these keys: "
                "contact, event_type, onset_ms, strongest_joint, affected_joints, evidence_start_ms, evidence_end_ms. "
                "Use null where no event exists. Event types are free, intentional, or accidental.\n\n"
                f"Signal metadata:\n{stats}"
            )
            output = call_vllm(endpoint, model, image_path, prompt)
            row = {
                "record_id": metadata["record_id"],
                "target": answer_payload(metadata, "summary"),
                "output": output,
                "prediction": parse_answer(output),
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

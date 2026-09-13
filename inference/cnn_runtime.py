"""Strict, checkpoint-backed inference for the trained 1D CNN baseline."""
from __future__ import annotations

import json
import os
from pathlib import Path
import threading
from typing import Any


EVENT_TYPES = ("free", "intentional", "accidental")
JOINT_NAMES = tuple(f"J{index}" for index in range(1, 8))
DEFAULT_CHANNEL_IDS = tuple(f"joint_{index}" for index in range(1, 8))


class CnnRuntime:
    """Load the CNN only when an explicit, matching deployment config is supplied."""

    def __init__(self, config_path: str | Path | None = None) -> None:
        configured = config_path or os.environ.get("TRACE_CNN_CONFIG")
        self.config_path = Path(configured).expanduser().resolve() if configured else None
        self.config: dict[str, Any] = {}
        self.model = None
        self.torch = None
        self.ready = False
        self.error = "CNN endpoint is not configured"
        self.model_id = "cnn-1d"
        self.revision = "not-loaded"
        self.channel_ids = DEFAULT_CHANNEL_IDS
        self.window_samples = 1024
        self.median: list[float] = []
        self.scale: list[float] = []
        self.clip = 20.0

    @property
    def enabled(self) -> bool:
        return self.config_path is not None

    def _resolve(self, configured_path: str) -> Path:
        path = Path(configured_path).expanduser()
        if not path.is_absolute():
            if self.config_path is None:
                raise RuntimeError("CNN config path is unavailable")
            path = self.config_path.parent / path
        return path.resolve(strict=True)

    def load(self) -> None:
        if not self.config_path:
            return
        try:
            import hashlib
            import numpy as np
            import torch
            from torch import nn

            self.config = json.loads(self.config_path.read_text(encoding="utf-8"))
            self.model_id = str(self.config.get("model_id", "cnn-1d"))
            self.channel_ids = tuple(self.config.get("channel_ids", DEFAULT_CHANNEL_IDS))
            self.window_samples = int(self.config.get("window_samples", 1024))
            if len(self.channel_ids) != 7 or len(set(self.channel_ids)) != 7:
                raise ValueError("CNN config must declare exactly seven distinct channel_ids")
            checkpoint_path = self._resolve(str(self.config["checkpoint_path"]))
            normalization_path = self._resolve(str(self.config["normalization_path"]))
            normalization = json.loads(normalization_path.read_text(encoding="utf-8"))
            self.median = [float(value) for value in normalization["center_nm"]]
            self.scale = [float(value) for value in normalization["scale_nm"]]
            self.clip = float(normalization.get("clip", 20.0))
            if len(self.median) != 7 or len(self.scale) != 7 or any(value <= 0 for value in self.scale):
                raise ValueError("CNN normalization must contain seven positive scales")

            checkpoint = torch.load(checkpoint_path, map_location="cpu", weights_only=True)
            manifest = checkpoint.get("manifest")
            state = checkpoint.get("model_state")
            if not isinstance(manifest, dict) or not isinstance(state, dict):
                raise ValueError("Not a supported CNN checkpoint")
            model_config = manifest.get("config")
            if not isinstance(model_config, dict):
                raise ValueError("CNN checkpoint has no architecture configuration")

            class MultiTaskCnn1D(nn.Module):
                def __init__(self) -> None:
                    super().__init__()
                    blocks: list[nn.Module] = []
                    input_channels = 7
                    for output_channels, kernel_size in zip(
                        model_config["channels"], model_config["kernel_sizes"], strict=True
                    ):
                        kernel = int(kernel_size)
                        blocks.extend((
                            nn.Conv1d(input_channels, int(output_channels), kernel, padding=kernel // 2, bias=False),
                            nn.BatchNorm1d(int(output_channels)),
                            nn.GELU(),
                            nn.MaxPool1d(kernel_size=2, stride=2),
                        ))
                        input_channels = int(output_channels)
                    self.encoder = nn.Sequential(*blocks)
                    self.dropout = nn.Dropout(float(model_config["dropout"]))
                    self.semantics_head = nn.Linear(input_channels, len(EVENT_TYPES))
                    self.onset_head = nn.Conv1d(input_channels, 1, kernel_size=1)
                    self.strongest_joint_head = nn.Linear(input_channels, len(JOINT_NAMES))
                    self.affected_joints_head = nn.Linear(input_channels, len(JOINT_NAMES))

                def forward(self, signal):
                    temporal = self.encoder(signal)
                    pooled = self.dropout(temporal.mean(dim=-1))
                    return {
                        "semantics": self.semantics_head(pooled),
                        "onset": self.onset_head(temporal).squeeze(1),
                        "strongest_joint": self.strongest_joint_head(pooled),
                        "affected_joints": self.affected_joints_head(pooled),
                    }

            requested_device = self.config.get("device") or os.environ.get("TRACE_CNN_DEVICE") or os.environ.get("TRACE_DEVICE", "cpu")
            if requested_device not in ("cpu", "cuda"):
                raise ValueError("CNN device must be cpu or cuda")
            if requested_device == "cuda" and not torch.cuda.is_available():
                raise RuntimeError("CNN CUDA requested but unavailable")
            device = torch.device(requested_device)
            model = MultiTaskCnn1D().to(device)
            model.load_state_dict(state, strict=True)
            model.eval()
            self.model = model
            self.torch = torch
            self.device = device
            self.numpy = np
            self.revision = "sha256:" + hashlib.sha256(checkpoint_path.read_bytes()).hexdigest()
            self.ready = True
            self.error = ""
        except Exception as error:  # Endpoint status must not crash the OpenTSLM service.
            self.ready = False
            self.error = f"CNN initialization failed: {type(error).__name__}"

    def validate_series(self, series: list[dict[str, Any]]) -> None:
        ids = [item.get("channelId") for item in series]
        if set(ids) != set(self.channel_ids) or len(ids) != len(self.channel_ids):
            raise ValueError("CNN requires all seven canonical joint channels")
        lengths = [len(item.get("values", [])) for item in series]
        if lengths != [self.window_samples] * len(self.channel_ids):
            raise ValueError(f"CNN requires exactly {self.window_samples} synchronized samples per channel")

    def predict(self, series: list[dict[str, Any]], cancelled: threading.Event) -> dict[str, Any]:
        if not self.ready or self.model is None or self.torch is None:
            raise RuntimeError("CNN model unavailable")
        if cancelled.is_set():
            raise InterruptedError("Query cancelled")
        self.validate_series(series)
        by_id = {item["channelId"]: item for item in series}
        values = self.numpy.asarray([by_id[channel_id]["values"] for channel_id in self.channel_ids], dtype="float32")
        normalized = self.numpy.clip(
            (values - self.numpy.asarray(self.median, dtype="float32")[:, None])
            / self.numpy.asarray(self.scale, dtype="float32")[:, None],
            -self.clip,
            self.clip,
        )
        with self.torch.inference_mode():
            output = self.model(self.torch.from_numpy(normalized).unsqueeze(0).to(self.device))
            probabilities = output["semantics"].softmax(dim=-1)[0].float().cpu().tolist()
            event_index = int(output["semantics"].argmax(dim=-1).item())
            onset_bin = int(output["onset"].argmax(dim=-1).item())
            bins = int(output["onset"].shape[-1])
            onset_sample = round(onset_bin * (self.window_samples - 1) / max(1, bins - 1))
            strongest_index = int(output["strongest_joint"].argmax(dim=-1).item())
            affected_probabilities = output["affected_joints"].sigmoid()[0].float().cpu().tolist()
        if cancelled.is_set():
            raise InterruptedError("Query cancelled")
        event_type = EVENT_TYPES[event_index]
        labels = [
            {"label": f"event type: {event_type}", "score": float(probabilities[event_index])},
            {"label": f"contact: {'detected' if event_type != 'free' else 'not detected'}", "score": float(1 - probabilities[0])},
        ]
        if event_type != "free":
            strongest = JOINT_NAMES[strongest_index]
            affected = [JOINT_NAMES[index] for index, value in enumerate(affected_probabilities) if value >= 0.5]
            if strongest not in affected:
                affected.insert(0, strongest)
            labels.extend((
                {"label": f"predicted onset: {onset_sample} ms"},
                {"label": f"strongest joint: {strongest}"},
                {"label": "affected joints: " + ", ".join(affected)},
            ))
        return {"labels": labels, "event_type": event_type, "onset_sample": onset_sample if event_type != "free" else None}

"""Tensor-only OpenTSLM checkpoints shared by training and inference."""

from __future__ import annotations

import os
from collections import OrderedDict
from pathlib import Path

import torch


def _cpu_state(module: torch.nn.Module) -> OrderedDict[str, torch.Tensor]:
    return OrderedDict(
        (name, value.detach().cpu().clone()) for name, value in module.state_dict().items()
    )


def runtime_checkpoint(model: object) -> dict[str, object]:
    """Build the minimal state consumed by ``inference.runtime.Runtime``.

    Upstream OpenTSLM also serializes its PEFT configuration object. The runtime
    already declares LoRA topology in JSON, so persisting that Python object is
    redundant and prevents a safe ``torch.load(..., weights_only=True)``.
    """
    lora_enabled = bool(getattr(model, "lora_enabled", False))
    payload: dict[str, object] = {
        "encoder_state": _cpu_state(model.encoder),
        "projector_state": _cpu_state(model.projector),
        "lora_enabled": lora_enabled,
    }
    if lora_enabled:
        lora_state = {
            name: parameter.detach().cpu().clone()
            for name, parameter in model.llm.named_parameters()
            if parameter.requires_grad and "lora_" in name
        }
        if not lora_state:
            raise ValueError("LoRA is enabled but no trainable LoRA parameters were found")
        payload["lora_state"] = lora_state
    return payload


def store_runtime_checkpoint(model: object, path: str | Path) -> None:
    """Atomically store a portable, weights-only checkpoint."""
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.tmp")
    try:
        torch.save(runtime_checkpoint(model), temporary)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)

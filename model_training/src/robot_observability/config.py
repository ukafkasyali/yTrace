"""Typed access to YAML configuration."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True)
class SplitConfig:
    train: float
    validation: float
    test: float


@dataclass(frozen=True)
class DataConfig:
    raw_root: Path
    prepared_root: Path
    sampling_hz: int
    window_samples: int
    event_position_min: int
    event_position_max: int
    train_crops_per_event: int
    eval_crops_per_event: int
    free_guard_samples: int
    pre_event_samples: int
    post_event_samples: int
    evidence_sustain_samples: int
    normalization_clip: float
    normalization_sample_stride: int
    split: SplitConfig
    seed: int

    @classmethod
    def from_yaml(cls, path: Path) -> DataConfig:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
        payload["raw_root"] = Path(payload["raw_root"])
        payload["prepared_root"] = Path(payload["prepared_root"])
        payload["split"] = SplitConfig(**payload["split"])
        return cls(**payload)

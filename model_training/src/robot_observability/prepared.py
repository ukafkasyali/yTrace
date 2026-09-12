"""Memory-mapped prepared dataset access."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np


class PreparedSplit:
    def __init__(self, root: Path, split: str) -> None:
        self.root = root
        self.split = split
        split_root = root / split
        self.signals = np.load(split_root / "signals.npy", mmap_mode="r")
        with (split_root / "records.jsonl").open(encoding="utf-8") as handle:
            self.records = [json.loads(line) for line in handle if line.strip()]
        if len(self.signals) != len(self.records):
            raise ValueError(f"Signal/metadata count mismatch in {split_root}")

    def __len__(self) -> int:
        return len(self.records)

    def __getitem__(self, index: int) -> tuple[np.ndarray, dict[str, object]]:
        return np.asarray(self.signals[index]), self.records[index]

"""Adapter from prepared robot windows to OpenTSLM's sample contract."""

from __future__ import annotations

import hashlib
from pathlib import Path

from robot_observability.prepared import PreparedSplit
from robot_observability.qa import INTENTS, PROMPTS, answer_payload, channel_descriptions, target_text


class RobotQADataset:
    """A torch-compatible dataset without importing torch at module import time."""

    def __init__(
        self,
        root: Path,
        split: str,
        *,
        mode: str = "summary_plus_atomic",
        seed: int = 20260912,
        eos_token: str = "",
    ) -> None:
        self.prepared = PreparedSplit(root, split)
        if mode not in {"mixed", "summary", "summary_plus_atomic", "all_intents"}:
            raise ValueError(f"Unknown prompt mode: {mode}")
        self.mode = mode
        self.seed = seed
        self.eos_token = eos_token

    def __len__(self) -> int:
        multiplier = (
            len(INTENTS) if self.mode == "all_intents" else 2 if self.mode == "summary_plus_atomic" else 1
        )
        return len(self.prepared) * multiplier

    def __getitem__(self, index: int) -> dict[str, object]:
        import torch

        multiplier = (
            len(INTENTS) if self.mode == "all_intents" else 2 if self.mode == "summary_plus_atomic" else 1
        )
        row_index, intent_index = divmod(index, multiplier)
        signal, metadata = self.prepared[row_index]
        if self.mode == "all_intents":
            intent = INTENTS[intent_index]
        elif self.mode == "mixed":
            digest = hashlib.sha256(f"{self.seed}:{metadata['record_id']}:mixed".encode()).digest()
            if digest[0] % 2 == 0:
                intent = "summary"
            else:
                intent = INTENTS[1 + int.from_bytes(digest[1:5], "big") % (len(INTENTS) - 1)]
        elif self.mode == "summary_plus_atomic" and intent_index == 1:
            digest = hashlib.sha256(f"{self.seed}:{metadata['record_id']}:atomic".encode()).digest()
            intent = INTENTS[1 + int.from_bytes(digest[:4], "big") % (len(INTENTS) - 1)]
        else:
            intent = "summary"
        variants = PROMPTS[intent]
        digest = hashlib.sha256(f"{self.seed}:{metadata['record_id']}:{intent}".encode()).digest()
        question = variants[int.from_bytes(digest[:4], "big") % len(variants)]
        schema_keys = list(answer_payload(metadata, intent))
        return {
            "pre_prompt": (
                "You are analyzing synchronized KUKA LWR4+ external-joint-torque telemetry. "
                "Use the numeric time series as primary evidence. "
            ),
            "time_series_text": channel_descriptions(metadata),
            "time_series": torch.from_numpy(signal.astype("float32", copy=True)),
            "post_prompt": (
                f"\nQuestion: {question}\n"
                f"Respond with `Answer:` and valid compact JSON containing only {schema_keys}, "
                "then one short `Evidence:` sentence."
            ),
            "answer": target_text(metadata, intent) + self.eos_token,
            "record_id": metadata["record_id"],
            "intent": intent,
            "metadata": metadata,
        }

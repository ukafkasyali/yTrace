"""Adapter from prepared robot windows to OpenTSLM's sample contract."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from robot_observability.prepared import PreparedSplit
from robot_observability.qa import (
    HELDOUT_PROMPTS,
    INTENTS,
    PROMPTS,
    answer_payload,
    channel_descriptions,
    target_text,
)


def build_sample(
    signal,
    metadata: dict[str, object],
    intent: str,
    *,
    seed: int,
    eos_token: str,
    output_format: str,
    prompt_set: str = "train",
) -> dict[str, object]:
    """Render one signal/metadata pair into the OpenTSLM sample contract."""
    import torch

    prompt_pools = {"train": PROMPTS, "heldout": HELDOUT_PROMPTS}
    if prompt_set not in prompt_pools:
        raise ValueError(f"Unknown prompt set: {prompt_set}")
    variants = prompt_pools[prompt_set][intent]
    digest = hashlib.sha256(f"{seed}:{metadata['record_id']}:{intent}".encode()).digest()
    question = variants[int.from_bytes(digest[:4], "big") % len(variants)]
    schema_keys = list(answer_payload(metadata, intent))
    schema = json.dumps(schema_keys, separators=(",", ":"))
    if output_format == "rationale_then_answer":
        response_contract = (
            "First write `Rationale:` as one natural paragraph grounded in temporal and joint "
            "patterns. Do not use headings inside it or name the interaction class before the "
            f"final line. End with `Answer:` and one closed compact JSON object containing only {schema}. "
        )
    else:
        response_contract = (
            f"Respond with `Answer:` and one closed compact JSON object containing only {schema}. "
        )
    tensor = (
        torch.from_numpy(signal.astype("float32", copy=True))
        if not isinstance(signal, torch.Tensor)
        else signal.detach().clone().float()
    )
    return {
        "pre_prompt": (
            "You are analyzing synchronized KUKA LWR4+ external-joint-torque telemetry. "
            "Use the numeric time series as primary evidence. "
        ),
        "time_series_text": channel_descriptions(metadata),
        "time_series": tensor,
        "post_prompt": (
            f"\nQuestion: {question}\n"
            f"{response_contract}"
            "Use JSON types exactly: booleans are true/false, numbers are unquoted, arrays are "
            "arrays, and missing values are null. Never quote a boolean, number, or null. "
            + ("Then write one short `Evidence:` sentence." if output_format == "answer_then_evidence" else "")
        ),
        "answer": target_text(metadata, intent, output_format) + eos_token,
        "record_id": metadata["record_id"],
        "intent": intent,
        "metadata": metadata,
    }


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
        output_format: str = "answer_then_evidence",
        prompt_set: str = "train",
    ) -> None:
        self.prepared = PreparedSplit(root, split)
        if mode not in {"mixed", "summary", "summary_plus_atomic", "all_intents"}:
            raise ValueError(f"Unknown prompt mode: {mode}")
        self.mode = mode
        self.seed = seed
        self.eos_token = eos_token
        if output_format not in {"answer_only", "answer_then_evidence", "rationale_then_answer"}:
            raise ValueError(f"Unknown output format: {output_format}")
        self.output_format = output_format
        if prompt_set not in {"train", "heldout"}:
            raise ValueError(f"Unknown prompt set: {prompt_set}")
        self.prompt_set = prompt_set

    def __len__(self) -> int:
        multiplier = (
            len(INTENTS) if self.mode == "all_intents" else 2 if self.mode == "summary_plus_atomic" else 1
        )
        return len(self.prepared) * multiplier

    def __getitem__(self, index: int) -> dict[str, object]:
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
        return build_sample(
            signal,
            metadata,
            intent,
            seed=self.seed,
            eos_token=self.eos_token,
            output_format=self.output_format,
            prompt_set=self.prompt_set,
        )

from pathlib import Path

import torch

from robot_observability.checkpoints import runtime_checkpoint, store_runtime_checkpoint


class FakeModel:
    def __init__(self, lora: bool = True) -> None:
        self.encoder = torch.nn.Linear(2, 2)
        self.projector = torch.nn.Linear(2, 1)
        self.lora_enabled = lora
        self.llm = torch.nn.Module()
        self.llm.register_parameter("adapter_lora_A", torch.nn.Parameter(torch.ones(2, 2)))


def test_runtime_checkpoint_contains_only_runtime_state() -> None:
    payload = runtime_checkpoint(FakeModel())
    assert set(payload) == {"encoder_state", "projector_state", "lora_enabled", "lora_state"}
    assert "lora_config" not in payload


def test_checkpoint_round_trips_with_weights_only(tmp_path: Path) -> None:
    path = tmp_path / "model.pt"
    store_runtime_checkpoint(FakeModel(), path)
    loaded = torch.load(path, map_location="cpu", weights_only=True)
    assert loaded["lora_enabled"] is True
    assert list(loaded["lora_state"]) == ["adapter_lora_A"]


def test_lora_state_is_required_when_enabled() -> None:
    model = FakeModel()
    model.llm.adapter_lora_A.requires_grad_(False)
    try:
        runtime_checkpoint(model)
    except ValueError as error:
        assert "no trainable LoRA" in str(error)
    else:
        raise AssertionError("expected missing LoRA state to fail")

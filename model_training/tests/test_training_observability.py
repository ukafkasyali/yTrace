from types import SimpleNamespace

import numpy as np
import pytest

torch = pytest.importorskip("torch")

from robot_observability.train_opentslm import (
    mean_loss,
    optimizer_group_snapshots,
    optimizer_group_stats,
    optimizer_group_update_stats,
    signal_preview,
)


def test_mean_loss_is_weighted_by_supervised_tokens() -> None:
    class FakeTokenizer:
        def __call__(self, answers, **kwargs):
            del kwargs
            width = max(len(answer) for answer in answers)
            mask = torch.zeros((len(answers), width), dtype=torch.int64)
            for index, answer in enumerate(answers):
                mask[index, : len(answer)] = 1
            return SimpleNamespace(attention_mask=mask)

    class FakeModel:
        tokenizer = FakeTokenizer()

        def eval(self):
            return self

        def compute_loss(self, batch):
            return torch.tensor(batch[0]["loss"])

    loader = [
        [{"answer": "a", "loss": 1.0}],
        [{"answer": "bbb", "loss": 3.0}],
    ]
    assert mean_loss(FakeModel(), loader, "cpu") == 2.5


def test_signal_preview_and_optimizer_update_instrumentation() -> None:
    image = signal_preview(
        np.zeros((7, 1024), dtype=np.float32),
        onset_sample=400,
        evidence_start=390,
        evidence_end=500,
    )
    assert image.shape == (280, 512, 3)
    assert image.dtype == np.uint8

    model = torch.nn.Linear(2, 1)
    optimizer = torch.optim.AdamW([{"name": "probe", "params": list(model.parameters()), "lr": 1e-3}])
    model(torch.ones(1, 2)).sum().backward()
    before = optimizer_group_stats(optimizer)
    snapshots = optimizer_group_snapshots(optimizer)
    optimizer.step()
    updates = optimizer_group_update_stats(optimizer, snapshots, before)
    assert before["probe_gradient_norm_preclip"] > 0
    assert before["probe_parameters_with_grad"] == 3
    assert updates["probe_update_norm"] > 0
    assert updates["probe_relative_update"] > 0

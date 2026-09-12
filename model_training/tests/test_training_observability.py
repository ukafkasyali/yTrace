from types import SimpleNamespace

import numpy as np
import pytest

torch = pytest.importorskip("torch")

from robot_observability.train_opentslm import (
    generation_eval,
    mean_loss,
    optimizer_group_snapshots,
    optimizer_group_stats,
    optimizer_group_update_stats,
    signal_preview,
    training_manifest_rows,
    wandb_manifest_table,
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


def test_training_manifest_wandb_table_includes_supervised_answer() -> None:
    sample = {
        "record_id": "record-1",
        "intent": "semantics",
        "post_prompt": "Classify the event.",
        "answer": '{"event_type":"free"}',
        "metadata": {
            "session_id": "session-1",
            "event_type": "free",
            "contact": False,
            "onset_sample": None,
            "strongest_joint": None,
            "affected_joints": [],
            "evidence_start_ms": None,
            "evidence_end_ms": None,
        },
    }

    class FakeDataset:
        def __len__(self):
            return 1

        def __getitem__(self, index):
            assert index == 0
            return sample

    class FakeWandb:
        class Table:
            def __init__(self, *, columns, data):
                self.columns = columns
                self.data = data

    rows = training_manifest_rows(FakeDataset())
    table = wandb_manifest_table(FakeWandb, rows)

    answer_index = table.columns.index("supervised_answer")
    assert rows[0]["supervised_answer"] == sample["answer"]
    assert table.data[0][answer_index] == sample["answer"]


def test_generation_eval_retries_invalid_first_pass_and_reports_it(tmp_path) -> None:
    metadata = {
        "session_id": "session-1",
        "event_type": "free",
        "contact": False,
        "onset_sample": None,
        "strongest_joint": None,
        "affected_joints": [],
        "evidence_start_ms": None,
        "evidence_end_ms": None,
    }

    class FakeDataset:
        def __len__(self):
            return 2

        def __getitem__(self, index):
            return {
                "record_id": f"record-{index}",
                "intent": "contact",
                "metadata": metadata,
            }

    class FakeModel:
        def eval(self):
            return self

        def generate(self, batch, **kwargs):
            if "min_new_tokens" in kwargs:
                return ['Answer: {"contact":false}\nEvidence: retry']
            return ["", 'Answer: {"contact":false}\nEvidence: first pass']

    metrics, rows = generation_eval(FakeModel(), FakeDataset(), tmp_path / "rows.jsonl")
    assert metrics["first_pass/parse_validity"] == 0.5
    assert metrics["parse_validity"] == 1.0
    assert metrics["retry_rate"] == 0.5
    assert metrics["first_pass_blank_rate"] == 0.5
    assert metrics["final_blank_rate"] == 0.0
    assert metrics["rationale_presence"] == 0.0
    assert metrics["rationale_premature_label_rate"] == 0.0
    assert rows[0]["retry_used"] is True
    assert rows[0]["first_pass_output"] == ""

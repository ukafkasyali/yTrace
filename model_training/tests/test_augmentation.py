from collections import Counter

import torch

from robot_observability.augmentation import JointAttributionCurriculumDataset
from robot_observability.qa import INTENTS


def sample(record_id: str, strongest_joint: str | None) -> dict[str, object]:
    contact = strongest_joint is not None
    affected = [strongest_joint, "J6"] if contact and strongest_joint != "J6" else []
    if strongest_joint == "J6":
        affected = ["J6"]
    scores = [float(index) for index in range(1, 8)]
    if strongest_joint is not None:
        scores[int(strongest_joint[1:]) - 1] = 9.0
    metadata = {
        "record_id": record_id,
        "contact": contact,
        "event_type": "accidental" if contact else "free",
        "onset_sample": 400 if contact else None,
        "strongest_joint": strongest_joint,
        "affected_joints": affected,
        "evidence_start_ms": 405 if contact else None,
        "evidence_end_ms": 500 if contact else None,
        "joint_scores": scores,
    }
    return {
        "record_id": record_id,
        "intent": "contact",
        "metadata": metadata,
        "time_series": torch.arange(7, dtype=torch.float32).reshape(7, 1),
        "time_series_text": [],
        "answer": "old",
    }


def test_curriculum_has_three_focused_views_and_remaps_joint_target() -> None:
    original = sample("record-1", "J3")
    curriculum = JointAttributionCurriculumDataset(
        [original], seed=11, output_format="rationale_then_answer", eos_token="<eos>"
    )

    assert len(curriculum) == 3
    assert [curriculum[index]["intent"] for index in range(3)] == [
        "summary",
        "strongest_joint",
        "onset",
    ]
    summary, transformed, onset = [curriculum[index] for index in range(3)]
    assert torch.equal(summary["time_series"], original["time_series"])
    assert torch.equal(onset["time_series"], original["time_series"])
    assert onset["metadata"]["augmentation"]["type"] == "identity"
    permutation = [
        value - 1 for value in transformed["metadata"]["augmentation"]["new_channel_to_old_channel"]
    ]
    inverse = {old: new for new, old in enumerate(permutation)}
    assert transformed["time_series"].flatten().tolist() == permutation
    assert transformed["metadata"]["joint_scores"] == [
        original["metadata"]["joint_scores"][index] for index in permutation
    ]
    assert transformed["metadata"]["strongest_joint"] == f"J{inverse[2] + 1}"
    assert (
        transformed["metadata"]["strongest_joint"]
        == f"J{transformed['metadata']['joint_scores'].index(max(transformed['metadata']['joint_scores'])) + 1}"
    )
    assert transformed["metadata"]["strongest_joint"] in transformed["answer"]
    assert transformed["answer"].endswith("<eos>")
    assert curriculum.source_index(2) == 0


def test_destination_joint_schedule_is_globally_balanced_and_deterministic() -> None:
    rows = [sample(f"record-{index}", f"J{index % 4 + 1}") for index in range(29)]
    first = JointAttributionCurriculumDataset(
        rows, seed=3, output_format="rationale_then_answer", eos_token=""
    )
    second = JointAttributionCurriculumDataset(
        rows, seed=3, output_format="rationale_then_answer", eos_token=""
    )
    destinations = [first[index * 3 + 1]["metadata"]["strongest_joint"] for index in range(29)]
    counts = Counter(destinations)
    assert set(counts) == {f"J{index}" for index in range(1, 8)}
    assert max(counts.values()) - min(counts.values()) <= 1
    assert destinations == [second[index * 3 + 1]["metadata"]["strongest_joint"] for index in range(29)]


def test_curriculum_control_keeps_all_channels_canonical() -> None:
    original = sample("record-control", "J3")
    curriculum = JointAttributionCurriculumDataset(
        [original],
        seed=11,
        output_format="rationale_then_answer",
        eos_token="",
        permute_strongest=False,
    )

    for item in curriculum:
        assert torch.equal(item["time_series"], original["time_series"])
        assert item["metadata"]["augmentation"]["type"] == "identity"


def test_conversational_curriculum_covers_every_supported_intent() -> None:
    curriculum = JointAttributionCurriculumDataset(
        [sample("record-conversational", "J3")],
        seed=11,
        output_format="rationale_then_answer",
        eos_token="",
        permute_strongest=False,
        views=INTENTS,
    )

    assert len(curriculum) == len(INTENTS)
    assert tuple(item["intent"] for item in curriculum) == INTENTS
    assert len({item["answer"].split("Answer:", 1)[0] for item in curriculum}) == len(INTENTS)

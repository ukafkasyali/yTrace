"""Leakage-safe, task-focused training curriculum for robot telemetry."""

from __future__ import annotations

import hashlib

import numpy as np

from robot_observability.constants import JOINT_NAMES
from robot_observability.opentslm_dataset import build_sample


class JointAttributionCurriculumDataset:
    """Create matched summary, joint-attribution, and onset views.

    Only the strongest-joint view permutes channels. Contact examples are assigned
    globally balanced destination joints, and every joint-keyed label is remapped
    atomically. Summary and onset views retain the physical KUKA channel ordering,
    so two thirds of the curriculum remains physically faithful.
    """

    VIEWS = ("summary", "strongest_joint", "onset")

    def __init__(
        self,
        dataset,
        *,
        seed: int,
        output_format: str,
        eos_token: str,
        permute_strongest: bool = True,
    ) -> None:
        self.dataset = dataset
        self.seed = seed
        self.output_format = output_format
        self.eos_token = eos_token
        self.permute_strongest = permute_strongest
        contact_indices = [
            index
            for index in range(len(dataset))
            if dataset[index]["metadata"].get("strongest_joint") is not None
        ]
        digest = hashlib.sha256(f"{seed}:balanced-joint-schedule".encode()).digest()
        shuffled = np.random.default_rng(int.from_bytes(digest[:8], "big")).permutation(contact_indices)
        self.destination_joint = {
            int(base_index): JOINT_NAMES[position % len(JOINT_NAMES)]
            for position, base_index in enumerate(shuffled)
        }

    def __len__(self) -> int:
        return len(self.dataset) * len(self.VIEWS)

    def source_index(self, index: int) -> int:
        return index // len(self.VIEWS)

    def _permutation(
        self,
        record_id: str,
        intent: str,
        old_strongest: str | None,
        destination: str | None,
    ) -> list[int]:
        digest = hashlib.sha256(f"{self.seed}:{record_id}:{intent}:joint-view".encode()).digest()
        rng = np.random.default_rng(int.from_bytes(digest[:8], "big"))
        if old_strongest is None or destination is None:
            raise ValueError("A channel permutation requires source and destination joints")
        old_index = JOINT_NAMES.index(old_strongest)
        new_index = JOINT_NAMES.index(destination)
        remaining = [index for index in range(len(JOINT_NAMES)) if index != old_index]
        rng.shuffle(remaining)
        permutation = np.empty(len(JOINT_NAMES), dtype=int)
        permutation[new_index] = old_index
        other_new = [index for index in range(len(JOINT_NAMES)) if index != new_index]
        permutation[other_new] = remaining
        if np.array_equal(permutation, np.arange(len(JOINT_NAMES))):
            # Preserve the assigned strongest-joint destination while ensuring this
            # view is genuinely augmented.
            first, second = other_new[:2]
            permutation[first], permutation[second] = permutation[second], permutation[first]
        return permutation.tolist()

    def __getitem__(self, index: int) -> dict[str, object]:
        base_index, view_index = divmod(index, len(self.VIEWS))
        intent = self.VIEWS[view_index]
        original = self.dataset[base_index]
        metadata = dict(original["metadata"])
        signal = original["time_series"]
        original_record_id = str(original["record_id"])

        if (
            self.permute_strongest
            and intent == "strongest_joint"
            and metadata.get("strongest_joint") is not None
        ):
            destination = self.destination_joint.get(base_index)
            permutation = self._permutation(
                original_record_id,
                intent,
                str(metadata["strongest_joint"]) if metadata.get("strongest_joint") is not None else None,
                destination,
            )
            old_to_new = {
                JOINT_NAMES[old_index]: JOINT_NAMES[new_index]
                for new_index, old_index in enumerate(permutation)
            }
            signal = signal[permutation].clone()
            for key in ("joint_scores", "raw_mean_nm", "raw_std_nm", "raw_rms_nm"):
                values = metadata.get(key)
                if isinstance(values, list) and len(values) == len(JOINT_NAMES):
                    metadata[key] = [values[old_index] for old_index in permutation]
            if metadata.get("strongest_joint") is not None:
                metadata["strongest_joint"] = old_to_new[str(metadata["strongest_joint"])]
            metadata["affected_joints"] = [
                old_to_new[str(joint)] for joint in metadata.get("affected_joints", [])
            ]
            scores = metadata.get("joint_scores")
            if isinstance(scores, list) and len(scores) == len(JOINT_NAMES):
                score_winner = JOINT_NAMES[int(np.argmax(scores))]
                if score_winner != metadata["strongest_joint"]:
                    raise ValueError(
                        "Atomic channel remap failed: joint_scores disagree with strongest_joint"
                    )
            augmentation = {
                "type": "balanced_channel_permutation",
                "new_channel_to_old_channel": [old_index + 1 for old_index in permutation],
                "destination_strongest_joint": destination,
            }
        else:
            augmentation = {"type": "identity"}

        record_id = f"{original_record_id}::curriculum-{intent}"
        metadata["source_record_id"] = original_record_id
        metadata["record_id"] = record_id
        metadata["augmentation"] = augmentation
        return build_sample(
            signal,
            metadata,
            intent,
            seed=self.seed,
            eos_token=self.eos_token,
            output_format=self.output_format,
        )

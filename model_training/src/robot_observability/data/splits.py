"""Recording-grouped deterministic splits."""

from __future__ import annotations

import random
from collections import defaultdict

from robot_observability.data.raw import RawSessionRef


def stratified_session_split(
    sessions: list[RawSessionRef],
    *,
    train_fraction: float = 0.70,
    validation_fraction: float = 0.15,
    seed: int = 20260912,
) -> dict[str, str]:
    if not 0 < train_fraction < 1 or not 0 < validation_fraction < 1:
        raise ValueError("Split fractions must be between zero and one")
    if train_fraction + validation_fraction >= 1:
        raise ValueError("Train and validation fractions must leave a test split")
    grouped: dict[str, list[RawSessionRef]] = defaultdict(list)
    for session in sessions:
        grouped[session.event_type].append(session)

    result: dict[str, str] = {}
    rng = random.Random(seed)
    for event_type, group in sorted(grouped.items()):
        ordered = sorted(group, key=lambda item: item.session_id)
        rng.shuffle(ordered)
        n_train = round(len(ordered) * train_fraction)
        n_validation = round(len(ordered) * validation_fraction)
        if not n_train or not n_validation or n_train + n_validation >= len(ordered):
            raise ValueError(f"Not enough {event_type} sessions for a three-way split")
        for index, session in enumerate(ordered):
            split = "train" if index < n_train else "validation" if index < n_train + n_validation else "test"
            result[session.session_id] = split
    return result

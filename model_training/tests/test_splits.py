from pathlib import Path

from robot_observability.data.raw import RawSessionRef
from robot_observability.data.splits import stratified_session_split


def make_ref(label: str, index: int) -> RawSessionRef:
    path = Path(f"/{label}/{index}")
    return RawSessionRef(f"{label}/{index}", label, path, path / "torque", path / "moments")


def test_split_is_deterministic_and_grouped() -> None:
    refs = [make_ref(label, index) for label in ("accidental", "intentional") for index in range(20)]
    first = stratified_session_split(refs, seed=42)
    second = stratified_session_split(refs, seed=42)
    assert first == second
    assert {split for split in first.values()} == {"train", "validation", "test"}
    assert len(first) == len(refs)

"""Application-facing entry points for producing TimeF datasets."""

from pathlib import Path

from timenet.engine import store_dataset

from .kuka_contact import KukaContactPart2Connector
from .kuka_collision import KukaCollisionPart1Connector


_CONNECTORS = {
    "kuka/collision-part1": KukaCollisionPart1Connector,
    "kuka/contact-part2": KukaContactPart2Connector,
}


def build_kuka_timef_dataset(
    dataset_id: str, source_root: str | Path, registry_root: str | Path
) -> Path:
    """Build one supported KUKA part into a TimeF registry.

    ``dataset_id`` is deliberately explicit so batches from Part I and Part II cannot be
    accidentally combined or assigned the wrong event semantics.
    """
    try:
        connector = _CONNECTORS[dataset_id]()
    except KeyError as error:
        supported = ", ".join(sorted(_CONNECTORS))
        raise ValueError(
            f"unsupported KUKA dataset_id {dataset_id!r}; choose one of: {supported}"
        ) from error
    dataset = connector.convert(connector.discover(Path(source_root)))
    return store_dataset(
        dataset,
        Path(registry_root),
        values_backend=connector.values_backend,
    )


def build_kuka_collision_part1(source_root: str | Path, registry_root: str | Path) -> Path:
    """Build the local Part I source directly, without TimeNet connector discovery.

    Returns:
        The written TimeF dataset-version directory.
    """
    return build_kuka_timef_dataset("kuka/collision-part1", source_root, registry_root)


def build_kuka_contact_part2(source_root: str | Path, registry_root: str | Path) -> Path:
    """Build the local Part II source directly, without TimeNet connector discovery."""
    return build_kuka_timef_dataset("kuka/contact-part2", source_root, registry_root)

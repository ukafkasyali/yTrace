"""Application-facing entry points for producing TimeF datasets."""

from pathlib import Path

from timenet.engine import store_dataset

from .kuka_contact import KukaContactPart2Connector
from .kuka_collision import KukaCollisionPart1Connector


def build_kuka_collision_part1(source_root: str | Path, registry_root: str | Path) -> Path:
    """Build the local Part I source directly, without TimeNet connector discovery.

    Returns:
        The written TimeF dataset-version directory.
    """
    connector = KukaCollisionPart1Connector()
    dataset = connector.convert(connector.discover(Path(source_root)))
    return store_dataset(
        dataset,
        Path(registry_root),
        values_backend=connector.values_backend,
    )


def build_kuka_contact_part2(source_root: str | Path, registry_root: str | Path) -> Path:
    """Build the local Part II source directly, without TimeNet connector discovery."""
    connector = KukaContactPart2Connector()
    dataset = connector.convert(connector.discover(Path(source_root)))
    return store_dataset(
        dataset,
        Path(registry_root),
        values_backend=connector.values_backend,
    )

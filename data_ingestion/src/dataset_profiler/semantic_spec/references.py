"""Load packaged, canonical DatasetSpec reference artifacts."""

from importlib.resources import files

from .models import DatasetSpec


def load_kuka_collision_part1_spec() -> DatasetSpec:
    resource = files(__package__).joinpath("specs/kuka_collision_part1.json")
    return DatasetSpec.from_json(resource.read_text(encoding="utf-8"))

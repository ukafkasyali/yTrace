"""TimeF connector for Part I of the KUKA accidental-collision dataset."""

from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache
import os
from pathlib import Path

import numpy as np
import pyarrow as pa
from timenet.connectors import BaseConnector
from timenet.dataset import TimeFDataset, TimeSeries
from timenet.dataset.axis import RegularAxis
from timenet.types import Annotation, DataSource, TimePoint, TimeSeriesSpec, ureg

from ...datasets import (
    TimedJointMatrix,
    collision_time_seconds,
    discover_kuka_runs,
    parse_collision_indices,
    parse_time_axis,
    parse_timed_joint_matrix,
)

_ROOT_ENV = "KUKA_PART1_ROOT"
_RATE_HZ = 1000
_SOURCE = DataSource(
    data_source_type="robot_experiment",
    name="KUKA LWR4+ accidental-collision experiment",
    provider="Technical University of Munich",
)
_TORQUE = TimeSeriesSpec(
    spec_type="measured_external_joint_torque",
    name="Measured external joint torque",
    unit_value=ureg.Unit("newton * meter"),
    data_source=_SOURCE,
    dtype="float64",
)
_POSITION = TimeSeriesSpec(
    spec_type="measured_joint_position",
    name="Measured joint position (source unit unresolved)",
    unit_value=ureg.dimensionless,
    data_source=_SOURCE,
    dtype="float64",
)


@dataclass(frozen=True)
class KukaRunRef:
    """Lightweight reference to one locally extracted experimental run."""

    run_dir: Path
    source_run_id: str
    source_files: tuple[str, ...]
    source_subset: str


def _series_loaders(
    path: Path, variable: str, expected_time: np.ndarray
) -> tuple[Callable[[], pa.Array], ...]:
    @lru_cache(maxsize=1)
    def decoded() -> TimedJointMatrix:
        return parse_timed_joint_matrix(path, variable, expected_time=expected_time)

    def load(channel: int) -> pa.Array:
        return pa.array(decoded().channels[channel], type=pa.float64())

    return tuple(lambda channel=channel: load(channel) for channel in range(7))


def _run_key(source_run_id: str) -> str:
    return source_run_id.replace("/", "--")


class KukaCollisionPart1Connector(BaseConnector[KukaRunRef]):
    """Convert local KUKA Part I runs into raw-run TimeF records."""

    def discover(self, source_root: Path) -> list[KukaRunRef]:
        """Discover complete runs beneath a batch directory or future Part I root."""
        root = source_root.expanduser().resolve()
        runs = discover_kuka_runs(root)
        if not runs:
            raise RuntimeError(f"no complete KUKA Part I runs found under {root}")
        return [self._run_ref(root, run_dir) for run_dir in runs]

    def download(self, cache_dir: Path) -> list[KukaRunRef]:  # noqa: ARG002
        configured = os.environ.get(_ROOT_ENV)
        if not configured:
            raise RuntimeError(f"{_ROOT_ENV} must name an extracted Part I root or batch directory")
        return self.discover(Path(configured))

    @staticmethod
    def _run_ref(root: Path, run_dir: Path) -> KukaRunRef:
        source_run_id = run_dir.relative_to(root).as_posix() if run_dir != root else run_dir.name
        parts = Path(source_run_id).parts
        subset = parts[0] if len(parts) > 1 else root.name
        return KukaRunRef(
            run_dir=run_dir,
            source_run_id=source_run_id,
            source_files=tuple(
                sorted(path.relative_to(root).as_posix() for path in run_dir.iterdir() if path.is_file())
            ),
            source_subset=subset,
        )

    def convert(self, raw_refs: list[KukaRunRef]) -> TimeFDataset:
        dataset = TimeFDataset(metadata=self.metadata())
        for raw_ref in sorted(raw_refs, key=lambda ref: ref.source_run_id):
            self._add_run(dataset, raw_ref)
        return dataset

    @staticmethod
    def _add_run(dataset: TimeFDataset, raw_ref: KukaRunRef) -> None:
        time_axis = parse_time_axis(raw_ref.run_dir)
        expected_time = np.arange(time_axis.size, dtype=np.float64) / _RATE_HZ
        if not np.allclose(time_axis, expected_time, rtol=0.0, atol=1e-12):
            raise ValueError(f"{raw_ref.run_dir} does not have the expected zero-based 1 kHz time axis")

        axis = RegularAxis.from_rate_hz(_RATE_HZ)
        run_key = _run_key(raw_ref.source_run_id)
        torque = _series_loaders(raw_ref.run_dir / "JK_MsrExtTrq.mat", "MsrExtTrq", time_axis)
        position = _series_loaders(raw_ref.run_dir / "JK_PosMsr.mat", "PosMsr", time_axis)
        series = tuple(
            TimeSeries(
                spec=spec,
                signal=f"joint_{joint}",
                time_axis=axis,
                loader=loaders[joint - 1],
                source_id=raw_ref.source_run_id,
                time_series_id=f"kuka-part1-{run_key}-{kind}-joint-{joint}",
                n_values=int(time_axis.size),
            )
            for kind, spec, loaders in (
                ("external-torque", _TORQUE, torque),
                ("position", _POSITION, position),
            )
            for joint in range(1, 8)
        )
        record_id = f"kuka-part1-{run_key}"
        record = dataset.add_record(time_series=series, record_id=record_id)
        record.add_annotations(
            [
                Annotation(key="source_run_id", value=raw_ref.source_run_id,
                           source="KUKA source directory", id=f"{record_id}-source-run-id"),
                Annotation(key="source_files", value=list(raw_ref.source_files),
                           source="KUKA source directory", id=f"{record_id}-source-files"),
                Annotation(key="source_subset", value=raw_ref.source_subset,
                           source="connector discovery", id=f"{record_id}-source-subset"),
                Annotation(
                    key="unit_resolution",
                    value={"spec_type": _POSITION.spec_type, "status": "unresolved",
                           "timef_unit": "dimensionless", "timef_unit_is_placeholder": True},
                    source="manual KUKA-to-TimeF mapping",
                    id=f"{record_id}-position-unit-resolution",
                ),
            ]
        )
        collision_source = f"{raw_ref.source_run_id}/JK_moments.mat"
        indices = parse_collision_indices(raw_ref.run_dir / "JK_moments.mat", n_samples=int(time_axis.size))
        for number, raw_index in enumerate(indices, start=1):
            matlab_index = int(raw_index)
            python_index = matlab_index - 1
            timestamp = collision_time_seconds(time_axis, matlab_index)
            record.add_annotation(
                Annotation(
                    key="collision",
                    value={"label": "collision", "object": "ball", "matlab_index": matlab_index,
                           "python_index": python_index, "timestamp_seconds": timestamp},
                    span=TimePoint.seconds(timestamp),
                    source=collision_source,
                    id=f"{record_id}-collision-{number:03d}",
                )
            )


CONNECTOR = KukaCollisionPart1Connector

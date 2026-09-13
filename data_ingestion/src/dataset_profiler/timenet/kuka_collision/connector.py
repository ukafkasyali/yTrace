"""Shared TimeF connector implementation for separately identified KUKA parts."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from functools import lru_cache
import os
from pathlib import Path
from typing import ClassVar

import numpy as np
import pyarrow as pa
from timenet.connectors import BaseConnector
from timenet.dataset import TimeFDataset, TimeSeries
from timenet.dataset.axis import RegularAxis
from timenet.types import Annotation, DataSource, TimePoint, TimeSeriesSpec, ureg

from ...datasets import (
    TimedJointMatrix,
    discover_kuka_runs,
    event_time_seconds,
    parse_event_indices,
    parse_time_axis,
    parse_timed_joint_matrix,
)

_RATE_HZ = 1000


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


class KukaPartConnector(BaseConnector[KukaRunRef]):
    """Reusable raw-run KUKA connector; subclasses supply only identity semantics."""

    ROOT_ENV: ClassVar[str]
    PART_LABEL: ClassVar[str]
    RECORD_PREFIX: ClassVar[str]
    EVENT_KEY: ClassVar[str]
    EVENT_LABEL: ClassVar[str]
    EVENT_OBJECT: ClassVar[str | None] = None
    EXPERIMENT_NAME: ClassVar[str]

    def discover(self, source_root: Path) -> list[KukaRunRef]:
        """Discover complete runs beneath a batch directory or a part root."""
        root = source_root.expanduser().resolve()
        runs = discover_kuka_runs(root)
        if not runs:
            raise RuntimeError(f"no complete KUKA {self.PART_LABEL} runs found under {root}")
        return [self._run_ref(root, run_dir) for run_dir in runs]

    def discover_subsets(
        self, source_subsets: list[tuple[str, Path]]
    ) -> list[KukaRunRef]:
        """Discover runs from separately extracted archives with stable subset identities."""
        refs: list[KukaRunRef] = []
        seen: set[str] = set()
        for subset, source_root in source_subsets:
            if not subset or "/" in subset or "\\" in subset:
                raise ValueError(f"invalid KUKA source subset {subset!r}")
            for ref in self.discover(source_root):
                source_run_id = f"{subset}/{ref.source_run_id}"
                if source_run_id in seen:
                    raise RuntimeError(f"duplicate KUKA source run {source_run_id}")
                seen.add(source_run_id)
                refs.append(
                    replace(
                        ref,
                        source_run_id=source_run_id,
                        source_files=tuple(
                            f"{subset}/{source_file}" for source_file in ref.source_files
                        ),
                        source_subset=subset,
                    )
                )
        return refs

    def download(self, cache_dir: Path) -> list[KukaRunRef]:  # noqa: ARG002
        configured = os.environ.get(self.ROOT_ENV)
        if not configured:
            raise RuntimeError(
                f"{self.ROOT_ENV} must name an extracted {self.PART_LABEL} root or batch directory"
            )
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

    @classmethod
    def _specs(cls) -> tuple[TimeSeriesSpec, TimeSeriesSpec]:
        source = DataSource(
            data_source_type="robot_experiment",
            name=cls.EXPERIMENT_NAME,
            provider="Technical University of Munich",
        )
        return (
            TimeSeriesSpec(
                spec_type="measured_external_joint_torque",
                name="Measured external joint torque",
                unit_value=ureg.Unit("newton * meter"),
                data_source=source,
                dtype="float64",
            ),
            TimeSeriesSpec(
                spec_type="measured_joint_position",
                name="Measured joint position",
                unit_value=ureg.radian,
                data_source=source,
                dtype="float64",
            ),
        )

    def convert(self, raw_refs: list[KukaRunRef]) -> TimeFDataset:
        dataset = TimeFDataset(metadata=self.metadata())
        for raw_ref in sorted(raw_refs, key=lambda ref: ref.source_run_id):
            self._add_run(dataset, raw_ref)
        return dataset

    @classmethod
    def _add_run(cls, dataset: TimeFDataset, raw_ref: KukaRunRef) -> None:
        time_axis = parse_time_axis(raw_ref.run_dir)
        expected_time = np.arange(time_axis.size, dtype=np.float64) / _RATE_HZ
        if not np.allclose(time_axis, expected_time, rtol=0.0, atol=1e-12):
            raise ValueError(f"{raw_ref.run_dir} does not have the expected zero-based 1 kHz time axis")

        axis = RegularAxis.from_rate_hz(_RATE_HZ)
        run_key = _run_key(raw_ref.source_run_id)
        torque_spec, position_spec = cls._specs()
        torque = _series_loaders(raw_ref.run_dir / "JK_MsrExtTrq.mat", "MsrExtTrq", time_axis)
        position = _series_loaders(raw_ref.run_dir / "JK_PosMsr.mat", "PosMsr", time_axis)
        series = tuple(
            TimeSeries(
                spec=spec,
                signal=f"joint_{joint}",
                time_axis=axis,
                loader=loaders[joint - 1],
                source_id=raw_ref.source_run_id,
                time_series_id=f"{cls.RECORD_PREFIX}-{run_key}-{kind}-joint-{joint}",
                n_values=int(time_axis.size),
            )
            for kind, spec, loaders in (
                ("external-torque", torque_spec, torque),
                ("position", position_spec, position),
            )
            for joint in range(1, 8)
        )
        record_id = f"{cls.RECORD_PREFIX}-{run_key}"
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
                    value={
                        "spec_type": position_spec.spec_type,
                        "unit": "radian",
                        "unit_resolution": "inferred",
                        "confidence": "high",
                    },
                    source="manual KUKA-to-TimeF mapping",
                    id=f"{record_id}-position-unit-resolution",
                ),
            ]
        )
        event_source = f"{raw_ref.source_run_id}/JK_moments.mat"
        indices = parse_event_indices(
            raw_ref.run_dir / "JK_moments.mat", n_samples=int(time_axis.size)
        )
        for number, raw_index in enumerate(indices, start=1):
            matlab_index = int(raw_index)
            python_index = matlab_index - 1
            timestamp = event_time_seconds(time_axis, matlab_index)
            value: dict[str, object] = {
                "label": cls.EVENT_LABEL,
                "matlab_index": matlab_index,
                "python_index": python_index,
                "timestamp_seconds": timestamp,
                "source_variable": "JK_moments",
                "source_file": event_source,
            }
            if cls.EVENT_OBJECT is not None:
                value["object"] = cls.EVENT_OBJECT
            record.add_annotation(
                Annotation(
                    key=cls.EVENT_KEY,
                    value=value,
                    span=TimePoint.seconds(timestamp),
                    source=event_source,
                    id=f"{record_id}-{cls.EVENT_KEY}-{number:03d}",
                )
            )


class KukaCollisionPart1Connector(KukaPartConnector):
    """Convert local KUKA Part I accidental-collision runs into TimeF records."""

    ROOT_ENV = "KUKA_PART1_ROOT"
    PART_LABEL = "Part I"
    RECORD_PREFIX = "kuka-part1"
    EVENT_KEY = "collision"
    EVENT_LABEL = "collision"
    EVENT_OBJECT = "ball"
    EXPERIMENT_NAME = "KUKA LWR4+ accidental-collision experiment"


CONNECTOR = KukaCollisionPart1Connector

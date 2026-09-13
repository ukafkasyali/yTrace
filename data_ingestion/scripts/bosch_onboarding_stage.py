"""Structured Bosch reference checks invoked by the generic onboarding adapter."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import re
from typing import Any

import h5py
import numpy as np

from timenet.client import TimeNet
from timenet.dataset.axis import RegularAxis
from timenet.types import ClassificationTask, ureg
from bosch_handoff_contract import DATASET_ID


_DATASET_ID = DATASET_ID
_FILENAME = re.compile(
    r"^(?P<machine>M\d{2})_(?P<timeframe>[A-Za-z]{3}_\d{4})_"
    r"(?P<process>OP\d{2})_(?P<example>\d+)\.h5$"
)


@dataclass(frozen=True)
class SourceRecord:
    """Expected record identity derived independently from one verified source path."""

    path: Path
    source_file: str
    record_id: str
    machine_number: str
    process_number: str
    process_health: str
    timeframe: str
    example_number: str


def _write(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )


def _discover_source(source_root: Path) -> list[SourceRecord]:
    """Derive expected records without importing connector implementation code."""
    data_root = source_root / "data" if (source_root / "data").is_dir() else source_root
    records = []
    for path in sorted(data_root.rglob("*.h5")):
        relative = path.relative_to(data_root)
        if len(relative.parts) != 4:
            raise ValueError(f"unexpected Bosch source hierarchy: {relative}")
        machine, process, health, filename = relative.parts
        match = _FILENAME.fullmatch(filename)
        if (
            match is None
            or health not in {"good", "bad"}
            or match["machine"] != machine
            or match["process"] != process
        ):
            raise ValueError(f"invalid Bosch source identity: {relative}")
        records.append(
            SourceRecord(
                path=path,
                source_file=relative.as_posix(),
                record_id=relative.with_suffix("").as_posix(),
                machine_number=machine,
                process_number=process,
                process_health=health,
                timeframe=match["timeframe"],
                example_number=match["example"],
            )
        )
    if not records:
        raise ValueError(f"no Bosch HDF5 records found below {data_root}")
    return records


def load(registry: Path, output: Path) -> None:
    """Exercise TimeNet.load and record a compact dataset summary."""
    dataset = TimeNet(registry=registry).load(_DATASET_ID)
    _write(
        output,
        {
            "passed": True,
            "dataset_id": dataset.metadata.dataset_id,
            "dataset_version": str(dataset.metadata.dataset_version),
            "record_count": len(dataset.records),
            "time_series_count": sum(
                len(record.time_series) for record in dataset.records
            ),
            "task_count": len(dataset.tasks),
            "registry": str(registry.resolve()),
            "api": "timenet.client.TimeNet.load",
        },
    )


def verify(source_root: Path, registry: Path, output: Path) -> None:
    """Compare every raw Bosch recording with its loaded TimeF representation."""
    sources = _discover_source(source_root.resolve())
    dataset = TimeNet(registry=registry).load(_DATASET_ID)
    records_by_id = {record.record_id: record for record in dataset.records}
    records = {}
    for record in dataset.records:
        annotations = {item.key: item.value for item in record.annotations}
        source_file = annotations.get("source_file")
        if not isinstance(source_file, str) or source_file in records:
            raise ValueError("TimeF records require unique source_file provenance")
        records[source_file] = record
    tasks = {
        task.record_ids[0]: task
        for task in dataset.tasks
        if isinstance(task, ClassificationTask) and len(task.record_ids) == 1
    }
    checks = {
        "record_count": len(records_by_id) == len(sources),
        "unique_record_ids": len(records_by_id) == len(dataset.records),
        "deterministic_record_mapping": set(records)
        == {source.source_file for source in sources},
        "signal_lengths": True,
        "channel_mapping": True,
        "numeric_values": True,
        "sampling_rate": True,
        "start_index": True,
        "unit": True,
        "classification_labels": True,
        "metadata": True,
        "dtype_conversion": True,
        "no_fabricated_timestamps": True,
        "no_fabricated_start_times": True,
        "no_fabricated_events": True,
    }
    compared_values = 0
    for source in sources:
        record = records[source.source_file]
        with h5py.File(source.path, "r") as handle:
            raw = np.asarray(handle["vibration_data"])
        series_by_signal = {series.signal: series for series in record.time_series}
        checks["channel_mapping"] &= tuple(series_by_signal) == ("x", "y", "z")
        for channel, signal in enumerate(("x", "y", "z")):
            series = series_by_signal[signal]
            actual = series.to_numpy()
            expected = raw[:, channel].astype(np.float64, copy=False)
            checks["signal_lengths"] &= series.n_values == raw.shape[0]
            checks["numeric_values"] &= np.array_equal(actual, expected)
            checks["dtype_conversion"] &= actual.dtype == np.dtype("float64")
            checks["sampling_rate"] &= series.time_axis == RegularAxis.from_rate_hz(
                2000
            )
            checks["start_index"] &= series.time_axis.start_index == 0
            checks["unit"] &= series.spec.unit_value == ureg.Unit("milligravity")
            checks["no_fabricated_timestamps"] &= series.time_offsets_loader is None
            compared_values += actual.size
        annotations = {item.key: item.value for item in record.annotations}
        expected_metadata = {
            "source_file": source.source_file,
            "machine_number": source.machine_number,
            "process_number": source.process_number,
            "process_health": source.process_health,
            "timeframe": source.timeframe,
            "example_number": source.example_number,
        }
        checks["metadata"] &= annotations == expected_metadata
        task = tasks.get(record.record_id)
        checks["classification_labels"] &= (
            task is not None
            and task.target == source.process_health
            and task.target_schema == "process_health"
            and task.scope is None
        )
        checks["no_fabricated_start_times"] &= record.start_time is None
        checks["no_fabricated_events"] &= all(
            item.span is None for item in record.annotations
        )
    _write(
        output,
        {
            "passed": all(checks.values()),
            "dataset_id": _DATASET_ID,
            "source_record_count": len(sources),
            "timef_record_count": len(records_by_id),
            "compared_numeric_values": compared_values,
            "checks": checks,
            "method": "full raw HDF5 arrays compared with TimeNet.load() output",
        },
    )


def main() -> int:
    """Run one Bosch reference stage."""
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    load_parser = subparsers.add_parser("load")
    load_parser.add_argument("--registry", type=Path, required=True)
    load_parser.add_argument("--output", type=Path, required=True)
    verify_parser = subparsers.add_parser("verify")
    verify_parser.add_argument("--source", type=Path, required=True)
    verify_parser.add_argument("--registry", type=Path, required=True)
    verify_parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "load":
        load(args.registry, args.output)
    else:
        verify(args.source, args.registry, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

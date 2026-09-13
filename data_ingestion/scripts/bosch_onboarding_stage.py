"""Structured Bosch reference checks invoked by the generic onboarding adapter."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import h5py
import numpy as np

from timenet.client import TimeNet
from timenet.dataset.axis import RegularAxis
from timenet.types import ClassificationTask, ureg
from timenet_connectors.datasets.boschresearch.cnc_machining.connector import (
    BoschCncConnector,
    _discover,
    _local_data_root,
)


_DATASET_ID = "boschresearch/cnc-machining"


def _write(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )


def implementation(handoff_path: Path, output: Path) -> None:
    """Confirm that the authoritative handoff can reach the native connector."""
    handoff_bytes = handoff_path.read_bytes()
    handoff = json.loads(handoff_bytes)
    metadata = BoschCncConnector().metadata()
    passed = (
        handoff.get("connector_ready") is True and metadata.dataset_id == _DATASET_ID
    )
    _write(
        output,
        {
            "passed": passed,
            "mode": "reused_existing_native_connector",
            "connector_class": (
                "timenet_connectors.datasets.boschresearch.cnc_machining."
                "connector.BoschCncConnector"
            ),
            "dataset_id": metadata.dataset_id,
            "connector_handoff_sha256": hashlib.sha256(handoff_bytes).hexdigest(),
            "semantic_input": str(handoff_path),
        },
    )


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
    sources = _discover(_local_data_root(str(source_root)))
    dataset = TimeNet(registry=registry).load(_DATASET_ID)
    records = {record.record_id: record for record in dataset.records}
    tasks = {
        task.record_ids[0]: task
        for task in dataset.tasks
        if isinstance(task, ClassificationTask) and len(task.record_ids) == 1
    }
    checks = {
        "record_count": len(records) == len(sources),
        "deterministic_record_mapping": set(records)
        == {source.record_id for source in sources},
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
        record = records[source.record_id]
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
        task = tasks.get(source.record_id)
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
            "timef_record_count": len(records),
            "compared_numeric_values": compared_values,
            "checks": checks,
            "method": "full raw HDF5 arrays compared with TimeNet.load() output",
        },
    )


def main() -> int:
    """Run one Bosch reference stage."""
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    implementation_parser = subparsers.add_parser("implementation")
    implementation_parser.add_argument("--handoff", type=Path, required=True)
    implementation_parser.add_argument("--output", type=Path, required=True)
    load_parser = subparsers.add_parser("load")
    load_parser.add_argument("--registry", type=Path, required=True)
    load_parser.add_argument("--output", type=Path, required=True)
    verify_parser = subparsers.add_parser("verify")
    verify_parser.add_argument("--source", type=Path, required=True)
    verify_parser.add_argument("--registry", type=Path, required=True)
    verify_parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "implementation":
        implementation(args.handoff, args.output)
    elif args.command == "load":
        load(args.registry, args.output)
    else:
        verify(args.source, args.registry, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

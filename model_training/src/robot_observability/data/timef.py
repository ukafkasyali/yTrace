"""Validated adapter from canonical KUKA TimeF records to training sessions.

TimeF stays the source of truth.  This module exposes the small recording contract
used by the leakage-safe window builder without teaching the trainer about raw
MATLAB files or TimeNet's storage internals.
"""

from __future__ import annotations

import hashlib
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from robot_observability.constants import N_JOINTS
from robot_observability.data.raw import EventType, RawSession

_DATASET_CONTRACTS: dict[str, tuple[EventType, str, str]] = {
    "kuka/collision-part1": ("accidental", "collision", "collision"),
    "kuka/contact-part2": (
        "intentional",
        "intentional_contact",
        "intentional_contact",
    ),
}
_TORQUE_SPEC_TYPE = "measured_external_joint_torque"
_SAMPLING_HZ = 1000


@dataclass(frozen=True)
class TimeFSessionRef:
    """A single continuous KUKA recording stored in one TimeF dataset version."""

    session_id: str
    event_type: EventType
    version_dir: Path
    record_id: str
    source_run_id: str
    event_key: str
    event_label: str


def _annotation_values(record: Any, key: str) -> list[Any]:
    return [annotation.value for annotation in record.annotations if annotation.key == key]


def _one_annotation(record: Any, key: str) -> Any:
    values = _annotation_values(record, key)
    if len(values) != 1:
        raise ValueError(
            f"TimeF record {record.record_id!r} must contain exactly one {key!r} "
            f"annotation, found {len(values)}"
        )
    return values[0]


def _dataset_identity(version: Any) -> tuple[str, str]:
    metadata = version.manifest.metadata
    return str(metadata.dataset_id), str(metadata.dataset_version)


def _session_ids(
    records: list[tuple[Path, str, EventType, str, str, str]],
) -> list[TimeFSessionRef]:
    """Match the legacy canonical ids while retaining collision-safe provenance."""
    short = [(event_type, Path(source_run_id).name) for _, _, event_type, source_run_id, _, _ in records]
    duplicates = {item for item, count in Counter(short).items() if count > 1}
    refs: list[TimeFSessionRef] = []
    for version_dir, record_id, event_type, source_run_id, event_key, event_label in records:
        suffix = (
            source_run_id
            if (event_type, Path(source_run_id).name) in duplicates
            else Path(source_run_id).name
        )
        refs.append(
            TimeFSessionRef(
                session_id=f"{event_type}/{suffix}",
                event_type=event_type,
                version_dir=version_dir,
                record_id=record_id,
                source_run_id=source_run_id,
                event_key=event_key,
                event_label=event_label,
            )
        )
    return refs


def discover_timef_sessions(version_dirs: list[Path]) -> list[TimeFSessionRef]:
    """Read TimeF control data and return validated continuous-recording references."""
    from timenet.reader import TimeFReader
    from timenet.registry import DatasetVersion

    if not version_dirs:
        raise ValueError("At least one TimeF dataset-version directory is required")
    discovered: list[tuple[Path, str, EventType, str, str, str]] = []
    seen_datasets: set[str] = set()
    for supplied in version_dirs:
        version_dir = supplied.expanduser().resolve()
        version = DatasetVersion.open_local(version_dir)
        dataset_id, _ = _dataset_identity(version)
        if dataset_id not in _DATASET_CONTRACTS:
            supported = ", ".join(sorted(_DATASET_CONTRACTS))
            raise ValueError(f"Unsupported TimeF dataset {dataset_id!r}; expected one of: {supported}")
        if dataset_id in seen_datasets:
            raise ValueError(f"Duplicate TimeF dataset version supplied for {dataset_id!r}")
        seen_datasets.add(dataset_id)
        event_type, event_key, event_label = _DATASET_CONTRACTS[dataset_id]
        with TimeFReader(version) as reader:
            for record in reader.iter_records():
                source_run_id = str(_one_annotation(record, "source_run_id"))
                discovered.append(
                    (
                        version_dir,
                        str(record.record_id),
                        event_type,
                        source_run_id,
                        event_key,
                        event_label,
                    )
                )
    missing = set(_DATASET_CONTRACTS) - seen_datasets
    if missing:
        raise ValueError(f"Missing required KUKA TimeF dataset(s): {', '.join(sorted(missing))}")
    refs = _session_ids(discovered)
    if len({ref.session_id for ref in refs}) != len(refs):
        raise ValueError("TimeF source records do not map to unique training session ids")
    return sorted(refs, key=lambda ref: ref.session_id)


def _joint_number(signal: str) -> int:
    prefix = "joint_"
    if not signal.startswith(prefix) or not signal[len(prefix) :].isdigit():
        raise ValueError(f"Expected TimeF signal name joint_1..joint_7, got {signal!r}")
    joint = int(signal[len(prefix) :])
    if not 1 <= joint <= N_JOINTS:
        raise ValueError(f"TimeF joint index is out of range: {signal!r}")
    return joint


def session_from_timef_record(ref: TimeFSessionRef, record: Any) -> RawSession:
    """Validate and materialize one TimeF record as a continuous torque session."""
    torque_series = [series for series in record.time_series if series.spec.spec_type == _TORQUE_SPEC_TYPE]
    if len(torque_series) != N_JOINTS:
        raise ValueError(
            f"TimeF record {record.record_id!r} has {len(torque_series)} torque series; expected {N_JOINTS}"
        )
    ordered = sorted(torque_series, key=lambda series: _joint_number(str(series.signal)))
    joints = [_joint_number(str(series.signal)) for series in ordered]
    if joints != list(range(1, N_JOINTS + 1)):
        raise ValueError(f"TimeF record {record.record_id!r} has invalid joint signals: {joints}")
    lengths = {int(series.n_values) for series in ordered}
    periods = {float(series.time_axis.period_us) for series in ordered}
    if len(lengths) != 1:
        raise ValueError(f"TimeF torque series have inconsistent lengths: {sorted(lengths)}")
    if periods != {1_000_000.0 / _SAMPLING_HZ}:
        raise ValueError(f"TimeF torque series must be regular 1 kHz; got periods {sorted(periods)} us")
    torque_nm = np.column_stack([series.to_numpy() for series in ordered]).astype(np.float32, copy=False)
    if not np.isfinite(torque_nm).all():
        raise ValueError(f"TimeF record {record.record_id!r} contains non-finite torque values")

    event_samples: list[int] = []
    for value in _annotation_values(record, ref.event_key):
        if not isinstance(value, dict) or value.get("label") != ref.event_label:
            raise ValueError(f"TimeF record {record.record_id!r} has an invalid {ref.event_key!r} annotation")
        sample = int(value["python_index"])
        timestamp = float(value["timestamp_seconds"])
        if not np.isclose(timestamp, sample / _SAMPLING_HZ, rtol=0.0, atol=1e-12):
            raise ValueError(f"TimeF event timestamp {timestamp} disagrees with sample {sample} at 1 kHz")
        event_samples.append(sample)
    events = np.unique(np.asarray(event_samples, dtype=np.int64))
    if events.size and (events[0] < 0 or events[-1] >= len(torque_nm)):
        raise ValueError(f"TimeF record {record.record_id!r} contains an out-of-range event")
    timestamps_s = np.arange(len(torque_nm), dtype=np.float64) / _SAMPLING_HZ
    return RawSession(
        ref=ref,
        timestamps_s=timestamps_s,
        torque_nm=torque_nm,
        event_samples=events,
    )


def load_timef_session(ref: TimeFSessionRef, expected_hz: float = 1000.0) -> RawSession:
    """Load one record through TimeNet's native reader and validate the model contract."""
    from timenet.reader import TimeFReader
    from timenet.registry import DatasetVersion

    if not np.isclose(expected_hz, _SAMPLING_HZ):
        raise ValueError(f"KUKA TimeF data is fixed at {_SAMPLING_HZ} Hz, got {expected_hz}")
    with TimeFReader(DatasetVersion.open_local(ref.version_dir)) as reader:
        records = list(reader.iter_records([ref.record_id]))
        if len(records) != 1:
            raise ValueError(f"Could not uniquely load TimeF record {ref.record_id!r}")
        return session_from_timef_record(ref, records[0])


def timef_source_receipt(version_dirs: list[Path]) -> dict[str, object]:
    """Record exact TimeF dataset identities and manifest hashes for every run."""
    from timenet.registry import DatasetVersion

    datasets: list[dict[str, str]] = []
    for supplied in sorted(version_dirs, key=lambda path: str(path)):
        version_dir = supplied.expanduser().resolve()
        version = DatasetVersion.open_local(version_dir)
        dataset_id, dataset_version = _dataset_identity(version)
        manifest_path = version_dir / "manifest.json"
        digest = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
        datasets.append(
            {
                "dataset_id": dataset_id,
                "dataset_version": dataset_version,
                "version_dir": str(version_dir),
                "manifest_sha256": digest,
            }
        )
    return {
        "source_backend": "timenet_timef",
        "adapter": "robot_observability.data.timef",
        "datasets": datasets,
        "contract": {
            "torque_spec_type": _TORQUE_SPEC_TYPE,
            "signals": [f"joint_{joint}" for joint in range(1, N_JOINTS + 1)],
            "sampling_hz": _SAMPLING_HZ,
        },
    }

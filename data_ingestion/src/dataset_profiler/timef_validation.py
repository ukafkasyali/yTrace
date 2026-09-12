"""Structured KUKA Part I validation after a TimeF writer/reader round trip."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
from pathlib import Path
from typing import Any

import numpy as np

from .datasets import parse_timed_joint_matrix


@dataclass(frozen=True)
class ValidationCheck:
    """One machine-readable round-trip assertion."""

    check: str
    passed: bool
    expected: Any
    actual: Any
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ValidationReport:
    """Complete machine-readable round-trip result."""

    dataset_id: str
    passed: bool
    checks: tuple[ValidationCheck, ...]

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable report."""
        return asdict(self)

    def to_json(self, *, indent: int = 2) -> str:
        """Serialize the report as JSON."""
        return json.dumps(self.to_dict(), indent=indent, allow_nan=False)


def _annotation_value(record: Any, key: str) -> Any:
    values = [annotation.value for annotation in record.annotations if annotation.key == key]
    if len(values) != 1:
        raise ValueError(
            f"record {record.record_id!r} must have exactly one {key!r} annotation, got {len(values)}"
        )
    return values[0]


def _add(
    checks: list[ValidationCheck],
    check: str,
    expected: Any,
    actual: Any,
    *,
    passed: bool | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    checks.append(
        ValidationCheck(
            check=check,
            passed=bool(expected == actual) if passed is None else bool(passed),
            expected=expected,
            actual=actual,
            details=details or {},
        )
    )


def validate_kuka_timef(
    registry: str | Path,
    profile_path: str | Path,
    source_root: str | Path,
    *,
    dataset_id: str = "kuka/collision-part1",
    event_key: str = "collision",
    event_label: str | None = None,
) -> ValidationReport:
    """Load KUKA TimeF output and compare it with its deterministic source profile."""
    from timenet.client import TimeNet

    profile = json.loads(Path(profile_path).read_text(encoding="utf-8"))
    dataset = TimeNet(registry=str(registry)).load(dataset_id)
    root = Path(source_root).resolve()
    expected_runs = {run["source_run_id"]: run for run in profile["runs"]}
    expected_event_label = event_label or event_key
    checks: list[ValidationCheck] = []

    actual_by_source: dict[str, Any] = {}
    provenance_errors: list[str] = []
    for record in dataset.records:
        try:
            source_run_id = str(_annotation_value(record, "source_run_id"))
        except ValueError as error:
            provenance_errors.append(str(error))
            continue
        if source_run_id in actual_by_source:
            provenance_errors.append(f"duplicate source_run_id {source_run_id!r}")
        actual_by_source[source_run_id] = record

    _add(checks, "source_run_count", len(expected_runs), len(dataset.records))
    _add(
        checks,
        "source_run_ids_unique_and_retained",
        sorted(expected_runs),
        sorted(actual_by_source),
        passed=not provenance_errors and set(actual_by_source) == set(expected_runs),
        details={"errors": provenance_errors},
    )
    _add(checks, "tasks_absent", 0, len(dataset.tasks))

    for source_run_id, expected in sorted(expected_runs.items()):
        record = actual_by_source.get(source_run_id)
        if record is None:
            continue
        prefix = f"run:{source_run_id}"
        torque = [
            series
            for series in record.time_series
            if series.spec.spec_type == "measured_external_joint_torque"
        ]
        position = [
            series
            for series in record.time_series
            if series.spec.spec_type == "measured_joint_position"
        ]
        _add(checks, f"{prefix}:torque_channels", 7, len(torque))
        _add(checks, f"{prefix}:position_channels", 7, len(position))
        lengths = sorted({series.n_values for series in record.time_series})
        _add(checks, f"{prefix}:samples", [expected["sequence_length"]], lengths)
        rates = sorted(
            {
                1_000_000.0 / float(series.time_axis.period_us)
                for series in record.time_series
            }
        )
        _add(checks, f"{prefix}:sampling_rate_hz", [expected["sampling_rate_hz"]], rates)
        end_times = sorted(
            {
                series.time_axis.time_offset_us(series.n_values - 1) / 1_000_000
                for series in record.time_series
            }
        )
        _add(
            checks,
            f"{prefix}:time_range_seconds",
            [expected["time_start"], expected["time_end"]],
            [0.0, end_times[0]] if len(end_times) == 1 else end_times,
            passed=len(end_times) == 1
            and np.isclose(end_times[0], expected["time_end"], rtol=0.0, atol=1e-9),
        )

        events = [annotation for annotation in record.annotations if annotation.key == event_key]
        _add(checks, f"{prefix}:{event_key}_count", len(expected["events"]), len(events))
        event_errors: list[str] = []
        for annotation, expected_event in zip(events, expected["events"]):
            value = annotation.value
            matlab_index = int(value["matlab_index"])
            python_index = int(value["python_index"])
            timestamp = float(value["timestamp_seconds"])
            event_source = f"{source_run_id}/JK_moments.mat"
            if value.get("label") != expected_event_label:
                event_errors.append(
                    f"event {matlab_index} label {value.get('label')!r} != {expected_event_label!r}"
                )
            if value.get("source_variable") != "JK_moments":
                event_errors.append(f"event {matlab_index} lost JK_moments variable provenance")
            if value.get("source_file") != event_source or annotation.source != event_source:
                event_errors.append(f"event {matlab_index} lost JK_moments file provenance")
            if python_index != matlab_index - 1:
                event_errors.append(f"{matlab_index} did not map to {matlab_index - 1}")
            if matlab_index != expected_event["observed_index"]:
                event_errors.append(
                    f"profile index {expected_event['observed_index']} became {matlab_index}"
                )
            if annotation.span is None or annotation.span.start_us != round(timestamp * 1_000_000):
                event_errors.append(f"{event_key} {matlab_index} span disagrees with timestamp")
            expected_timestamp = expected_event["inferred_time_seconds"]
            if not np.isclose(timestamp, expected_timestamp, rtol=0.0, atol=1e-12):
                event_errors.append(
                    f"{event_key} {matlab_index} timestamp {timestamp} != {expected_timestamp}"
                )
        _add(
            checks,
            f"{prefix}:{event_key}_index_and_timestamp_conversion",
            "all i -> i - 1 and timestamp == time[i - 1]",
            "valid" if not event_errors else "invalid",
            passed=not event_errors and len(events) == len(expected["events"]),
            details={"errors": event_errors},
        )

        actual_source_files = _annotation_value(record, "source_files")
        _add(
            checks,
            f"{prefix}:source_files",
            expected["source_files"],
            actual_source_files,
        )
        unit_resolution = _annotation_value(record, "unit_resolution")
        position_units = sorted({str(series.spec.unit_value) for series in position})
        _add(
            checks,
            f"{prefix}:position_unit_radian_high_confidence_inference",
            True,
            bool(
                position_units == ["radian"]
                and unit_resolution.get("unit") == "radian"
                and unit_resolution.get("unit_resolution") == "inferred"
                and unit_resolution.get("confidence") == "high"
            ),
            details={"timef_units": position_units, "unit_resolution": unit_resolution},
        )
        _add(
            checks,
            f"{prefix}:series_source_ids",
            [source_run_id],
            sorted({series.source_id for series in record.time_series}),
        )

        sample_indices = np.array([0, expected["sequence_length"] // 2, expected["sequence_length"] - 1])
        run_dir = root / source_run_id
        raw_torque = parse_timed_joint_matrix(
            run_dir / "JK_MsrExtTrq.mat", "MsrExtTrq"
        ).channels[:, sample_indices]
        raw_position = parse_timed_joint_matrix(
            run_dir / "JK_PosMsr.mat", "PosMsr"
        ).channels[:, sample_indices]
        restored_torque = np.vstack(
            [series.to_numpy()[sample_indices] for series in sorted(torque, key=lambda item: item.signal)]
        )
        restored_position = np.vstack(
            [series.to_numpy()[sample_indices] for series in sorted(position, key=lambda item: item.signal)]
        )
        _add(
            checks,
            f"{prefix}:selected_torque_values",
            "exact raw equality",
            "equal" if np.array_equal(raw_torque, restored_torque) else "different",
            passed=np.array_equal(raw_torque, restored_torque),
            details={"sample_indices": sample_indices.tolist()},
        )
        _add(
            checks,
            f"{prefix}:selected_position_values",
            "exact raw equality",
            "equal" if np.array_equal(raw_position, restored_position) else "different",
            passed=np.array_equal(raw_position, restored_position),
            details={"sample_indices": sample_indices.tolist()},
        )
        full_raw_torque = parse_timed_joint_matrix(
            run_dir / "JK_MsrExtTrq.mat", "MsrExtTrq"
        ).channels
        full_raw_position = parse_timed_joint_matrix(
            run_dir / "JK_PosMsr.mat", "PosMsr"
        ).channels
        full_restored_torque = np.vstack(
            [series.to_numpy() for series in sorted(torque, key=lambda item: item.signal)]
        )
        full_restored_position = np.vstack(
            [series.to_numpy() for series in sorted(position, key=lambda item: item.signal)]
        )
        _add(
            checks,
            f"{prefix}:all_torque_values",
            "exact raw equality",
            "equal" if np.array_equal(full_raw_torque, full_restored_torque) else "different",
            passed=np.array_equal(full_raw_torque, full_restored_torque),
            details={"shape": list(full_raw_torque.shape)},
        )
        _add(
            checks,
            f"{prefix}:all_position_values",
            "exact raw equality",
            "equal" if np.array_equal(full_raw_position, full_restored_position) else "different",
            passed=np.array_equal(full_raw_position, full_restored_position),
            details={"shape": list(full_raw_position.shape)},
        )

    return ValidationReport(
        dataset_id=dataset_id,
        passed=all(check.passed for check in checks),
        checks=tuple(checks),
    )


def validate_kuka_contact_part2(
    registry: str | Path,
    profile_path: str | Path,
    source_root: str | Path,
) -> ValidationReport:
    """Validate Part II intentional-contact output against its raw source profile."""
    return validate_kuka_timef(
        registry,
        profile_path,
        source_root,
        dataset_id="kuka/contact-part2",
        event_key="intentional_contact",
        event_label="intentional_contact",
    )

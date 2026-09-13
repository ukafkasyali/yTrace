"""Deterministic validation of semantic DatasetSpecs against DatasetProfiles."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
from math import isclose
import re
from typing import Any

from ..models import DatasetProfile
from .models import (
    DatasetSpec,
    EvidenceClaim,
    EvidenceStatus,
    IndexBase,
    IndexConversion,
    SamplingRateClaim,
    TimeAxisKind,
    VariableRole,
)


@dataclass(frozen=True)
class ValidationIssue:
    code: str
    path: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ValidationResult:
    valid: bool
    errors: tuple[ValidationIssue, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self, *, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, allow_nan=False) + "\n"


def validate_dataset_spec(profile: DatasetProfile, spec: DatasetSpec,
                          *, evidence_ids: set[str] | None = None) -> ValidationResult:
    """Return repair-oriented issues instead of raising for ordinary validation failures."""
    issues: list[ValidationIssue] = []

    def add(code: str, path: str, message: str, **details: Any) -> None:
        issues.append(ValidationIssue(code, path, message, details))

    accepted_ids = {spec.identity.dataset_id, *spec.identity.compatible_profile_ids}
    if profile.dataset_id not in accepted_ids:
        add(
            "DATASET_ID_MISMATCH", "identity.dataset_id",
            f"Profile dataset_id {profile.dataset_id!r} is not declared by the spec.",
            observed=profile.dataset_id, accepted=sorted(accepted_ids),
        )

    observed_boundary = profile.observed_structure.get("record_boundary")
    if observed_boundary != spec.record_discovery.boundary:
        add("RECORD_BOUNDARY_MISMATCH", "record_discovery.boundary",
            "Record-discovery boundary does not match the profiled structure.",
            claimed=spec.record_discovery.boundary, observed=observed_boundary)
    observed_subset = profile.source.get("scope")
    normalized_subsets = {_normalized_text(value) for value in spec.identity.source_subsets}
    if observed_subset and _normalized_text(str(observed_subset)) not in normalized_subsets:
        add("SOURCE_SUBSET_MISMATCH", "identity.source_subsets",
            f"Profile source scope {observed_subset!r} is not declared by the spec.",
            observed=observed_subset)

    observed_schema = profile.observed_structure.get("variable_schema", {})
    summaries = {item.get("observed_name"): item for item in profile.signals}
    declared = {item.name: item for item in spec.source_variables}
    for index, variable in enumerate(spec.source_variables):
        if variable.name not in observed_schema:
            add(
                "UNKNOWN_SOURCE_VARIABLE", f"source_variables[{index}].name",
                f"Variable {variable.name!r} does not exist in DatasetProfile.",
                source_variable=variable.name,
            )

    observed_runs = {run.source_run_id for run in profile.runs}
    for index, run_id in enumerate(spec.record_discovery.included_run_ids):
        if run_id not in observed_runs:
            add(
                "UNKNOWN_RUN_REFERENCE", f"record_discovery.included_run_ids[{index}]",
                f"Run {run_id!r} does not exist in DatasetProfile.", source_run_id=run_id,
            )

    run_rates = [run.sampling_rate_hz for run in profile.runs]
    axes = {axis.name: axis for axis in spec.time_axes}
    strict_evidence_refs = spec.schema_version == "0.2"
    for index, axis in enumerate(spec.time_axes):
        path = f"time_axes[{index}]"
        if axis.kind is TimeAxisKind.IMPLICIT_REGULAR:
            if axis.source_variable is not None:
                add("IMPLICIT_CLOCK_HAS_SOURCE", f"{path}.source_variable",
                    "An implicit regular clock must not name a raw source variable.")
            if axis.embedded_signal_row is not None:
                add("IMPLICIT_CLOCK_HAS_EMBEDDED_ROW", f"{path}.embedded_signal_row",
                    "An implicit regular clock cannot use an embedded timestamp row.")
            if (isinstance(axis.sample_index_origin, bool)
                    or not isinstance(axis.sample_index_origin, int)
                    or axis.sample_index_origin < 0):
                add("INVALID_SAMPLE_INDEX_ORIGIN", f"{path}.sample_index_origin",
                    "An implicit regular clock requires a non-negative integer index origin.")
            _validate_sampling_rate(axis.sampling_rate, run_rates=[run.sampling_rate_hz for run in profile.runs],
                                    path=f"{path}.sampling_rate", add=add,
                                    strict_evidence_refs=strict_evidence_refs,
                                    evidence_ids=evidence_ids)
            if axis.monotonic is not True:
                add("IMPLICIT_CLOCK_NOT_MONOTONIC", f"{path}.monotonic",
                    "A positive-rate implicit regular clock is structurally monotonic.")
        else:
            _check_reference(axis.source_variable, VariableRole.TIME,
                             f"{path}.source_variable", declared, add)
            if axis.source_variable not in observed_schema:
                add("TIME_AXIS_NOT_OBSERVED", f"{path}.source_variable",
                    f"Time variable {axis.source_variable!r} was not observed.")
            if axis.monotonic and any(run.timestamps_monotonic is not True for run in profile.runs):
                add("TIME_AXIS_NOT_MONOTONIC", f"{path}.monotonic",
                    "The spec requires a monotonic time axis, but not every run verifies it.")

    sequence_lengths = {run.sequence_length for run in profile.runs if run.sequence_length is not None}
    for index, signal in enumerate(spec.signals):
        path = f"signals[{index}]"
        _check_reference(signal.source_variable, VariableRole.SIGNAL, f"{path}.source_variable", declared, add)
        summary = summaries.get(signal.source_variable)
        if summary is None:
            continue
        observed_counts = summary.get("data_channel_count")
        if not observed_counts or set(observed_counts) != {signal.channels.count}:
            add("CHANNEL_COUNT_MISMATCH", f"{path}.channels.count",
                f"Claimed {signal.channels.count} channels do not match observed {observed_counts}.",
                claimed=signal.channels.count, observed=observed_counts)
        if (len(signal.channels.source_indices) != signal.channels.count
                or len(signal.channels.target_names) != signal.channels.count):
            add("CHANNEL_MAPPING_COUNT_MISMATCH", f"{path}.channels",
                "Channel count, source indices, and target names must have equal lengths.")
        shapes = summary.get("observed_shapes", [])
        channel_axis = summary.get("channel_axis", 0)
        row_counts = {shape[channel_axis] for shape in shapes
                      if shape and channel_axis is not None and channel_axis < len(shape)}
        source_indices = signal.channels.source_indices
        if (len(set(source_indices)) != len(source_indices)
                or any(index < 0 or any(index >= rows for rows in row_counts)
                       for index in source_indices)):
            add("INVALID_CHANNEL_MAPPING", f"{path}.channels.source_indices",
                "Source channel indices must be unique and within every observed matrix shape.",
                source_indices=source_indices, observed_row_counts=sorted(row_counts))
        observed_dtypes = summary.get("observed_dtypes", [])
        if spec.schema_version == "0.2":
            claimed_dtypes = list(signal.observed_dtypes)
            if signal.dtype is not None:
                add("INVALID_DTYPE_REPRESENTATION", f"{path}.dtype",
                    "DatasetSpec v0.2 represents physical storage types in observed_dtypes.")
            if len(set(claimed_dtypes)) != len(claimed_dtypes):
                add("DUPLICATE_OBSERVED_DTYPE", f"{path}.observed_dtypes",
                    "Observed dtype values must be unique.")
            if set(claimed_dtypes) != set(observed_dtypes):
                add("DTYPE_SET_MISMATCH", f"{path}.observed_dtypes",
                    "Claimed physical dtypes do not match the profiled dtype set.",
                    claimed=sorted(set(claimed_dtypes)), observed=sorted(set(observed_dtypes)))
        elif signal.dtype not in observed_dtypes:
            add("DTYPE_MISMATCH", f"{path}.dtype",
                f"Claimed dtype {signal.dtype!r} is not among observed dtypes {observed_dtypes!r}.")
        if signal.sampling.time_axis not in axes:
            add("UNKNOWN_TIME_AXIS", f"{path}.sampling.time_axis",
                f"Time axis {signal.sampling.time_axis!r} is not declared.")
        if spec.schema_version == "0.2" and signal.sampling.rate_hz is not None:
            add("LEGACY_SAMPLING_RATE", f"{path}.sampling.rate_hz",
                "DatasetSpec v0.2 stores sampling-rate provenance on the referenced time axis.")
        elif signal.sampling.rate_hz is not None:
            bad_rates = [rate for rate in run_rates if rate is None or not isclose(
                rate, signal.sampling.rate_hz, rel_tol=1e-9, abs_tol=1e-9)]
            if bad_rates:
                add("SAMPLING_RATE_MISMATCH", f"{path}.sampling.rate_hz",
                    f"Claimed {signal.sampling.rate_hz} Hz does not match every run.",
                    claimed=signal.sampling.rate_hz, observed=run_rates)
        sample_axis = summary.get("sample_axis", -1)
        signal_lengths = {shape[sample_axis] for shape in shapes
                          if shape and sample_axis is not None and abs(sample_axis) < len(shape)}
        if sequence_lengths and signal_lengths != sequence_lengths:
            add("SEQUENCE_LENGTH_MISMATCH", f"{path}.source_variable",
                "Signal sample dimensions do not match profiled run sequence lengths.",
                signal_lengths=sorted(signal_lengths), run_lengths=sorted(sequence_lengths))
        _check_claim(signal.unit.resolution, f"{path}.unit.resolution", add,
                     strict_evidence_refs=strict_evidence_refs, evidence_ids=evidence_ids)
        if signal.unit.resolution.status is EvidenceStatus.UNRESOLVED:
            if signal.unit.name is not None or signal.unit.symbol is not None:
                add("INVALID_UNIT_CLAIM", f"{path}.unit",
                    "Unresolved units must use null name and symbol fields.")
        elif not signal.unit.name or not signal.unit.symbol:
            add("INVALID_UNIT_CLAIM", f"{path}.unit",
                "Resolved unit information requires non-empty name and symbol fields.")
        if signal.unit.resolution.status is EvidenceStatus.OBSERVED:
            observed_unit = summary.get("observed_unit")
            if observed_unit is None:
                add("UNIT_NOT_OBSERVED", f"{path}.unit.resolution.status",
                    "The spec calls this unit observed, but DatasetProfile contains no observed unit.")
            else:
                observed_name = (observed_unit.get("name") if isinstance(observed_unit, dict)
                                 else str(observed_unit))
                if signal.unit.name != observed_name:
                    add("UNIT_MISMATCH", f"{path}.unit.name",
                        "Claimed unit does not match the unit observed in DatasetProfile.",
                        claimed=signal.unit.name, observed=observed_unit)
        _check_claim(signal.semantics, f"{path}.semantics", add,
                     strict_evidence_refs=strict_evidence_refs, evidence_ids=evidence_ids)

    for index, event_mapping in enumerate(spec.events):
        path = f"events[{index}]"
        _check_reference(event_mapping.source_variable, VariableRole.EVENT,
                         f"{path}.source_variable", declared, add)
        if event_mapping.source_variable not in observed_schema or not any(
                run.events for run in profile.runs):
            add("INVALID_EVENT_SOURCE", f"{path}.source_variable",
                f"Event source {event_mapping.source_variable!r} is not supported by the profile.")
        if event_mapping.time_axis not in axes:
            add("UNKNOWN_TIME_AXIS", f"{path}.time_axis",
                f"Time axis {event_mapping.time_axis!r} is not declared.")
        valid_pair = (
            (event_mapping.source_index_base is IndexBase.MATLAB_ONE_BASED
             and event_mapping.index_conversion is IndexConversion.SUBTRACT_ONE)
            or (event_mapping.source_index_base is IndexBase.ZERO_BASED
                and event_mapping.index_conversion is IndexConversion.IDENTITY)
        )
        if not valid_pair:
            add("INVALID_INDEX_BASE_CONVERSION", f"{path}.index_conversion",
                "Index conversion is incompatible with the declared source index base.")
        for run in profile.runs:
            for event in run.events:
                python_index = (event.observed_index - 1 if
                                event_mapping.index_conversion is IndexConversion.SUBTRACT_ONE
                                else event.observed_index)
                if run.sequence_length is None or not 0 <= python_index < run.sequence_length:
                    add("EVENT_INDEX_OUT_OF_BOUNDS", f"{path}.source_variable",
                        f"Event {event.event_id!r} converts to out-of-bounds index {python_index}.",
                        source_run_id=run.source_run_id, observed_index=event.observed_index,
                        converted_index=python_index, sequence_length=run.sequence_length)
                    continue
                if (event.inferred_time_seconds is not None and run.sampling_rate_hz is not None
                        and run.time_start is not None):
                    expected = run.time_start + python_index / run.sampling_rate_hz
                    if not isclose(event.inferred_time_seconds, expected, rel_tol=0.0, abs_tol=1e-9):
                        add("EVENT_TIMESTAMP_MISMATCH", f"{path}.timestamp_rule",
                            f"Event {event.event_id!r} timestamp disagrees with its converted index.",
                            source_run_id=run.source_run_id, converted_index=python_index,
                            expected=expected, observed=event.inferred_time_seconds)
        _check_claim(event_mapping.semantics, f"{path}.semantics", add,
                     strict_evidence_refs=strict_evidence_refs, evidence_ids=evidence_ids)

    available = {
        "source_run_id": all(bool(run.source_run_id) for run in profile.runs),
        "source_files": all(bool(run.source_files) for run in profile.runs),
        "source_subset": bool(profile.source.get("scope")),
        "series_source_id": all(bool(run.source_run_id) for run in profile.runs),
    }
    for index, requirement in enumerate(spec.provenance):
        if requirement.required and not available.get(requirement.field, False):
            add("PROVENANCE_SOURCE_UNAVAILABLE", f"provenance[{index}].field",
                f"Required provenance field {requirement.field!r} cannot be produced from the profile.")

    return ValidationResult(valid=not issues, errors=tuple(issues))


def _check_reference(name: str, role: VariableRole, path: str,
                     declared: dict[str, Any], add: Any) -> None:
    variable = declared.get(name)
    if variable is None:
        add("UNDECLARED_SOURCE_VARIABLE", path,
            f"Source variable {name!r} is used but not declared in source_variables.")
    elif variable.role is not role:
        add("SOURCE_VARIABLE_ROLE_MISMATCH", path,
            f"Source variable {name!r} is declared as {variable.role.value}, expected {role.value}.")


def _check_claim(claim: EvidenceClaim, path: str, add: Any,
                 *, strict_evidence_refs: bool = False,
                 evidence_ids: set[str] | None = None) -> None:
    if claim.status in {EvidenceStatus.DOCUMENTED, EvidenceStatus.INFERRED}:
        if claim.confidence is None or not claim.evidence:
            add("MISSING_EVIDENCE_REFERENCE", path,
                f"{claim.status.value.title()} claims require confidence and evidence references.")
        elif any(not reference.strip() for reference in claim.evidence):
            add("INVALID_EVIDENCE_CLAIM", path,
                "Evidence references must be non-empty strings.")
        elif strict_evidence_refs and claim.status is EvidenceStatus.DOCUMENTED and not any(
                reference.startswith("ev_documentation_") for reference in claim.evidence):
            add("INVALID_DOCUMENTATION_EVIDENCE", path,
                "Documented claims require at least one documentation evidence ID.")
        elif strict_evidence_refs and any(
                not reference.startswith("ev_") for reference in claim.evidence):
            add("INVALID_EVIDENCE_REFERENCE", path,
                "Claims require evidence IDs returned by the bounded evidence session.")
        elif evidence_ids is not None and any(
                reference not in evidence_ids for reference in claim.evidence):
            add("UNKNOWN_EVIDENCE_REFERENCE", path,
                "Claim references were not returned by this bounded evidence run.")
    if claim.status is EvidenceStatus.UNRESOLVED and claim.confidence is not None:
        add("INVALID_EVIDENCE_CLAIM", path,
            "Unresolved claims must not declare confidence.")


def _validate_sampling_rate(claim: SamplingRateClaim | None, *, run_rates: list[float | None],
                            path: str, add: Any, strict_evidence_refs: bool,
                            evidence_ids: set[str] | None) -> None:
    if claim is None:
        add("MISSING_SAMPLING_RATE", path,
            "An implicit regular clock requires a sampling-rate claim.")
        return
    _check_claim(claim.resolution, f"{path}.resolution", add,
                 strict_evidence_refs=strict_evidence_refs, evidence_ids=evidence_ids)
    if claim.resolution.status is EvidenceStatus.UNRESOLVED:
        if claim.value is not None:
            add("INVALID_SAMPLING_RATE_CLAIM", f"{path}.value",
                "An unresolved sampling rate must have a null value.")
        return
    if claim.value is None or claim.value <= 0:
        add("INVALID_SAMPLING_RATE", f"{path}.value",
            "A resolved sampling rate must be positive.")
        return
    if claim.unit.casefold() not in {"hz", "hertz"}:
        add("INVALID_SAMPLING_RATE_UNIT", f"{path}.unit",
            "Sampling rate must use Hz or hertz.")
    observed = [rate for rate in run_rates if rate is not None]
    contradictions = [rate for rate in observed
                      if not isclose(rate, claim.value, rel_tol=1e-9, abs_tol=1e-9)]
    if contradictions:
        add("SAMPLING_RATE_CONTRADICTION", f"{path}.value",
            "The claimed sampling rate contradicts deterministic observations.",
            claimed=claim.value, observed=sorted(set(observed)))
    if (claim.resolution.status is EvidenceStatus.OBSERVED
            and (not observed or len(observed) != len(run_rates))):
        add("SAMPLING_RATE_NOT_OBSERVED", f"{path}.resolution.status",
            "An observed sampling-rate claim requires a rate for every profiled run.")


def _normalized_text(value: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", value.casefold()))

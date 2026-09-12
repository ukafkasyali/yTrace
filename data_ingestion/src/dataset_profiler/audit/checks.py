from __future__ import annotations

from collections import defaultdict
from statistics import median

from ..models import AuditIssue, AuditSummary, FileProfile, RunProfile


CHECKS = [
    "missing_values",
    "infinities",
    "empty_signals",
    "inconsistent_channel_count",
    "inconsistent_schema",
    "sequence_length_distribution",
    "timestamp_monotonicity",
    "sampling_rate_variation",
    "constant_or_near_constant_channels",
    "exact_duplicate_files_or_arrays",
    "duplicate_ids",
]


def audit_profile_inputs(files: list[FileProfile], runs: list[RunProfile]) -> AuditSummary:
    issues: list[AuditIssue] = []

    for file in files:
        for variable in file.variables:
            if variable.nan_count:
                issues.append(AuditIssue("error", "missing_values", f"{variable.nan_count} NaN values in {variable.name}", [file.source_run_id], [file.relative_path]))
            if variable.inf_count:
                issues.append(AuditIssue("error", "infinities", f"{variable.inf_count} infinite values in {variable.name}", [file.source_run_id], [file.relative_path]))
            if variable.size == 0:
                issues.append(AuditIssue("error", "empty_signals", f"Empty variable {variable.name}", [file.source_run_id], [file.relative_path]))

    schemas: dict[str, dict[str, tuple[tuple[int, ...], str]]] = defaultdict(dict)
    channels: dict[str, dict[str, int]] = defaultdict(dict)
    for file in files:
        for variable in file.variables:
            shape = tuple(variable.shape)
            if variable.ndim == 2 and len(shape) == 2 and shape[1] > shape[0]:
                schema_shape = (shape[0], -1)  # sample count is intentionally variable
            elif variable.ndim == 2 and len(shape) == 2 and shape[1] == 1:
                schema_shape = (-1, 1)  # variable-length column vector
            else:
                schema_shape = shape
            schemas[file.source_run_id][variable.name] = (schema_shape, variable.dtype)
            if variable.ndim == 2 and len(variable.shape) == 2 and variable.shape[1] > variable.shape[0]:
                channels[variable.name][file.source_run_id] = variable.shape[0]

    all_variables = sorted({name for schema in schemas.values() for name in schema})
    for name in all_variables:
        observed = {run_id: schema.get(name) for run_id, schema in schemas.items()}
        if len(set(observed.values())) > 1:
            issues.append(AuditIssue("warning", "inconsistent_schema", f"Schema differs across runs for {name}", sorted(observed), details={k: v for k, v in observed.items()}))
        counts = channels.get(name, {})
        if len(set(counts.values())) > 1:
            issues.append(AuditIssue("warning", "inconsistent_channel_count", f"Row count differs across runs for {name}", sorted(counts), details=counts))

    lengths = {run.source_run_id: run.sequence_length for run in runs}
    valid_lengths = [value for value in lengths.values() if value is not None]
    if valid_lengths and len(set(valid_lengths)) > 1:
        issues.append(AuditIssue("warning", "sequence_length_distribution", "Sequence lengths differ across runs", sorted(lengths), details={"by_run": lengths, "min": min(valid_lengths), "median": median(valid_lengths), "max": max(valid_lengths)}))
    for run in runs:
        if run.timestamps_monotonic is False:
            issues.append(AuditIssue("error", "timestamp_monotonicity", "Timestamp axis is not strictly increasing", [run.source_run_id]))

    rates = {run.source_run_id: run.sampling_rate_hz for run in runs if run.sampling_rate_hz is not None}
    if rates and max(rates.values()) - min(rates.values()) > max(rates.values()) * 1e-6:
        issues.append(AuditIssue("warning", "sampling_rate_variation", "Sampling rate differs across runs", sorted(rates), details=rates))

    for file in files:
        for variable in file.variables:
            # Row zero is commonly time; the profiler reports it, but the dataset-level
            # signal interpretation decides whether to exclude it downstream.
            affected = [stat.index for stat in variable.channel_stats if stat.constant or stat.near_constant]
            if affected:
                issues.append(AuditIssue("info", "constant_or_near_constant_channels", f"{variable.name} has constant or near-constant rows", [file.source_run_id], [file.relative_path], {"row_indices": affected}))

    for attribute, label in (("sha256", "files"),):
        groups: dict[str, list[str]] = defaultdict(list)
        for file in files:
            groups[getattr(file, attribute)].append(file.relative_path)
        for paths in groups.values():
            if len(paths) > 1:
                issues.append(AuditIssue("warning", "exact_duplicate_files_or_arrays", f"Exact duplicate source {label}", affected_files=sorted(paths)))
    content_groups: dict[str, list[str]] = defaultdict(list)
    for file in files:
        for variable in file.variables:
            if variable.content_sha256:
                content_groups[variable.content_sha256].append(f"{file.relative_path}:{variable.name}")
    for members in content_groups.values():
        if len(members) > 1:
            issues.append(AuditIssue("warning", "exact_duplicate_files_or_arrays", "Exact duplicate variable payloads", affected_files=sorted(members)))
    run_payloads: dict[str, list[str]] = defaultdict(list)
    for run in runs:
        payload_hashes = sorted(
            variable.content_sha256
            for file in files
            if file.source_run_id == run.source_run_id
            for variable in file.variables
            if variable.content_sha256 and variable.name != "rt_tout"
        )
        signature = "|".join(payload_hashes)
        if signature:
            run_payloads[signature].append(run.source_run_id)
    for run_ids in run_payloads.values():
        if len(run_ids) > 1:
            issues.append(AuditIssue("error", "exact_duplicate_files_or_arrays", "Exact duplicate experimental sequences", affected_runs=sorted(run_ids)))

    for attr in ("internal_id", "source_run_id", "original_sequence_id"):
        groups: dict[str, list[str]] = defaultdict(list)
        for run in runs:
            groups[getattr(run, attr)].append(run.source_directory)
        duplicate_values = {key: paths for key, paths in groups.items() if len(paths) > 1}
        if duplicate_values:
            issues.append(AuditIssue("error", "duplicate_ids", f"Duplicate {attr} values", details=duplicate_values))

    return AuditSummary(CHECKS.copy(), issues)

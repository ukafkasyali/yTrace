from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path
import hashlib
from typing import Any

import numpy as np

from .audit import audit_profile_inputs
from .datasets import KukaCollisionHints
from .inspection import inspect_mat_file
from .io.matlab import load_matlab
from .models import DatasetProfile, RunProfile


def discover_runs(root: str | Path) -> list[Path]:
    source = Path(root).resolve()
    if list(source.glob("*.mat")):
        return [source]
    return sorted(
        directory
        for directory in source.iterdir()
        if directory.is_dir() and any(directory.glob("*.mat"))
    )


def _stable_run_id(dataset_id: str, source_run_id: str) -> str:
    suffix = hashlib.sha256(f"{dataset_id}\0{source_run_id}".encode()).hexdigest()[:12]
    return f"{dataset_id}:{source_run_id}:{suffix}"


def _find_time_axis(run_dir: Path) -> tuple[np.ndarray | None, str | None]:
    candidates = [(run_dir / "JsmoExp.mat", "rt_tout")]
    candidates.extend((path, "") for path in sorted(run_dir.glob("*.mat")))
    for path, preferred in candidates:
        if not path.exists():
            continue
        for name, value in load_matlab(path).variables.items():
            array = np.asarray(value)
            if preferred and name != preferred:
                continue
            flat = array.reshape(-1)
            if flat.size >= 2 and np.issubdtype(flat.dtype, np.number):
                differences = np.diff(flat)
                if np.all(np.isfinite(flat)) and np.all(differences > 0):
                    return flat, f"{path.name}:{name}"
        if preferred:
            continue
    return None, None


def _run_profile(dataset_id: str, root: Path, run_dir: Path, hints: Any | None) -> RunProfile:
    source_run_id = run_dir.relative_to(root).as_posix() if run_dir != root else run_dir.name
    time_axis, time_source = _find_time_axis(run_dir)
    rate = None
    monotonic = None
    if time_axis is not None:
        differences = np.diff(time_axis)
        monotonic = bool(np.all(differences > 0))
        if monotonic:
            rate = float(1.0 / np.median(differences))
            if np.isclose(rate, round(rate), rtol=1e-9, atol=1e-9):
                rate = float(round(rate))
    metadata = hints.parse_run_metadata(run_dir) if hints else {}
    if time_source:
        metadata["time_axis_source"] = time_source
    events = hints.events(run_dir, time_axis) if hints else []
    if events and metadata.get("collision_times") is not None:
        metadata["event_count_matches_readme"] = len(events) == len(metadata["collision_times"])
        start = metadata.get("start_time")
        if start and len(events) == len(metadata["collision_times"]):
            def seconds(value: str) -> int:
                hours, minutes, secs = map(int, value.split(":"))
                return hours * 3600 + minutes * 60 + secs

            start_seconds = seconds(start)
            offsets = [(seconds(value) - start_seconds) % 86400 for value in metadata["collision_times"]]
            errors = [abs(offset - event.inferred_time_seconds) for offset, event in zip(offsets, events) if event.inferred_time_seconds is not None]
            metadata["event_time_alignment_max_abs_seconds"] = max(errors) if errors else None
    return RunProfile(
        internal_id=_stable_run_id(dataset_id, source_run_id),
        dataset_id=dataset_id,
        source_run_id=source_run_id,
        original_sequence_id=run_dir.name,
        source_directory=source_run_id,
        source_files=sorted(path.relative_to(root).as_posix() for path in run_dir.iterdir() if path.is_file()),
        sequence_length=int(time_axis.size) if time_axis is not None else None,
        channel_counts={},
        sampling_rate_hz=rate,
        time_start=float(time_axis[0]) if time_axis is not None else None,
        time_end=float(time_axis[-1]) if time_axis is not None else None,
        timestamps_monotonic=monotonic,
        metadata=metadata,
        events=events,
    )


def _signal_summary(files: list[Any], hints: Any | None) -> list[dict[str, Any]]:
    observations: dict[str, list[tuple[str, Any]]] = defaultdict(list)
    for file in files:
        for variable in file.variables:
            observations[variable.name].append((file.source_run_id, variable))
    summaries: list[dict[str, Any]] = []
    names = hints.signal_names if hints else {}
    for name, items in sorted(observations.items()):
        shapes = sorted({tuple(variable.shape) for _, variable in items})
        dtypes = sorted({variable.dtype for _, variable in items})
        matrix = all(variable.ndim == 2 for _, variable in items)
        has_embedded_time = matrix and all(
            variable.shape[0] > 1 and variable.shape[1] > variable.shape[0]
            for _, variable in items
        ) and name not in {"JK_moments"}
        summaries.append(
            {
                "observed_name": name,
                "observed_shapes": [list(shape) for shape in shapes],
                "observed_dtypes": dtypes,
                "files_count": len(items),
                "has_embedded_time_row": has_embedded_time,
                "data_channel_count": sorted({v.shape[0] - 1 for _, v in items}) if has_embedded_time else None,
                "inferred_semantic_name": {
                    "value": names.get(name, "unknown"),
                    "confidence": 0.98 if name in names else 0.0,
                    "evidence": ["dataset-specific KUKA hint mapping"] if name in names else [],
                },
            }
        )
    return summaries


def profile_dataset(
    source: str | Path,
    dataset_id: str,
    *,
    hints: Any | None = None,
) -> DatasetProfile:
    root = Path(source).resolve()
    if not root.is_dir():
        raise ValueError(f"Dataset source is not a directory: {root}")
    run_dirs = discover_runs(root)
    if not run_dirs:
        raise ValueError(f"No directories containing MAT files found under {root}")

    runs = [_run_profile(dataset_id, root, run_dir, hints) for run_dir in run_dirs]
    by_source_id = {run.source_run_id: run for run in runs}
    files = []
    for run_dir in run_dirs:
        source_run_id = run_dir.relative_to(root).as_posix() if run_dir != root else run_dir.name
        for path in sorted(run_dir.glob("*.mat")):
            profile = inspect_mat_file(path, root, source_run_id)
            files.append(profile)
            for variable in profile.variables:
                likely_time_row = (
                    variable.ndim == 2
                    and len(variable.shape) == 2
                    and variable.shape[0] > 1
                    and variable.shape[1] > variable.shape[0]
                    and variable.name != "JK_moments"
                )
                if likely_time_row:
                    by_source_id[source_run_id].channel_counts[variable.name] = variable.shape[0] - 1

    format_counts = Counter(file.format for file in files)
    variable_schema: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for file in files:
        for variable in file.variables:
            observation = {"shape": variable.shape, "dtype": variable.dtype, "ndim": variable.ndim}
            if observation not in variable_schema[variable.name]:
                variable_schema[variable.name].append(observation)
    audit = audit_profile_inputs(files, runs)
    semantics = hints.semantic_claims() if hints else []
    unknowns = hints.unknowns() if hints else ["Signal semantics and record boundaries have not been identified."]
    return DatasetProfile(
        schema_version="1.0.0",
        dataset_id=dataset_id,
        source={
            "path": str(root),
            "scope": "Part I Batch 01" if isinstance(hints, KukaCollisionHints) else "user-provided directory",
            "raw_data_copied": False,
        },
        discovery={
            "run_directories": len(run_dirs),
            "mat_files": len(files),
            "all_files": sum(len(run.source_files) for run in runs),
            "matlab_formats": dict(sorted(format_counts.items())),
            "compression_layout": "already extracted; no archive found inside source directory",
        },
        files=files,
        runs=runs,
        observed_structure={
            "record_boundary": "directory containing MAT files",
            "variable_schema": dict(sorted(variable_schema.items())),
            "sequence_lengths": [run.sequence_length for run in runs],
            "sampling_rates_hz": [run.sampling_rate_hz for run in runs],
        },
        signals=_signal_summary(files, hints),
        entities=[
            {
                "entity_type": "experimental_run",
                "count": len(runs),
                "identifier_fields": ["dataset_id", "source_run_id", "original_sequence_id", "internal_id"],
                "future_window_provenance_fields": ["source_run_id", "window_start", "window_end"],
            }
        ],
        metadata={
            "run_metadata_sources": sorted({run.metadata.get("metadata_source") for run in runs if run.metadata.get("metadata_source")}),
            "dataset_specific_hints": type(hints).__name__ if hints else None,
        },
        quality=audit,
        inferred_semantics=semantics,
        unknowns=unknowns,
    )

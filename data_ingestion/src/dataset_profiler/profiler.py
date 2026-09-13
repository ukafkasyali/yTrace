from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path
import hashlib
import json
from typing import Any

import numpy as np

from .audit import audit_profile_inputs
from .datasets import KukaCollisionHints
from .inspection import inspect_mat_file
from .io.hdf5 import inspect_hdf5_file, inspect_hdf5_structure
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


def _discover_hdf5_files(root: Path) -> list[Path]:
    return sorted((*root.rglob("*.h5"), *root.rglob("*.hdf5")))


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
            # Monotonic values alone are not evidence of timestamps: KUKA's
            # JK_moments is a sorted vector of one-based event sample indices.
            # Without an identified clock, retain unknown timing in the profile.
            if not preferred and name.casefold() not in {
                "rt_tout", "time", "timestamp", "timestamps", "time_seconds",
            }:
                continue
            if array.ndim > 2 or (array.ndim == 2 and min(array.shape) != 1):
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
        structural_axes = [
            variable.nested_structure.get("structural_axes", {})
            for _, variable in items
            if isinstance(variable.nested_structure, dict)
        ]
        channel_axes = {axes.get("channel_axis") for axes in structural_axes}
        sample_axes = {axes.get("sample_axis") for axes in structural_axes}
        channel_axes.discard(None)
        sample_axes.discard(None)
        channel_axis = next(iter(channel_axes)) if len(channel_axes) == 1 else (0 if has_embedded_time else None)
        sample_axis = next(iter(sample_axes)) if len(sample_axes) == 1 else (1 if has_embedded_time else None)
        if has_embedded_time:
            data_channel_count = sorted({v.shape[0] - 1 for _, v in items})
        elif channel_axis is not None:
            data_channel_count = sorted({v.shape[channel_axis] for _, v in items})
        else:
            data_channel_count = None
        summaries.append(
            {
                "observed_name": name,
                "observed_shapes": [list(shape) for shape in shapes],
                "observed_dtypes": dtypes,
                "files_count": len(items),
                "has_embedded_time_row": has_embedded_time,
                "data_channel_count": data_channel_count,
                "channel_axis": channel_axis,
                "sample_axis": sample_axis,
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
    hdf5_files = _discover_hdf5_files(root)
    run_dirs = discover_runs(root)
    if hdf5_files and run_dirs:
        raise ValueError("Mixed MAT and HDF5 dataset roots are not supported")
    if not run_dirs and not hdf5_files:
        raise ValueError(f"No supported MAT or HDF5 files found under {root}")

    hierarchy_variants: dict[str, dict[str, Any]] = {}
    if hdf5_files:
        runs, files = _profile_hdf5(root, dataset_id, hdf5_files, hierarchy_variants)
    else:
        runs = [_run_profile(dataset_id, root, run_dir, hints) for run_dir in run_dirs]
        files = _profile_mat_files(root, run_dirs, runs)

    format_counts = Counter(file.format for file in files)
    variable_schema: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for file in files:
        for variable in file.variables:
            observation = {"shape": variable.shape, "dtype": variable.dtype, "ndim": variable.ndim}
            if observation not in variable_schema[variable.name]:
                variable_schema[variable.name].append(observation)
    audit = audit_profile_inputs(files, runs)
    semantics = hints.semantic_claims() if hints else []
    unknowns = hints.unknowns() if hints else [
        "Signal semantics, units, sampling/time interpretation, and directory metadata meanings have not been identified."
    ]
    return DatasetProfile(
        schema_version="1.0.0",
        dataset_id=dataset_id,
        source={
            "path": str(root),
            "scope": "Part I Batch 01" if isinstance(hints, KukaCollisionHints) else "user-provided directory",
            "raw_data_copied": False,
        },
        discovery={
            "run_directories": len(run_dirs) if not hdf5_files else len({path.parent for path in hdf5_files}),
            "mat_files": len(files) if not hdf5_files else 0,
            "hdf5_files": len(files) if hdf5_files else 0,
            "all_files": sum(len(run.source_files) for run in runs),
            "matlab_formats": dict(sorted(format_counts.items())) if not hdf5_files else {},
            "file_formats": dict(sorted(format_counts.items())),
            "compression_layout": "already extracted; no archive found inside source directory",
        },
        files=files,
        runs=runs,
        observed_structure={
            "record_boundary": "HDF5 file" if hdf5_files else "directory containing MAT files",
            "variable_schema": dict(sorted(variable_schema.items())),
            "sequence_lengths": [run.sequence_length for run in runs],
            "sampling_rates_hz": [run.sampling_rate_hz for run in runs],
            "hdf5_hierarchy_variants": list(hierarchy_variants.values()),
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


def _profile_mat_files(root: Path, run_dirs: list[Path], runs: list[RunProfile]) -> list[Any]:
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
    return files


def _profile_hdf5(root: Path, dataset_id: str, paths: list[Path], hierarchy_variants: dict[str, dict[str, Any]]) -> tuple[list[RunProfile], list[Any]]:
    runs: list[RunProfile] = []
    files = []
    for path in paths:
        source_run_id = path.relative_to(root).as_posix()
        profile = inspect_hdf5_file(path, root, source_run_id)
        files.append(profile)
        sample_lengths = {
            variable.shape[axes["sample_axis"]]
            for variable in profile.variables
            if isinstance(variable.nested_structure, dict)
            and (axes := variable.nested_structure.get("structural_axes", {})).get("sample_axis") is not None
        }
        channel_counts = {
            variable.name: variable.shape[axes["channel_axis"]]
            for variable in profile.variables
            if isinstance(variable.nested_structure, dict)
            and (axes := variable.nested_structure.get("structural_axes", {})).get("channel_axis") is not None
        }
        runs.append(RunProfile(
            internal_id=_stable_run_id(dataset_id, source_run_id), dataset_id=dataset_id,
            source_run_id=source_run_id,
            original_sequence_id=path.relative_to(root).with_suffix("").as_posix(),
            source_directory=path.parent.relative_to(root).as_posix(),
            source_files=[source_run_id],
            sequence_length=next(iter(sample_lengths)) if len(sample_lengths) == 1 else None,
            channel_counts=channel_counts, sampling_rate_hz=None, time_start=None, time_end=None,
            timestamps_monotonic=None,
            metadata={"source_parent_parts": list(path.parent.relative_to(root).parts)}, events=[],
        ))
        structure = inspect_hdf5_structure(path)
        structure_value = {
            "root_attributes": structure.root_attributes, "groups": list(structure.groups),
            "datasets": sorted(variable.name for variable in profile.variables),
        }
        signature = json.dumps(structure_value, sort_keys=True)
        hierarchy_variants.setdefault(signature, {**structure_value, "files": []})["files"].append(source_run_id)
    return runs, files

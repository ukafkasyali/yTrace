from dataclasses import replace
from pathlib import Path

import numpy as np
from scipy.io import savemat

from dataset_profiler.datasets import KukaCollisionHints
from dataset_profiler.models import Event, Inference
from dataset_profiler.profiler import profile_dataset
from dataset_profiler.semantic_spec import (
    DatasetSpec,
    load_kuka_collision_part1_spec,
    validate_dataset_spec,
)
from dataset_profiler.semantic_spec.models import IndexConversion, SourceVariable


def _profile(tmp_path: Path):
    run = tmp_path / "03-15-12-53"
    run.mkdir()
    time = np.arange(10, dtype=np.float64) / 1000
    torque = np.vstack([time, np.arange(70, dtype=np.float64).reshape(7, 10)])
    position = np.vstack([time, np.arange(100, 170, dtype=np.float64).reshape(7, 10)])
    savemat(run / "JsmoExp.mat", {"rt_tout": time[:, None]})
    savemat(run / "JK_MsrExtTrq.mat", {"MsrExtTrq": torque})
    savemat(run / "JK_PosMsr.mat", {"PosMsr": position})
    savemat(run / "JK_moments.mat", {"JK_moments": np.array([[1], [3], [10]])})
    (run / "ReadMe.txt").write_text("Collision(ball):\n", encoding="utf-8")
    return profile_dataset(
        tmp_path,
        "kuka/collision-part1",
        hints=KukaCollisionHints(),
    )


def _codes(profile, spec):
    return {issue.code for issue in validate_dataset_spec(profile, spec).errors}


def test_kuka_reference_spec_round_trips_and_validates(tmp_path):
    profile = _profile(tmp_path)
    spec = load_kuka_collision_part1_spec()

    assert DatasetSpec.from_json(spec.to_json()) == spec
    assert validate_dataset_spec(profile, spec).valid
    assert spec.identity.dataset_id == "kuka/collision-part1"
    assert spec.tasks == ()
    assert spec.record_defaults.subject_ids == ()
    assert spec.record_defaults.start_time is None


def test_wrong_channel_count_has_specific_issue(tmp_path):
    profile = _profile(tmp_path)
    spec = load_kuka_collision_part1_spec()
    wrong = replace(spec.signals[0], channels=replace(spec.signals[0].channels, count=8))
    spec = replace(spec, signals=(wrong, *spec.signals[1:]))

    assert "CHANNEL_COUNT_MISMATCH" in _codes(profile, spec)


def test_nonexistent_source_variable_has_specific_issue(tmp_path):
    profile = _profile(tmp_path)
    spec = load_kuka_collision_part1_spec()
    missing = "HallucinatedSignal"
    declaration = replace(spec.source_variables[0], name=missing)
    signal = replace(spec.signals[0], source_variable=missing)
    spec = replace(
        spec,
        source_variables=(declaration, *spec.source_variables[1:]),
        signals=(signal, *spec.signals[1:]),
    )

    assert "UNKNOWN_SOURCE_VARIABLE" in _codes(profile, spec)


def test_wrong_sampling_rate_has_specific_issue(tmp_path):
    profile = _profile(tmp_path)
    spec = load_kuka_collision_part1_spec()
    signal = replace(spec.signals[0], sampling=replace(spec.signals[0].sampling, rate_hz=500.0))
    spec = replace(spec, signals=(signal, *spec.signals[1:]))

    assert "SAMPLING_RATE_MISMATCH" in _codes(profile, spec)


def test_incompatible_collision_index_conversion_has_specific_issue(tmp_path):
    profile = _profile(tmp_path)
    spec = load_kuka_collision_part1_spec()
    event = replace(spec.events[0], index_conversion=IndexConversion.IDENTITY)
    spec = replace(spec, events=(event,))

    codes = _codes(profile, spec)
    assert "INVALID_INDEX_BASE_CONVERSION" in codes
    assert "EVENT_TIMESTAMP_MISMATCH" in codes


def test_event_outside_sequence_has_specific_issue(tmp_path):
    profile = _profile(tmp_path)
    last = Event(
        event_id="outside",
        observed_index=11,
        inferred_time_seconds=0.010,
        interpretation=Inference(value="collision", confidence=0.99, evidence=["fixture"]),
    )
    profile.runs[0].events.append(last)

    assert "EVENT_INDEX_OUT_OF_BOUNDS" in _codes(profile, load_kuka_collision_part1_spec())


def test_missing_required_provenance_source_has_specific_issue(tmp_path):
    profile = _profile(tmp_path)
    profile.runs[0].source_files.clear()

    assert "PROVENANCE_SOURCE_UNAVAILABLE" in _codes(
        profile, load_kuka_collision_part1_spec()
    )


def test_structurally_invalid_unit_has_specific_issue(tmp_path):
    profile = _profile(tmp_path)
    spec = load_kuka_collision_part1_spec()
    signal = replace(spec.signals[1], unit=replace(spec.signals[1].unit, name=""))
    spec = replace(spec, signals=(spec.signals[0], signal))

    assert "INVALID_UNIT_CLAIM" in _codes(profile, spec)


def test_undeclared_source_reference_is_repairable_issue(tmp_path):
    profile = _profile(tmp_path)
    spec = load_kuka_collision_part1_spec()
    declarations = tuple(
        item for item in spec.source_variables if item.name != spec.signals[0].source_variable
    )
    assert all(isinstance(item, SourceVariable) for item in declarations)
    spec = replace(spec, source_variables=declarations)

    assert "UNDECLARED_SOURCE_VARIABLE" in _codes(profile, spec)

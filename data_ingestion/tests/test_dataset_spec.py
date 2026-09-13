from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

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
from dataset_profiler.semantic_agent.agent import SemanticAgentRun
from dataset_profiler.semantic_agent.repair import run_repairs
from dataset_profiler.semantic_spec.models import IndexConversion, SourceVariable
from dataset_profiler.semantic_spec.validation import ValidationIssue, ValidationResult


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


def _v02_spec(profile, *, rate=2000.0, rate_status="documented", rate_evidence=None,
              observed_dtypes=("float64",)):
    return DatasetSpec.from_dict({
        "schema_version": "0.2",
        "identity": {"dataset_id": profile.dataset_id,
                     "source_subsets": [profile.source["scope"]],
                     "compatible_profile_ids": []},
        "record_discovery": {"record_unit": "record",
                             "boundary": profile.observed_structure["record_boundary"],
                             "included_run_ids": []},
        "source_variables": [{"name": "MsrExtTrq", "role": "signal"}],
        "signals": [{
            "source_variable": "MsrExtTrq", "semantic_type": "generic_signal",
            "channels": {"count": 7, "source_indices": [1, 2, 3, 4, 5, 6, 7],
                         "target_names": [f"channel_{index}" for index in range(7)]},
            "dtype": None, "observed_dtypes": list(observed_dtypes),
            "unit": {"name": None, "symbol": None,
                     "resolution": {"status": "unresolved", "confidence": None,
                                    "evidence": []}},
            "sampling": {"rate_hz": None, "time_axis": "sample_clock"},
            "semantics": {"status": "documented", "confidence": "high",
                          "evidence": ["ev_documentation_signal_123"]},
        }],
        "time_axes": [{
            "name": "sample_clock", "source_variable": None,
            "kind": "implicit_regular", "unit": "second", "monotonic": True,
            "embedded_signal_row": None, "sample_index_origin": 0,
            "sampling_rate": {"value": rate, "unit": "Hz",
                              "resolution": {"status": rate_status,
                                             "confidence": "high",
                                             "evidence": rate_evidence or [
                                                 "ev_documentation_sampling_123"
                                             ]}},
        }],
        "events": [], "provenance": [], "tasks": [],
        "record_defaults": {"subject_ids": [], "start_time": None},
    })


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


def test_documented_implicit_clock_does_not_require_raw_timestamps(tmp_path):
    profile = _profile(tmp_path)
    for run in profile.runs:
        run.sampling_rate_hz = None
        run.timestamps_monotonic = None
    spec = _v02_spec(profile)

    assert validate_dataset_spec(profile, spec).valid
    axis = spec.time_axes[0]
    assert axis.source_variable is None
    assert axis.sample_index_origin == 0
    assert axis.sampling_rate.value == 2000.0


def test_documented_sampling_rate_fails_when_raw_observation_contradicts_it(tmp_path):
    profile = _profile(tmp_path)
    spec = _v02_spec(profile, rate=2000.0)

    assert "SAMPLING_RATE_CONTRADICTION" in _codes(profile, spec)


def test_v02_accepts_homogeneous_and_heterogeneous_observed_dtype_sets(tmp_path):
    profile = _profile(tmp_path)
    for run in profile.runs:
        run.sampling_rate_hz = None
        run.timestamps_monotonic = None
    homogeneous = _v02_spec(profile)
    assert validate_dataset_spec(profile, homogeneous).valid

    next(item for item in profile.signals
         if item["observed_name"] == "MsrExtTrq")["observed_dtypes"] = [
             "float32", "float64", "int64"
         ]
    heterogeneous = _v02_spec(
        profile, observed_dtypes=("float32", "float64", "int64")
    )
    assert validate_dataset_spec(profile, heterogeneous).valid

    false_mixed = replace(heterogeneous.signals[0], dtype="mixed", observed_dtypes=())
    invalid = replace(heterogeneous, signals=(false_mixed,))
    codes = _codes(profile, invalid)
    assert "INVALID_DTYPE_REPRESENTATION" in codes
    assert "DTYPE_SET_MISMATCH" in codes


def test_v02_documented_claim_requires_documentation_evidence_id(tmp_path):
    profile = _profile(tmp_path)
    for run in profile.runs:
        run.sampling_rate_hz = None
        run.timestamps_monotonic = None
    spec = _v02_spec(profile, rate_evidence=["ev_signal_statistics_rate_123"])

    assert "INVALID_DOCUMENTATION_EVIDENCE" in _codes(profile, spec)


def test_v02_claim_reference_must_exist_in_run_evidence(tmp_path):
    profile = _profile(tmp_path)
    for run in profile.runs:
        run.sampling_rate_hz = None
        run.timestamps_monotonic = None
    spec = _v02_spec(profile)

    result = validate_dataset_spec(profile, spec, evidence_ids=set())
    assert "UNKNOWN_EVIDENCE_REFERENCE" in {issue.code for issue in result.errors}


def test_repair_returns_best_candidate_when_later_round_regresses(tmp_path):
    profile = _profile(tmp_path)
    initial = load_kuka_collision_part1_spec()
    round_one = replace(initial, schema_version="round-one")
    round_two = replace(initial, schema_version="round-two")

    def result(count):
        issues = tuple(ValidationIssue(f"ISSUE_{index}", "path", "message")
                       for index in range(count))
        return ValidationResult(not issues, issues)

    runs = [SemanticAgentRun(round_one, {"round": 1}),
            SemanticAgentRun(round_two, {"round": 2})]
    with patch("dataset_profiler.semantic_agent.repair.validate_dataset_spec",
               side_effect=[result(12), result(2), result(5)]), patch(
        "dataset_profiler.semantic_agent.repair.repair_dataset_spec", side_effect=runs
    ):
        rounds, final_spec, final_validation, selection = run_repairs(
            profile, [], initial, object()
        )

    assert final_spec is round_one
    assert len(final_validation.errors) == 2
    assert selection["best_round"] == 1
    assert rounds[0]["accepted_as_best"] is True
    assert rounds[1]["accepted_as_best"] is False

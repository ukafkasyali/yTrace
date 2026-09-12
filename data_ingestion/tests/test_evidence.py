from pathlib import Path

import numpy as np
from scipy.io import savemat

from dataset_profiler.datasets import KukaCollisionHints
from dataset_profiler.evidence import (
    Evidence,
    EvidenceBudget,
    EvidenceErrorCode,
    EvidenceLimits,
    EvidenceSession,
)
from dataset_profiler.profiler import profile_dataset


def _kuka_profile(tmp_path: Path):
    time = np.arange(12, dtype=np.float64) / 1000
    for run_number in range(5):
        run = tmp_path / f"03-15-13-{run_number:02d}"
        run.mkdir()
        torque_channels = np.vstack([
            np.linspace(-10.0 - channel, 10.0 + channel, time.size)
            for channel in range(7)
        ])
        position_channels = np.vstack([
            np.linspace(-1.44 + channel * 0.01, 1.44 + channel * 0.01, time.size)
            for channel in range(7)
        ])
        savemat(run / "JsmoExp.mat", {"rt_tout": time[:, None]})
        savemat(run / "JK_MsrExtTrq.mat", {
            "MsrExtTrq": np.vstack([time, torque_channels]),
        })
        savemat(run / "JK_PosMsr.mat", {
            "PosMsr": np.vstack([time, position_channels]),
        })
        savemat(run / "JK_moments.mat", {
            "JK_moments": np.array([[1], [6], [12]], dtype=np.int32),
        })
        (run / "ReadMe.txt").write_text("Collision(ball):\n", encoding="utf-8")
    return profile_dataset(
        tmp_path,
        "kuka/collision-part1",
        hints=KukaCollisionHints(),
    )


def test_dataset_summary_exposes_kuka_inventory_without_source_path(tmp_path):
    profile = _kuka_profile(tmp_path)
    evidence = EvidenceSession(profile).dataset_summary()

    assert isinstance(evidence, Evidence)
    assert evidence.value["dataset_id"] == "kuka/collision-part1"
    assert evidence.value["source_subsets"] == ["Part I Batch 01"]
    assert evidence.value["run_count"] == 5
    assert evidence.value["sampling_rates_hz"] == [1000.0]
    assert {"MsrExtTrq", "PosMsr", "JK_moments"}.issubset(
        evidence.value["variables"]["items"]
    )
    assert str(tmp_path) not in evidence.to_json()


def test_variable_schema_exposes_seven_channel_kuka_signals(tmp_path):
    profile = _kuka_profile(tmp_path)
    session = EvidenceSession(profile)

    for name in ("MsrExtTrq", "PosMsr"):
        evidence = session.variable_schema(name)
        assert isinstance(evidence, Evidence)
        assert evidence.value["dtypes"] == ["float64"]
        assert evidence.value["channel_counts"] == [7]
        assert evidence.value["logical_shape"] == [12, 7]
        assert evidence.value["sampling_rates_hz"] == [1000.0]
        assert evidence.value["consistent_across_runs"]


def test_position_statistics_are_compact_and_exclude_time_row(tmp_path):
    profile = _kuka_profile(tmp_path)
    evidence = EvidenceSession(profile).signal_statistics("PosMsr")

    assert isinstance(evidence, Evidence)
    assert evidence.value["available_channels"] == 7
    assert evidence.value["returned_channels"] == 7
    assert evidence.value["global"]["count"] == 5 * 7 * 12
    assert evidence.value["global"]["minimum"] == -1.44
    assert evidence.value["global"]["maximum"] == 1.5
    assert all(set(channel) == {
        "channel_index", "source_index", "count", "minimum", "maximum", "mean",
        "standard_deviation", "nan_count", "inf_count",
    } for channel in evidence.value["per_channel"])


def test_metadata_summary_exposes_event_source_and_bounds(tmp_path):
    evidence = EvidenceSession(_kuka_profile(tmp_path)).metadata_summary()

    assert isinstance(evidence, Evidence)
    events = evidence.value["events"]
    assert events["count"] == 15
    assert events["source_variables"]["items"] == ["JK_moments"]
    assert events["semantic_labels"]["items"] == ["collision"]
    assert events["observed_index_min"] == 1
    assert events["observed_index_max"] == 12
    assert events["sequence_length_bounds"] == [12]


def test_evidence_ids_are_stable_for_same_profile_and_query(tmp_path):
    profile = _kuka_profile(tmp_path)

    first = EvidenceSession(profile).variable_schema("PosMsr")
    second = EvidenceSession(profile).variable_schema("PosMsr")

    assert isinstance(first, Evidence) and isinstance(second, Evidence)
    assert first.id == second.id
    assert first.id.startswith("ev_variable_schema_posmsr_")


def test_unknown_variable_and_run_return_structured_errors(tmp_path):
    profile = _kuka_profile(tmp_path)
    session = EvidenceSession(profile, allow_bounded_source_excerpts=True)

    unknown_variable = session.variable_schema("NotThere")
    unknown_run = session.bounded_excerpt("PosMsr", "not-a-run", start=0, length=1)

    assert unknown_variable.code is EvidenceErrorCode.UNKNOWN_VARIABLE
    assert unknown_run.code is EvidenceErrorCode.UNKNOWN_RUN


def test_excerpt_limits_are_rejected_before_source_access(tmp_path):
    profile = _kuka_profile(tmp_path)
    session = EvidenceSession(profile)
    run_id = profile.runs[0].source_run_id

    too_long = session.bounded_excerpt(
        "PosMsr", run_id, start=0, length=session.limits.max_excerpt_samples + 1
    )
    too_many_channels = session.bounded_excerpt(
        "PosMsr", run_id, start=0, length=1,
        channels=tuple(range(session.limits.max_excerpt_channels + 1)),
    )

    assert too_long.code is EvidenceErrorCode.EXCERPT_TOO_LARGE
    assert too_many_channels.code is EvidenceErrorCode.CHANNEL_LIMIT_EXCEEDED


def test_bounded_excerpt_returns_only_requested_values_and_hides_paths(tmp_path):
    profile = _kuka_profile(tmp_path)
    session = EvidenceSession(profile, allow_bounded_source_excerpts=True)
    run_id = profile.runs[0].source_run_id

    evidence = session.bounded_excerpt(
        "PosMsr", run_id, start=2, length=3, channels=(0, 2)
    )

    assert isinstance(evidence, Evidence)
    assert len(evidence.value["values"]) == 2
    assert all(len(channel) == 3 for channel in evidence.value["values"])
    assert evidence.value["returned_values"] == 6
    assert evidence.value["available_samples"] == 12
    assert session.usage.excerpt_values == 6
    assert str(tmp_path) not in evidence.to_json()


def test_source_excerpts_are_disabled_by_default(tmp_path):
    profile = _kuka_profile(tmp_path)
    session = EvidenceSession(profile)

    result = session.bounded_excerpt(
        "PosMsr", profile.runs[0].source_run_id, start=0, length=1, channels=(0,)
    )

    assert result.code is EvidenceErrorCode.UNAVAILABLE_EVIDENCE
    assert session.usage.excerpt_values == 0


def test_excerpt_rejects_source_changed_since_profiling_without_leaking_path(tmp_path):
    profile = _kuka_profile(tmp_path)
    run_id = profile.runs[0].source_run_id
    source = tmp_path / run_id / "JK_PosMsr.mat"
    savemat(source, {"PosMsr": np.zeros((8, 12), dtype=np.float64)})
    session = EvidenceSession(profile, allow_bounded_source_excerpts=True)

    result = session.bounded_excerpt("PosMsr", run_id, start=0, length=1, channels=(0,))

    assert result.code is EvidenceErrorCode.UNAVAILABLE_EVIDENCE
    assert str(tmp_path) not in result.to_json()
    assert session.usage.excerpt_values == 0


def test_total_query_and_excerpt_budgets_are_enforced(tmp_path):
    profile = _kuka_profile(tmp_path)
    query_session = EvidenceSession(
        profile,
        budget=EvidenceBudget(max_total_queries=2, max_excerpt_queries=1,
                              max_total_excerpt_values=8),
    )
    query_session.dataset_summary()
    query_session.metadata_summary()
    exhausted = query_session.variable_schema("PosMsr")
    assert exhausted.code is EvidenceErrorCode.QUERY_BUDGET_EXCEEDED

    excerpt_session = EvidenceSession(
        profile,
        budget=EvidenceBudget(max_total_queries=4, max_excerpt_queries=1,
                              max_total_excerpt_values=8),
        allow_bounded_source_excerpts=True,
    )
    run_id = profile.runs[0].source_run_id
    assert isinstance(excerpt_session.bounded_excerpt(
        "PosMsr", run_id, start=0, length=2, channels=(0, 1)
    ), Evidence)
    exhausted = excerpt_session.bounded_excerpt(
        "PosMsr", run_id, start=2, length=1, channels=(0,)
    )
    assert exhausted.code is EvidenceErrorCode.EXCERPT_BUDGET_EXCEEDED

    value_session = EvidenceSession(
        profile,
        budget=EvidenceBudget(max_total_queries=4, max_excerpt_queries=2,
                              max_total_excerpt_values=3),
        allow_bounded_source_excerpts=True,
    )
    exhausted = value_session.bounded_excerpt(
        "PosMsr", run_id, start=0, length=2, channels=(0, 1)
    )
    assert exhausted.code is EvidenceErrorCode.SAMPLE_BUDGET_EXCEEDED
    assert value_session.usage.excerpt_values == 0


def test_statistics_and_metadata_limits_report_truncation(tmp_path):
    profile = _kuka_profile(tmp_path)
    session = EvidenceSession(
        profile,
        limits=EvidenceLimits(max_excerpt_samples=4, max_excerpt_channels=2,
                              max_statistics_entries=2, max_metadata_entries=3,
                              max_response_bytes=32_768),
    )

    stats = session.signal_statistics("PosMsr")
    metadata = session.metadata_summary()
    assert isinstance(stats, Evidence) and stats.value["truncated"]
    assert stats.value["returned_channels"] == 2
    assert isinstance(metadata, Evidence) and metadata.value["run_ids"]["truncated"]


def test_serialized_response_limit_is_enforced(tmp_path):
    session = EvidenceSession(
        _kuka_profile(tmp_path),
        limits=EvidenceLimits(max_excerpt_samples=1, max_excerpt_channels=1,
                              max_statistics_entries=1, max_metadata_entries=1,
                              max_response_bytes=1),
    )

    result = session.dataset_summary()

    assert result.code is EvidenceErrorCode.RESPONSE_TOO_LARGE

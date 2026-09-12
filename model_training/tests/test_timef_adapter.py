from types import SimpleNamespace

import numpy as np
import pytest

from robot_observability.data.timef import TimeFSessionRef, session_from_timef_record


class FakeSeries:
    def __init__(self, joint: int, values: np.ndarray, *, period_us: int = 1000) -> None:
        self.signal = f"joint_{joint}"
        self.spec = SimpleNamespace(spec_type="measured_external_joint_torque")
        self.n_values = len(values)
        self.time_axis = SimpleNamespace(period_us=period_us)
        self._values = values

    def to_numpy(self) -> np.ndarray:
        return self._values


def make_ref() -> TimeFSessionRef:
    return TimeFSessionRef(
        session_id="accidental/run-1",
        event_type="accidental",
        version_dir=None,  # type: ignore[arg-type]
        record_id="record-1",
        source_run_id="batch/run-1",
        event_key="collision",
        event_label="collision",
    )


def make_record(*, period_us: int = 1000):
    series = [
        FakeSeries(joint, np.arange(8, dtype=np.float64) + joint, period_us=period_us)
        for joint in reversed(range(1, 8))
    ]
    annotations = [
        SimpleNamespace(
            key="collision",
            value={"label": "collision", "python_index": 3, "timestamp_seconds": 0.003},
        )
    ]
    return SimpleNamespace(record_id="record-1", time_series=series, annotations=annotations)


def test_materializes_ordered_joint_channels_and_zero_based_events() -> None:
    session = session_from_timef_record(make_ref(), make_record())
    assert session.torque_nm.shape == (8, 7)
    np.testing.assert_array_equal(session.torque_nm[0], np.arange(1, 8))
    assert session.event_samples.tolist() == [3]
    assert session.timestamps_s[-1] == pytest.approx(0.007)


def test_rejects_non_1khz_timef_series() -> None:
    with pytest.raises(ValueError, match="regular 1 kHz"):
        session_from_timef_record(make_ref(), make_record(period_us=2000))


def test_rejects_timestamp_index_disagreement() -> None:
    record = make_record()
    record.annotations[0].value["timestamp_seconds"] = 0.004
    with pytest.raises(ValueError, match="disagrees with sample"):
        session_from_timef_record(make_ref(), record)

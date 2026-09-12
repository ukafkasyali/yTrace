import numpy as np

from robot_observability.data.pseudolabels import derive_evidence_labels


def test_strongest_joint_uses_calibrated_top_fraction_mean() -> None:
    signal = np.zeros((1024, 7), dtype=np.float32)
    signal[400:430, 3] = 10.0
    signal[400, 1] = 100.0  # one isolated spike must not beat a sustained disturbance
    labels = derive_evidence_labels(
        signal,
        400,
        np.ones(7),
        affected_threshold=np.full(7, 3.0),
        sustain_samples=5,
    )
    assert labels.strongest_joint == "J4"
    assert labels.evidence_start_ms == 400
    assert "J4" in labels.affected_joints


def test_joint_thresholds_calibrate_noisy_channels() -> None:
    signal = np.zeros((1024, 7), dtype=np.float32)
    signal[300:350, 0] = 8.0
    signal[300:350, 1] = 12.0
    labels = derive_evidence_labels(
        signal,
        300,
        np.ones(7),
        affected_threshold=np.asarray([2.0, 6.0, 3, 3, 3, 3, 3]),
    )
    assert labels.strongest_joint == "J1"

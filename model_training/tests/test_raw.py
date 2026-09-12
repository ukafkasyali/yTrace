from pathlib import Path

import numpy as np
from scipy.io import savemat

from robot_observability.data.raw import discover_sessions, load_session


def test_matlab_indices_become_zero_based(tmp_path: Path) -> None:
    session = tmp_path / "collision" / "03-15-12-53"
    session.mkdir(parents=True)
    timestamps = np.arange(2000) / 1000
    matrix = np.vstack([timestamps, np.zeros((7, 2000))])
    savemat(session / "JK_MsrExtTrq.mat", {"MsrExtTrq": matrix}, format="4")
    savemat(session / "JK_moments.mat", {"moments": np.asarray([[257, 1025]])})
    refs = discover_sessions(tmp_path)
    loaded = load_session(refs[0])
    assert loaded.torque_nm.shape == (2000, 7)
    assert loaded.event_samples.tolist() == [256, 1024]

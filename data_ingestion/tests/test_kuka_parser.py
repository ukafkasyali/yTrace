from pathlib import Path
import tempfile
import unittest

import numpy as np
from scipy.io import savemat

from dataset_profiler.datasets import (
    collision_time_seconds,
    discover_kuka_runs,
    matlab_index_to_python,
    parse_collision_indices,
    parse_time_axis,
    parse_timed_joint_matrix,
)


class KukaParserTests(unittest.TestCase):
    def test_matlab_index_converts_to_python_index(self) -> None:
        self.assertEqual(matlab_index_to_python(1, n_samples=5), 0)
        self.assertEqual(matlab_index_to_python(5, n_samples=5), 4)
        with self.assertRaises(ValueError):
            matlab_index_to_python(0, n_samples=5)
        with self.assertRaises(ValueError):
            matlab_index_to_python(6, n_samples=5)

    def test_collision_timestamp_uses_converted_index(self) -> None:
        time_axis = np.array([0.0, 0.001, 0.002, 0.003])
        self.assertEqual(collision_time_seconds(time_axis, 3), time_axis[2])

    def test_shared_parser_decodes_run_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory) / "collision-batch-01" / "run-001"
            run.mkdir(parents=True)
            time_axis = np.arange(5, dtype=np.float64) / 1000
            matrix = np.vstack([time_axis, np.arange(35, dtype=np.float64).reshape(7, 5)])
            savemat(run / "JsmoExp.mat", {"rt_tout": time_axis[:, None]})
            savemat(run / "JK_MsrExtTrq.mat", {"MsrExtTrq": matrix})
            savemat(run / "JK_PosMsr.mat", {"PosMsr": matrix})
            savemat(run / "JK_moments.mat", {"JK_moments": np.array([[1], [3], [5]])})

            self.assertEqual(discover_kuka_runs(directory), [run])
            parsed_time = parse_time_axis(run)
            parsed = parse_timed_joint_matrix(
                run / "JK_MsrExtTrq.mat",
                "MsrExtTrq",
                expected_time=parsed_time,
            )
            self.assertEqual(parsed.channels.shape, (7, 5))
            np.testing.assert_array_equal(parse_collision_indices(run / "JK_moments.mat"), [1, 3, 5])


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

import numpy as np
from scipy.io import savemat

from dataset_profiler.datasets import KukaCollisionHints
from dataset_profiler.inspection import inspect_mat_file
from dataset_profiler.profiler import profile_dataset


class ProfilerTests(unittest.TestCase):
    def test_event_indices_are_not_inferred_as_a_time_axis(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            savemat(root / "JK_moments.mat", {"JK_moments": np.array([[100, 200, 300]])})
            profile = profile_dataset(root, "fixture", hints=KukaCollisionHints())
            self.assertIsNone(profile.runs[0].sampling_rate_hz)
            self.assertIsNone(profile.runs[0].sequence_length)
            self.assertEqual(len(profile.runs[0].events), 3)
            self.assertTrue(all(e.inferred_time_seconds is None for e in profile.runs[0].events))

    def test_inspector_reports_numeric_details(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "signal.mat"
            savemat(path, {"signal": np.array([[0.0, 1.0], [np.nan, np.inf]])})
            result = inspect_mat_file(path)
            variable = result.variables[0]
            self.assertEqual(variable.name, "signal")
            self.assertEqual(variable.shape, [2, 2])
            self.assertEqual(variable.nan_count, 1)
            self.assertEqual(variable.inf_count, 1)

    def test_profiles_runs_provenance_and_audits(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for run_name in ("run-a", "run-b"):
                run = root / run_name
                run.mkdir()
                time = np.arange(5, dtype=float) / 1000
                signal = np.vstack([time, np.ones(5), np.arange(5)])
                savemat(run / "JsmoExp.mat", {"rt_tout": time[:, None]})
                savemat(run / "JK_MsrExtTrq.mat", {"MsrExtTrq": signal})
            profile = profile_dataset(root, "fixture")
            self.assertEqual(len(profile.runs), 2)
            self.assertEqual(profile.runs[0].sampling_rate_hz, 1000.0)
            self.assertNotEqual(profile.runs[0].internal_id, profile.runs[1].internal_id)
            self.assertIn("source_run_id", profile.entities[0]["future_window_provenance_fields"])
            parsed = json.loads(profile.to_json())
            self.assertEqual(parsed["dataset_id"], "fixture")
            self.assertTrue(any(issue.check == "constant_or_near_constant_channels" for issue in profile.quality.issues))

    def test_inspector_describes_nested_matlab_structure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "nested.mat"
            savemat(path, {"metadata": {"name": "fixture", "value": np.array([1, 2])}})
            result = inspect_mat_file(path)
            variable = result.variables[0]
            self.assertEqual(variable.name, "metadata")
            self.assertIsNotNone(variable.nested_structure)


if __name__ == "__main__":
    unittest.main()

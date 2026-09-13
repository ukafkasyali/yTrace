from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

import numpy as np
from scipy.io import savemat

try:
    import h5py
except ImportError:  # pragma: no cover - exercised by the hdf5 extra
    h5py = None

from dataset_profiler.datasets import KukaCollisionHints
from dataset_profiler.inspection import inspect_mat_file
from dataset_profiler.io.hdf5 import inspect_hdf5_file, inspect_hdf5_structure
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

    @unittest.skipIf(h5py is None, "hdf5 extra is not installed")
    def test_hdf5_inspector_reports_structure_attributes_and_bounded_stats(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "signal.h5"
            with h5py.File(path, "w") as handle:
                handle.attrs["creator"] = "fixture"
                group = handle.create_group("sensors")
                group.attrs["location"] = "test"
                values = np.array([[1.0, 2.0], [3.0, np.nan], [np.inf, 6.0]])
                dataset = group.create_dataset("values", data=values)
                dataset.attrs["units"] = np.array(["a", "b"], dtype="S1")

            result = inspect_hdf5_file(path)
            variable = result.variables[0]
            self.assertEqual(variable.name, "sensors/values")
            self.assertEqual(variable.shape, [3, 2])
            self.assertEqual(variable.nan_count, 1)
            self.assertEqual(variable.inf_count, 1)
            self.assertEqual(len(variable.channel_stats), 2)
            self.assertEqual(variable.nested_structure["structural_axes"]["sample_axis"], 0)
            self.assertEqual(variable.nested_structure["hdf5_attributes"]["units"]["values"], ["a", "b"])
            self.assertAlmostEqual(variable.nested_structure["numeric_statistics"]["mean"], 3.0)

            structure = inspect_hdf5_structure(path)
            self.assertEqual(structure.root_attributes, {"creator": "fixture"})
            self.assertEqual(structure.groups[0]["path"], "sensors")

    @unittest.skipIf(h5py is None, "hdf5 extra is not installed")
    def test_hdf5_inspector_streams_large_channel_first_arrays(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "channel-first.h5"
            values = np.repeat(
                np.array([[1.0], [2.0], [3.0]], dtype=np.float32),
                600_000,
                axis=1,
            )
            with h5py.File(path, "w") as handle:
                handle.create_dataset("measurements", data=values)

            variable = inspect_hdf5_file(path).variables[0]

            self.assertEqual(variable.shape, [3, 600_000])
            self.assertEqual(
                variable.nested_structure["structural_axes"]["channel_axis"], 0
            )
            self.assertEqual(len(variable.channel_stats), 3)
            self.assertEqual(
                [channel.mean for channel in variable.channel_stats], [1.0, 2.0, 3.0]
            )
            self.assertTrue(
                all(channel.count == 600_000 for channel in variable.channel_stats)
            )

    @unittest.skipIf(h5py is None, "hdf5 extra is not installed")
    def test_profiles_recursive_hdf5_files_as_records(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for relative, dtype, length in (
                ("machine-a/process-a/group-a/one.h5", np.float32, 5),
                ("machine-b/process-b/group-b/two.h5", np.int64, 7),
            ):
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                with h5py.File(path, "w") as handle:
                    handle.create_dataset("measurements", data=np.arange(
                        length * 3, dtype=dtype).reshape(length, 3))

            profile = profile_dataset(root, "hdf5-fixture")
            self.assertEqual(len(profile.files), 2)
            self.assertEqual(len(profile.runs), 2)
            self.assertEqual(profile.observed_structure["record_boundary"], "HDF5 file")
            self.assertEqual(profile.discovery["hdf5_files"], 2)
            self.assertEqual(profile.signals[0]["data_channel_count"], [3])
            self.assertEqual(profile.signals[0]["channel_axis"], 1)
            self.assertEqual(profile.signals[0]["sample_axis"], 0)
            self.assertEqual(sorted(run.sequence_length for run in profile.runs), [5, 7])


if __name__ == "__main__":
    unittest.main()

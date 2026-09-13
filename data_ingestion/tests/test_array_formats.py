import tempfile
import unittest
import zipfile
from pathlib import Path

import numpy as np
from scipy.io import savemat

from dataset_profiler.ingestion.formats.arrays import NumericArrayAdapter
from dataset_profiler.ingestion.formats.tabular import FormatAdapterError
from dataset_profiler.ingestion.jobs import ResourceFormat


class NumericArrayAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.adapter = NumericArrayAdapter(max_elements_per_array=100)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_npy_shape_dtype_values_and_pickle_rejection(self) -> None:
        path = self.root / "signals.npy"
        expected = np.arange(12, dtype=np.float64).reshape(3, 4).T
        np.save(path, expected, allow_pickle=False)

        profile = self.adapter.inspect(path, ResourceFormat.NPY)
        values = self.adapter.read_arrays(path, ResourceFormat.NPY, ["array"])

        self.assertEqual(profile.arrays[0].shape, (4, 3))
        np.testing.assert_array_equal(values.arrays["array"], expected)

        unsafe = self.root / "objects.npy"
        np.save(unsafe, np.array([{"instruction": "run me"}], dtype=object), allow_pickle=True)
        with self.assertRaisesRegex(FormatAdapterError, "unsafe"):
            self.adapter.inspect(unsafe, ResourceFormat.NPY)

    def test_npz_named_arrays_and_non_array_members(self) -> None:
        path = self.root / "signals.npz"
        np.savez(
            path,
            time=np.array([0.0, 0.1]),
            signals=np.array([[1.0, 2.0], [3.0, 4.0]]),
        )

        profile = self.adapter.inspect(path, ResourceFormat.NPZ)
        data = self.adapter.read_arrays(path, ResourceFormat.NPZ, ["time", "signals"])

        self.assertEqual([array.name for array in profile.arrays], ["signals", "time"])
        np.testing.assert_array_equal(data.arrays["time"], np.array([0.0, 0.1]))

        with zipfile.ZipFile(path, "a") as archive:
            archive.writestr("payload.py", "print('never run')")
        with self.assertRaisesRegex(FormatAdapterError, "non-array"):
            self.adapter.inspect(path, ResourceFormat.NPZ)

    def test_mat_numeric_variables_are_selected_without_cells(self) -> None:
        path = self.root / "signals.mat"
        expected = np.array([[0.0, 1.0], [0.1, 2.0]])
        savemat(
            path,
            {
                "signals": expected,
                "labels": np.array(["contact", "free"], dtype=object),
            },
        )

        profile = self.adapter.inspect(path, ResourceFormat.MAT)
        data = self.adapter.read_arrays(path, ResourceFormat.MAT, ["signals"])

        self.assertEqual([array.name for array in profile.arrays], ["signals"])
        np.testing.assert_array_equal(data.arrays["signals"], expected)

    def test_shape_limits_fail_before_mapping(self) -> None:
        path = self.root / "large.npy"
        np.save(path, np.zeros((11, 10), dtype=np.float64), allow_pickle=False)

        with self.assertRaisesRegex(FormatAdapterError, "element limit"):
            self.adapter.inspect(path, ResourceFormat.NPY)

    def test_hdf5_nested_numeric_datasets_are_bounded(self) -> None:
        try:
            import h5py
        except ImportError:
            self.skipTest("h5py is unavailable in the lightweight test environment")
        path = self.root / "signals.h5"
        with h5py.File(path, "w") as container:
            container.create_dataset("run/time", data=np.array([0.0, 0.1]))
            container.create_dataset("run/signals", data=np.array([[1.0], [2.0]]))
            container.create_dataset("run/labels", data=np.array([b"a", b"b"]))

        profile = self.adapter.inspect(path, ResourceFormat.HDF5)
        data = self.adapter.read_arrays(path, ResourceFormat.HDF5, ["run/time"])

        self.assertEqual(
            [array.name for array in profile.arrays],
            ["run/signals", "run/time"],
        )
        np.testing.assert_array_equal(data.arrays["run/time"], np.array([0.0, 0.1]))


if __name__ == "__main__":
    unittest.main()

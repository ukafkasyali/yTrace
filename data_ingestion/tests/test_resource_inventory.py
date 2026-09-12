import tempfile
import unittest
from pathlib import Path

import numpy as np
from scipy.io import savemat

from dataset_profiler.ingestion import ResourceFormat, ResourceInventory


class ResourceInventoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.inventory = ResourceInventory(max_probe_bytes=4_096)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def inspect(self, name: str, content: bytes):
        path = self.root / name
        path.write_bytes(content)
        return self.inventory.inspect(
            ingestion_id="11111111-1111-4111-8111-111111111111",
            asset_id="asset_0123456789abcdef",
            logical_path=name,
            path=path,
        )

    def test_delimited_inventory_requires_matching_dialect(self) -> None:
        csv_profile = self.inspect("signals.csv", b"time,j1,j2\n0,1,2\n1,2,3\n")
        tsv_profile = self.inspect("signals.tsv", b"time\tj1\n0\t1\n")
        mismatch = self.inspect("wrong.csv", b"time\tj1\n0\t1\n")

        self.assertEqual(csv_profile.format, ResourceFormat.CSV)
        self.assertEqual(csv_profile.details["columnCount"], 3)
        self.assertEqual(tsv_profile.format, ResourceFormat.TSV)
        self.assertEqual(mismatch.format, ResourceFormat.UNSUPPORTED)
        self.assertEqual(mismatch.details["reason"], "EXTENSION_DIALECT_MISMATCH")

    def test_binary_magic_and_extension_must_agree(self) -> None:
        parquet = self.inspect("signals.parquet", b"PAR1metadataPAR1")
        mismatch = self.inspect("signals.csv", b"PAR1metadataPAR1")

        npy_path = self.root / "signals.npy"
        with npy_path.open("wb") as output:
            np.save(output, np.array([[1.0, 2.0]]), allow_pickle=False)
        npy = self.inventory.inspect(
            ingestion_id="11111111-1111-4111-8111-111111111111",
            asset_id="asset_0123456789abcdef",
            logical_path="signals.npy",
            path=npy_path,
        )

        npz_path = self.root / "signals.npz"
        np.savez(npz_path, signal=np.array([1.0, 2.0]))
        npz = self.inventory.inspect(
            ingestion_id="11111111-1111-4111-8111-111111111111",
            asset_id="asset_0123456789abcdef",
            logical_path="signals.npz",
            path=npz_path,
        )

        self.assertEqual(parquet.format, ResourceFormat.PARQUET)
        self.assertEqual(npy.format, ResourceFormat.NPY)
        self.assertEqual(npz.format, ResourceFormat.NPZ)
        self.assertEqual(mismatch.details["reason"], "EXTENSION_MAGIC_MISMATCH")

    def test_mat_hdf5_and_unknown_files_have_stable_results(self) -> None:
        mat_path = self.root / "signals.mat"
        savemat(mat_path, {"signals": np.array([[1.0, 2.0]])})
        mat = self.inventory.inspect(
            ingestion_id="11111111-1111-4111-8111-111111111111",
            asset_id="asset_0123456789abcdef",
            logical_path="signals.mat",
            path=mat_path,
        )
        hdf5 = self.inspect("signals.h5", b"\x89HDF\r\n\x1a\nsynthetic")
        executable = self.inspect("dataset.py", b"print('never execute me')\n")
        empty = self.inspect("empty.csv", b"")
        nested_archive = self.inspect("nested.zip", b"PK\x03\x04synthetic")

        self.assertEqual(mat.format, ResourceFormat.MAT)
        self.assertEqual(hdf5.format, ResourceFormat.HDF5)
        self.assertEqual(executable.format, ResourceFormat.UNSUPPORTED)
        self.assertEqual(executable.details["reason"], "UNSUPPORTED_FORMAT")
        self.assertEqual(empty.details["reason"], "DELIMITED_TEXT_TOO_SHORT")
        self.assertEqual(nested_archive.details["reason"], "EXTENSION_MAGIC_MISMATCH")


if __name__ == "__main__":
    unittest.main()

import tempfile
import unittest
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from dataset_profiler.ingestion.formats.tabular import (
    FormatAdapterError,
    TabularAdapter,
)
from dataset_profiler.ingestion.jobs import ResourceFormat


class TabularAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.adapter = TabularAdapter(max_rows=10)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_csv_schema_projection_missingness_and_source_rows(self) -> None:
        path = self.root / "wide.csv"
        path.write_text("run,time,j1,j2\na,0.0,1,\na,0.1,2,3.5\n", encoding="utf-8")

        profile = self.adapter.inspect(path, ResourceFormat.CSV)
        data = self.adapter.read_columns(
            path,
            ResourceFormat.CSV,
            ["run", "time", "j1", "j2"],
        )

        self.assertEqual(profile.row_count, 2)
        self.assertEqual(
            [column.dtype for column in profile.columns],
            ["string", "float64", "int64", "float64"],
        )
        self.assertEqual(data.columns["j2"].tolist(), [None, 3.5])
        self.assertEqual(data.source_rows.tolist(), [2, 3])

    def test_tsv_malformed_rows_and_limits_fail_closed(self) -> None:
        malformed = self.root / "bad.tsv"
        malformed.write_text("time\tj1\n0\t1\textra\n", encoding="utf-8")
        with self.assertRaisesRegex(FormatAdapterError, "row width"):
            self.adapter.inspect(malformed, ResourceFormat.TSV)

        oversized = self.root / "large.csv"
        oversized.write_text(
            "time,j1\n" + "\n".join(f"{i},{i}" for i in range(11)),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(FormatAdapterError, "row limit"):
            self.adapter.inspect(oversized, ResourceFormat.CSV)

    def test_parquet_uses_metadata_and_projected_reads(self) -> None:
        path = self.root / "signals.parquet"
        table = pa.table(
            {
                "run": ["a", "a", "b"],
                "time": [0.0, 0.1, 0.0],
                "j1": [1.0, None, 3.0],
                "unused": [9, 9, 9],
            }
        )
        pq.write_table(table, path, row_group_size=2)

        profile = self.adapter.inspect(path, ResourceFormat.PARQUET)
        data = self.adapter.read_columns(
            path,
            ResourceFormat.PARQUET,
            ["run", "time", "j1"],
        )

        self.assertEqual(profile.row_count, 3)
        self.assertEqual(profile.row_groups, 2)
        self.assertEqual(set(data.columns), {"run", "time", "j1"})
        self.assertEqual(data.columns["j1"].tolist(), [1.0, None, 3.0])
        self.assertEqual(data.source_rows.tolist(), [1, 2, 3])


if __name__ == "__main__":
    unittest.main()

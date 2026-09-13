from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace

from dataset_profiler.ingestion.catalog import (
    ImportedDatasetCatalog,
    ImportedDatasetUnavailable,
)


class FakeClient:
    def __init__(self, records):
        self.records = records
        self.requests = []

    def load(self, dataset_id: str, version: str | None = None, *, auto_build: bool):
        self.requests.append((dataset_id, version, auto_build))
        return SimpleNamespace(records=self.records)


def record(number: int):
    axis = SimpleNamespace(period_us=1_000)
    series = [
        SimpleNamespace(
            signal=f"joint_{index}", n_values=1_001, time_axis=axis,
            spec=SimpleNamespace(spec_type="measured_external_joint_torque"),
        )
        for index in range(1, 8)
    ]
    annotations = [SimpleNamespace(key="collision"), SimpleNamespace(key="source_run_id")]
    return SimpleNamespace(
        record_id=f"batch-01/run-{number:02d}",
        time_series=series,
        annotations=annotations,
    )


class ImportedDatasetCatalogTests(unittest.TestCase):
    def test_lists_bounded_record_summaries_with_pagination(self) -> None:
        client = FakeClient([record(1), record(2), record(3)])
        catalog = ImportedDatasetCatalog(Path("unused"), client=client)

        result = catalog.list_records(
            "kuka/collision-part1", "1.0.0", page=2, page_size=2
        )

        self.assertEqual(client.requests, [("kuka/collision-part1", "1.0.0", False)])
        self.assertEqual(result.pagination.total_items, 3)
        self.assertEqual(result.pagination.total_pages, 2)
        self.assertEqual([item.record_id for item in result.data], ["batch-01/run-03"])
        self.assertEqual(result.data[0].series_count, 7)
        self.assertEqual(result.data[0].value_count, 7_007)
        self.assertEqual(result.data[0].duration_seconds, 1)
        self.assertEqual(
            result.data[0].signals,
            [f"joint_{index}" for index in range(1, 8)],
        )
        self.assertTrue(result.data[0].is_replay_compatible)
        self.assertEqual(
            result.data[0].annotation_keys, ["collision", "source_run_id"]
        )

    def test_rejects_invalid_dataset_identity_before_registry_access(self) -> None:
        client = FakeClient([])
        catalog = ImportedDatasetCatalog(Path("unused"), client=client)

        with self.assertRaisesRegex(ImportedDatasetUnavailable, "identity"):
            catalog.list_records("../outside", "1.0.0", page=1, page_size=20)

        self.assertEqual(client.requests, [])


if __name__ == "__main__":
    unittest.main()

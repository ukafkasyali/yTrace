from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from dataset_profiler.ingestion.catalog import ImportedDatasetCatalog, imported_record_key
from dataset_profiler.ingestion.replay import ImportedReplayService


class FakeClient:
    def __init__(self, record):
        self.record = record

    def load(self, dataset_id: str, version: str | None = None, *, auto_build: bool):
        return SimpleNamespace(records=[self.record])


def time_series(joint: int):
    values = np.arange(2_501, dtype=np.float64) + joint
    return SimpleNamespace(
        signal=f"joint_{joint}",
        n_values=values.size,
        time_axis=SimpleNamespace(period_us=1_000),
        spec=SimpleNamespace(spec_type="measured_external_joint_torque"),
        to_numpy=lambda values=values: values,
    )


def kuka_record():
    annotation = SimpleNamespace(
        key="collision",
        span=SimpleNamespace(start_us=600_000),
        value={"label": "collision"},
        source="publisher/JK_moments.mat",
    )
    return SimpleNamespace(
        record_id="kuka-part1-batch-01--run-01",
        time_series=[time_series(joint) for joint in range(1, 8)],
        annotations=[annotation],
    )


class ImportedReplayServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.record = kuka_record()
        catalog = ImportedDatasetCatalog(Path("unused"), FakeClient(self.record))
        self.replay = ImportedReplayService(catalog)
        self.key = imported_record_key(self.record.record_id)
        self.ingestion_id = "11111111-1111-4111-8111-111111111111"

    def test_replay_uses_imported_torque_and_publisher_annotations(self) -> None:
        result = self.replay.replay(
            "kuka/collision-part1", "1.0.0", self.ingestion_id, self.key,
            "https://zenodo.org/records/21927431",
        )

        self.assertEqual(result.recording.id, f"imported:{self.ingestion_id}:{self.key}")
        self.assertEqual(result.recording.name, self.record.record_id)
        self.assertEqual(result.recording.sample_rate_hz, 1_000)
        self.assertEqual([channel.id for channel in result.channels], [f"joint_{i}" for i in range(1, 8)])
        self.assertEqual(result.channels[0].unit, "Nm")
        self.assertEqual(result.events[0].time_seconds, 0.6)
        self.assertEqual(result.events[0].source, "publisher/JK_moments.mat")
        self.assertEqual(result.detail.start_seconds, 0.2)
        self.assertEqual(result.detail.times[0], 0.2)

    def test_signal_window_returns_exact_model_shape(self) -> None:
        result = self.replay.signals(
            "kuka/collision-part1", "1.0.0", self.ingestion_id, self.key,
            start_sec=1.0, end_sec=2.024,
            channel_ids=[f"joint_{i}" for i in range(1, 8)], max_points=1_024,
        )

        self.assertEqual(result.resolution, "raw")
        self.assertEqual(len(result.series), 7)
        self.assertTrue(all(len(series.values) == 1_024 for series in result.series))
        self.assertEqual(result.series[0].time_sec[0], 1.0)
        self.assertEqual(result.series[0].time_sec[-1], 2.023)
        self.assertEqual(result.window.recording_id, f"imported:{self.ingestion_id}:{self.key}")


if __name__ == "__main__":
    unittest.main()

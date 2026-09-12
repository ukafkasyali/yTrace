import copy
import json
import math
from pathlib import Path
import statistics
import tempfile
import unittest

from inference.runtime import prepare_sample, question_contract


class InputPreparationTests(unittest.TestCase):
    def setUp(self):
        self.request = {
            "question": "Describe the signal", "playheadSec": 8,
            "window": {"startSec": 5, "endSec": 5.003, "channelIds": ["joint_2", "joint_1"]},
            "publisherAnnotations": ["DO NOT INCLUDE THIS LABEL"],
        }
        self.series = [
            {"channelId": "joint_2", "timeSec": [5, 5.001, 5.002], "values": [1, 2, 3]},
            {"channelId": "joint_1", "timeSec": [5, 5.001, 5.002], "values": [7, 7, 7]},
        ]

    def test_preserves_order_count_and_no_targets(self):
        before = copy.deepcopy(self.series)
        result = prepare_sample(self.request, self.series, "none")
        self.assertEqual(result["time_series"], [[1, 2, 3], [7, 7, 7]])
        self.assertEqual(result["answer"], "")
        self.assertNotIn("DO NOT INCLUDE", str(result))
        self.assertIn("J2 external joint torque", result["time_series_text"][0])
        self.assertEqual(result["intent"], "summary")
        self.assertEqual(before, self.series)

    def test_normalizes_with_sample_std_and_constant_channel_is_finite(self):
        result = prepare_sample(self.request, self.series, "zscore_sample")
        self.assertAlmostEqual(statistics.stdev(result["time_series"][0]), 1)
        self.assertEqual(result["time_series"][1], [0, 0, 0])
        self.assertIn("train-only robust statistics", result["time_series_text"][0])

    def test_robust_normalization_uses_stable_joint_identity(self):
        self.series[0]["values"] = [11, 12, 13]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "normalization.json"
            path.write_text(json.dumps({"center_nm": [0, 10, 0, 0, 0, 0, 0], "scale_nm": [1] * 7, "clip": 100}))
            result = prepare_sample(self.request, self.series, "train_robust", str(path))
        self.assertEqual(result["time_series"][0], [1, 2, 3])
        self.assertEqual(result["time_series"][1], [7, 7, 7])

    def test_routes_operator_questions_to_trained_contracts(self):
        self.assertEqual(question_contract("Did contact occur?")[2], "contact")
        self.assertEqual(question_contract("Which joint is strongest?")[2], "strongest_joint")
        question, keys, intent = question_contract("Analyze the event and timing")
        self.assertEqual((question, intent), ("Diagnose this robot telemetry window.", "summary"))
        self.assertIn("onset_ms", keys)

    def test_rejects_future_boundary_and_unaligned_series(self):
        self.series[0]["timeSec"][-1] = 5.003
        with self.assertRaises(ValueError):
            prepare_sample(self.request, self.series, "none")
        self.series[0]["timeSec"][-1] = 5.002
        self.request["playheadSec"] = 5.002
        with self.assertRaises(ValueError):
            prepare_sample(self.request, self.series, "none")

    def test_rejects_channel_order_and_missing_values(self):
        with self.assertRaises(ValueError):
            prepare_sample(self.request, self.series[::-1], "none")
        self.series[0]["values"][0] = math.nan
        with self.assertRaises(ValueError):
            prepare_sample(self.request, self.series, "none")

    def test_unknown_preprocessing_fails(self):
        with self.assertRaises(ValueError):
            prepare_sample(self.request, self.series, "guess")


if __name__ == "__main__":
    unittest.main()

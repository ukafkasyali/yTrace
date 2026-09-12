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
            "window": {"startSec": 5, "endSec": 6.024, "channelIds": [f"joint_{i}" for i in range(1, 8)]},
            "publisherAnnotations": ["DO NOT INCLUDE THIS LABEL"],
        }
        self.series = [{"channelId": f"joint_{i}", "timeSec": [round(5 + j / 1000, 3) for j in range(1024)],
                        "values": list(range(1024)) if i == 1 else [7] * 1024} for i in range(1, 8)]

    def test_preserves_order_count_and_no_targets(self):
        before = copy.deepcopy(self.series)
        result = prepare_sample(self.request, self.series, "none")
        self.assertEqual(result["time_series"], [c["values"] for c in self.series])
        self.assertEqual(result["answer"], "")
        self.assertNotIn("DO NOT INCLUDE", str(result))
        self.assertIn("J1 external joint torque", result["time_series_text"][0])
        self.assertEqual(result["intent"], "summary")
        self.assertEqual(before, self.series)

    def test_normalizes_with_sample_std_and_constant_channel_is_finite(self):
        result = prepare_sample(self.request, self.series, "zscore_sample")
        self.assertAlmostEqual(statistics.stdev(result["time_series"][0]), 1)
        self.assertEqual(result["time_series"][1], [0] * 1024)
        self.assertIn("train-only robust statistics", result["time_series_text"][0])

    def test_robust_normalization_uses_stable_joint_identity(self):
        self.series[1]["values"] = [11] * 1024
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "normalization.json"
            path.write_text(json.dumps({"center_nm": [0, 10, 0, 0, 0, 0, 0], "scale_nm": [1] * 7, "clip": 100}))
            result = prepare_sample(self.request, self.series, "train_robust", str(path))
        self.assertEqual(result["time_series"][1], [1] * 1024)
        self.assertEqual(result["time_series"][0], list(range(101)) + [100] * 923)

    def test_routes_operator_questions_to_trained_contracts(self):
        self.assertEqual(question_contract("Did contact occur?")[2], "contact")
        self.assertEqual(question_contract("Which joint is strongest?")[2], "strongest_joint")
        question, keys, intent = question_contract("Analyze the event and timing")
        self.assertEqual((question, intent), ("Diagnose this robot telemetry window.", "summary"))
        self.assertIn("onset_ms", keys)

    def test_rejects_future_boundary_and_unaligned_series(self):
        self.series[0]["timeSec"][-1] = 6.024
        with self.assertRaises(ValueError):
            prepare_sample(self.request, self.series, "none")
        self.series[0]["timeSec"][-1] = 6.023
        self.request["playheadSec"] = 6.023
        with self.assertRaises(ValueError):
            prepare_sample(self.request, self.series, "none")

    def test_rejects_channel_order_and_missing_values(self):
        with self.assertRaises(ValueError):
            prepare_sample(self.request, self.series[::-1], "none")
        self.series[0]["values"][0] = math.nan
        with self.assertRaises(ValueError):
            prepare_sample(self.request, self.series, "none")

    def test_rejects_wrong_shape_missing_sample_and_bad_cadence(self):
        for mutation in ("short", "channels", "gap", "duration"):
            request, series = copy.deepcopy(self.request), copy.deepcopy(self.series)
            if mutation == "short":
                for c in series:
                    c["values"].pop(); c["timeSec"].pop()
            elif mutation == "channels":
                series.pop(); request["window"]["channelIds"].pop()
            elif mutation == "gap":
                for c in series:
                    c["timeSec"][500] += .0001
            else:
                request["window"]["endSec"] += .001
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                prepare_sample(request, series, "none")

    def test_unknown_preprocessing_fails(self):
        with self.assertRaises(ValueError):
            prepare_sample(self.request, self.series, "guess")


if __name__ == "__main__":
    unittest.main()

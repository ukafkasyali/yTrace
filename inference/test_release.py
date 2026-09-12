import json
from pathlib import Path
import tempfile
import unittest

import torch

from inference.release import stage_release, verified_evaluation


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.candidate = self.root / "best_model.pt"
        torch.save(
            {
                "encoder_state": {"weight": torch.ones(2)},
                "projector_state": {"weight": torch.ones(2)},
                "lora_enabled": True,
                "lora_state": {"adapter_lora_A": torch.ones(2)},
            },
            self.candidate,
        )
        self.status = self.root / "post_training_status.json"
        self.status.write_text(json.dumps({"state": "complete", "metrics": {"test/parse_validity": 1.0}}))
        self.config = self.root / "active.json"
        self.config.write_text(json.dumps({"architecture": "sp", "lora": {"lora_r": 16}}))

    def tearDown(self):
        self.temp.cleanup()

    def test_stages_immutable_checkpoint_and_config(self):
        output = self.root / "next.json"
        result = stage_release(self.candidate, self.status, self.config, self.root / "models", output)
        released = json.loads(output.read_text())
        self.assertEqual(released["checkpoint_sha256"], result["checkpoint_sha256"])
        self.assertTrue(Path(released["checkpoint_path"]).exists())
        self.assertTrue(Path(released["checkpoint_path"]).with_suffix(".release.json").exists())

    def test_rejects_incomplete_evaluation(self):
        self.status.write_text(json.dumps({"state": "evaluating", "metrics": {"score": 1.0}}))
        with self.assertRaisesRegex(ValueError, "not complete"):
            verified_evaluation(self.status)

    def test_rejects_unsafe_or_extra_checkpoint_state(self):
        state = torch.load(self.candidate, weights_only=True)
        state["lora_config"] = "must not be serialized"
        torch.save(state, self.candidate)
        with self.assertRaisesRegex(ValueError, "runtime contract"):
            stage_release(
                self.candidate,
                self.status,
                self.config,
                self.root / "models",
                self.root / "next.json",
            )


if __name__ == "__main__":
    unittest.main()

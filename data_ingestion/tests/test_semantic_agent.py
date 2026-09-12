from __future__ import annotations

import json
from pathlib import Path
import unittest

from dataset_profiler.evidence import EvidenceSession
from dataset_profiler.semantic_agent import SemanticAgent, SemanticAgentError
from dataset_profiler.semantic_agent.profile_io import read_dataset_profile

PROFILE = Path(__file__).parents[1] / "outputs" / "dataset_profile.json"

def minimal_spec(dataset_id: str) -> dict:
    return {"schema_version": "0.1", "identity": {"dataset_id": dataset_id, "source_subsets": []}, "record_discovery": {"record_unit": "unresolved", "boundary": "run_directory"}, "source_variables": [], "signals": [], "time_axes": [], "events": [], "provenance": []}

class FakeClient:
    provider, model = "fake", "test-model"
    def __init__(self, outputs): self.outputs = iter(outputs); self.messages = []
    def complete(self, messages, tools, schema):
        self.messages.append(messages); self.tool_names = set(tools)
        return next(self.outputs)

class SemanticAgentTests(unittest.TestCase):
    def setUp(self): self.profile = read_dataset_profile(PROFILE)

    def test_dispatches_only_bounded_tool_and_records_real_evidence_id(self):
        client = FakeClient([{"tool_calls": [{"id": "call-1", "name": "dataset_summary", "arguments": {}}]}, {"content": json.dumps(minimal_spec(self.profile.dataset_id))}])
        run = SemanticAgent(client).run(self.profile, EvidenceSession(self.profile))
        call = run.trace["tool_calls"][0]
        self.assertEqual(call["name"], "dataset_summary")
        self.assertEqual(len(call["returned_evidence_ids"]), 1)
        self.assertTrue(call["returned_evidence_ids"][0].startswith("ev_"))
        self.assertEqual(run.spec.schema_version, "0.1")
        self.assertNotIn("connector", " ".join(client.tool_names))
        self.assertNotIn("kuka_collision_part1", " ".join(str(m) for batch in client.messages for m in batch).casefold())

    def test_invalid_structured_output_is_reported(self):
        with self.assertRaises(SemanticAgentError):
            SemanticAgent(FakeClient([{"content": "{not-json"}])).run(self.profile, EvidenceSession(self.profile))

    def test_prompt_requires_generic_iterative_documentation_discovery(self):
        from dataset_profiler.semantic_agent.agent import SYSTEM_PROMPT
        self.assertIn("Documentation discovery is iterative and bounded", SYSTEM_PROMPT)
        self.assertIn("broad → informative → refined", SYSTEM_PROMPT)
        self.assertIn("one short concept or phrase", SYSTEM_PROMPT)
        self.assertNotIn("Newton-meters", SYSTEM_PROMPT)
        self.assertNotIn("KUKA", SYSTEM_PROMPT)

if __name__ == "__main__": unittest.main()

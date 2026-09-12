from __future__ import annotations

from io import BytesIO
import json
import socket
import unittest
from urllib.error import HTTPError

from dataset_profiler.semantic_agent.openai_responses import OpenAIResponsesClient, ResponsesStatusError


SCHEMA = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]}
TOOLS = {"get_test_value": {"type": "object", "properties": {}, "additionalProperties": False}}


class FakeResponse(BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


class SequenceOpener:
    def __init__(self, *results):
        self.results = iter(results)
        self.requests = []

    def __call__(self, request, *, timeout):
        self.requests.append((request, timeout))
        result = next(self.results)
        if isinstance(result, BaseException):
            raise result
        return FakeResponse(json.dumps(result).encode())


def completed(*output):
    return {"id": "resp_1", "status": "completed", "output": list(output), "error": None, "incomplete_details": None}


class OpenAIResponsesTests(unittest.TestCase):
    def client(self, opener, **kwargs):
        return OpenAIResponsesClient(
            "gpt-test", api_key="test-key", reasoning_effort="high", opener=opener, **kwargs
        )

    def body(self, opener, index=0):
        return json.loads(opener.requests[index][0].data)

    def test_plain_request_construction_timeout_and_nested_output_text(self):
        opener = SequenceOpener(completed({"type": "message", "content": [{"type": "output_text", "text": "OK"}]}))
        client = self.client(opener, timeout=17, max_output_tokens=2048)
        payload = client.create_response("Reply with exactly: OK")
        body = self.body(opener)
        self.assertEqual(body["model"], "gpt-test")
        self.assertEqual(body["reasoning"], {"effort": "high"})
        self.assertEqual(body["max_output_tokens"], 2048)
        self.assertEqual(opener.requests[0][1], 17)
        self.assertEqual(payload["status"], "completed")

    def test_function_call_uses_call_id_and_continuation_resends_instructions(self):
        first = completed({"type": "function_call", "id": "fc_item_1", "call_id": "call_1", "name": "get_test_value", "arguments": "{}"})
        second = {**completed({"type": "message", "content": [{"type": "output_text", "text": '{"ok":true}'}]}), "id": "resp_2"}
        opener = SequenceOpener(first, second)
        client = self.client(opener)
        messages = [{"role": "system", "content": "Keep this instruction."}, {"role": "user", "content": "Call the tool."}]
        response = client.complete(messages, TOOLS, SCHEMA)
        call = response["tool_calls"][0]
        self.assertEqual(call["id"], "call_1")
        self.assertEqual(call["call_id"], "call_1")
        self.assertEqual(call["item_id"], "fc_item_1")

        messages += [
            {"role": "assistant", "tool_calls": []},
            {"role": "tool", "tool_call_id": call["id"], "content": '{"value":"TEST_OK"}'},
        ]
        response = client.complete(messages, TOOLS, SCHEMA)
        body = self.body(opener, 1)
        self.assertEqual(body["previous_response_id"], "resp_1")
        self.assertEqual(body["instructions"], "Keep this instruction.")
        self.assertEqual(body["input"], [{"type": "function_call_output", "call_id": "call_1", "output": '{"value":"TEST_OK"}'}])
        self.assertEqual(response["content"], '{"ok":true}')

    def test_only_pending_tool_outputs_are_sent_on_later_continuations(self):
        first = completed({"type": "function_call", "id": "item_1", "call_id": "call_1", "name": "get_test_value", "arguments": "{}"})
        second = {**completed({"type": "function_call", "id": "item_2", "call_id": "call_2", "name": "get_test_value", "arguments": "{}"}), "id": "resp_2"}
        third = {**completed({"type": "message", "content": [{"type": "output_text", "text": '{"ok":true}'}]}), "id": "resp_3"}
        opener = SequenceOpener(first, second, third)
        client = self.client(opener)
        messages = [{"role": "system", "content": "instruction"}]
        client.complete(messages, TOOLS, SCHEMA)
        messages.append({"role": "tool", "tool_call_id": "call_1", "content": "one"})
        client.complete(messages, TOOLS, SCHEMA)
        messages.append({"role": "tool", "tool_call_id": "call_2", "content": "two"})
        client.complete(messages, TOOLS, SCHEMA)
        self.assertEqual(self.body(opener, 2)["input"], [{"type": "function_call_output", "call_id": "call_2", "output": "two"}])

    def test_incomplete_status_is_not_treated_as_completed(self):
        payload = {"id": "resp_incomplete", "status": "incomplete", "output": [], "error": None, "incomplete_details": {"reason": "max_output_tokens"}}
        client = self.client(SequenceOpener(payload))
        with self.assertRaises(ResponsesStatusError) as caught:
            client.complete([{"role": "user", "content": "test"}], {}, SCHEMA)
        self.assertEqual(caught.exception.payload["incomplete_details"]["reason"], "max_output_tokens")

    def test_timeout_is_propagated_and_logged(self):
        events = []
        client = self.client(SequenceOpener(socket.timeout("timed out")), diagnostic_logger=lambda event, fields: events.append((event, fields)))
        with self.assertRaisesRegex(RuntimeError, "timed out"):
            client.create_response("test")
        self.assertEqual(events[0][0], "sending request 1")
        self.assertEqual(events[-1][1]["exception_type"], "TimeoutError")

    def test_reasoning_fallback_is_explicit_and_only_for_unsupported_error(self):
        detail = b'{"error":{"message":"reasoning effort high is not supported"}}'
        error = HTTPError("https://api.openai.com/v1/responses", 400, "Bad Request", {}, BytesIO(detail))
        opener = SequenceOpener(error, completed({"type": "message", "content": [{"type": "output_text", "text": '{"ok":true}'}]}))
        client = self.client(opener, allow_reasoning_fallback=True)
        response = client.complete([{"role": "user", "content": "test"}], {}, SCHEMA)
        self.assertEqual(response["content"], '{"ok":true}')
        self.assertIsNone(client.effective_reasoning_effort)
        self.assertIsNotNone(client.fallback_reason)
        self.assertNotIn("reasoning", self.body(opener, 1))

    def test_fallback_disabled_propagates_api_error(self):
        detail = b'{"error":{"message":"reasoning effort high is not supported"}}'
        error = HTTPError("https://api.openai.com/v1/responses", 400, "Bad Request", {}, BytesIO(detail))
        client = self.client(SequenceOpener(error))
        with self.assertRaisesRegex(RuntimeError, "not supported"):
            client.complete([{"role": "user", "content": "test"}], {}, SCHEMA)
        self.assertEqual(client.effective_reasoning_effort, "high")


if __name__ == "__main__":
    unittest.main()

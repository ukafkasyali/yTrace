"""Minimal, observable live smoke tests for the official OpenAI Responses API."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
import json
from pathlib import Path
import time
from typing import Any, Callable

from dataset_profiler.evidence import EvidenceSession
from dataset_profiler.semantic_agent.openai_responses import OpenAIResponsesClient, _function_calls, _output_item_types, _output_text
from dataset_profiler.semantic_agent.profile_io import read_dataset_profile


TRIVIAL_TOOL = {
    "type": "function",
    "name": "get_test_value",
    "description": "Return a fixed test value.",
    "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
    "strict": True,
}
SUMMARY_TOOL = {
    "type": "function",
    "name": "dataset_summary",
    "description": "Return the bounded dataset summary.",
    "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
    "strict": True,
}


def log(event: str, fields: dict[str, Any] | None = None, **extra: Any) -> None:
    print(json.dumps({"time": datetime.now(UTC).isoformat(), "event": event, **(fields or {}), **extra}, allow_nan=False), flush=True)


def report(payload: dict[str, Any], *, latency_seconds: float) -> None:
    log(
        "response inspection",
        response_id=payload.get("id"),
        response_status=payload.get("status"),
        output_item_types=_output_item_types(payload),
        error=payload.get("error"),
        incomplete_details=payload.get("incomplete_details"),
        final_text=_output_text(payload),
        usage=payload.get("usage"),
        latency_seconds=round(latency_seconds, 3),
    )


def request(client: OpenAIResponsesClient, input_items: str | list[dict[str, Any]], **kwargs: Any) -> dict[str, Any]:
    started = time.monotonic()
    payload = client.create_response(input_items, **kwargs)
    report(payload, latency_seconds=time.monotonic() - started)
    return payload


def run_plain(client: OpenAIResponsesClient) -> bool:
    log("request started", model=client.model, reasoning_effort=client.effective_reasoning_effort, timeout=client.timeout)
    payload = request(client, "Reply with exactly: OK")
    return payload.get("status") == "completed" and (_output_text(payload) or "").strip() == "OK"


def run_tool(client: OpenAIResponsesClient, *, tool: dict[str, Any], prompt: str, output: str | Callable[[], str]) -> bool:
    first = request(client, prompt, tools=[tool])
    if first.get("status") != "completed":
        return False
    calls = _function_calls(first)
    for item in first.get("output", []):
        if item.get("type") == "function_call":
            log("function call", output_item_type=item.get("type"), function_name=item.get("name"), arguments=item.get("arguments"), item_id=item.get("id"), call_id=item.get("call_id"))
    if len(calls) != 1 or calls[0]["name"] != tool["name"]:
        log("unexpected function calls", calls=calls)
        return False
    call = calls[0]
    if callable(output):
        output = output()
    continuation_input = [{"type": "function_call_output", "call_id": call["call_id"], "output": output}]
    log("continuation request", previous_response_id=first.get("id"), call_id=call["call_id"], tool_output_payload=output)
    second = request(
        client,
        continuation_input,
        tools=[tool],
        previous_response_id=first["id"],
        instructions="Use the function result and answer the user's request.",
    )
    log("continuation result", continuation_response_id=second.get("id"), continuation_status=second.get("status"))
    return second.get("status") == "completed" and bool(_output_text(second))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="gpt-5.6-sol")
    parser.add_argument("--reasoning-effort", default="high", choices=["high", "medium", "low", "none"])
    parser.add_argument("--timeout", type=float, default=120)
    parser.add_argument("--max-output-tokens", type=int, default=4096)
    parser.add_argument("--test", choices=["plain", "trivial-tool", "dataset-summary"], default="plain")
    parser.add_argument("--profile", type=Path)
    args = parser.parse_args()
    client = OpenAIResponsesClient(
        args.model,
        reasoning_effort=args.reasoning_effort,
        allow_reasoning_fallback=False,
        timeout=args.timeout,
        max_output_tokens=args.max_output_tokens,
        diagnostic_logger=log,
    )
    try:
        if args.test == "plain":
            ok = run_plain(client)
        elif args.test == "trivial-tool":
            ok = run_tool(client, tool=TRIVIAL_TOOL, prompt="Call get_test_value, then reply with the returned value.", output=json.dumps({"value": "TEST_OK"}))
        else:
            if not args.profile:
                parser.error("--profile is required for dataset-summary")
            session = EvidenceSession(read_dataset_profile(args.profile))
            def dataset_summary_output() -> str:
                evidence = session.dataset_summary()
                rendered = evidence.to_dict() if hasattr(evidence, "to_dict") else evidence
                log("bounded tool executed", tool="dataset_summary", result=rendered)
                return json.dumps(rendered)

            ok = run_tool(client, tool=SUMMARY_TOOL, prompt="Call dataset_summary and summarize the dataset in one sentence.", output=dataset_summary_output)
    except Exception as exc:
        log("smoke test exception", exception_type=type(exc).__name__, exception_message=str(exc), request_stage=args.test)
        raise
    log("smoke test completed", test=args.test, success=ok, requested_reasoning_effort=args.reasoning_effort, effective_reasoning_effort=client.effective_reasoning_effort)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

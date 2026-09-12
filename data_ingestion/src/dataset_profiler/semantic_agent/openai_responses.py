"""Official OpenAI Responses API adapter for reasoning models with tools."""

from __future__ import annotations

import json
import os
import socket
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .openai_client import _dotenv_values


class ResponsesStatusError(RuntimeError):
    """A Responses request returned HTTP success but not a usable response."""

    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        status = payload.get("status")
        detail = payload.get("error") or payload.get("incomplete_details")
        super().__init__(f"OpenAI Responses API returned status {status!r}: {detail!r}")


class OpenAIResponsesClient:
    provider = "openai-responses"

    def __init__(
        self,
        model: str,
        *,
        reasoning_effort: str | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
        allow_reasoning_fallback: bool = False,
        timeout: float = 120,
        max_output_tokens: int = 8192,
        diagnostic_logger: Callable[[str, dict[str, Any]], None] | None = None,
        opener: Callable[..., Any] = urlopen,
    ) -> None:
        dotenv = _dotenv_values()
        self.model = model
        self.reasoning_effort = reasoning_effort
        self.effective_reasoning_effort = reasoning_effort
        self.allow_reasoning_fallback = allow_reasoning_fallback
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY") or dotenv.get("OPENAI_API_KEY")
        self.base_url = (base_url or "https://api.openai.com/v1").rstrip("/")
        self.timeout = timeout
        self.max_output_tokens = max_output_tokens
        self.diagnostic_logger = diagnostic_logger
        self._opener = opener
        if not self.api_key:
            raise ValueError("OPENAI_API_KEY is required (set it in your environment; do not commit it).")
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        if max_output_tokens <= 0:
            raise ValueError("max_output_tokens must be positive")
        self._previous_response_id: str | None = None
        self._pending_call_ids: set[str] = set()
        self._request_number = 0
        self.fallback_reason: str | None = None

    def reset(self) -> None:
        self._previous_response_id = None
        self._pending_call_ids = set()
        self._request_number = 0
        self.effective_reasoning_effort = self.reasoning_effort
        self.fallback_reason = None

    def create_response(
        self,
        input_items: str | list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        previous_response_id: str | None = None,
        instructions: str | None = None,
        text: dict[str, Any] | None = None,
        reasoning_effort: str | None = None,
    ) -> dict[str, Any]:
        """Send one observable, synchronous Responses request and return its raw JSON."""
        effort = self.effective_reasoning_effort if reasoning_effort is None else reasoning_effort
        body: dict[str, Any] = {
            "model": self.model,
            "input": input_items,
            "max_output_tokens": self.max_output_tokens,
        }
        if tools:
            body["tools"] = tools
        if previous_response_id:
            body["previous_response_id"] = previous_response_id
        if instructions:
            body["instructions"] = instructions
        if text:
            body["text"] = text
        if effort:
            body["reasoning"] = {"effort": effort}
        return self._post(body, stage="responses.create")

    def complete(
        self,
        messages: list[dict[str, Any]],
        tools: dict[str, dict[str, Any]],
        schema: dict[str, Any],
    ) -> dict[str, Any]:
        tool_defs = [
            {
                "type": "function",
                "name": name,
                "description": f"Bounded evidence tool: {name}",
                "parameters": parameters,
                "strict": False,
            }
            for name, parameters in tools.items()
        ]
        instructions = _instructions(messages)
        if self._previous_response_id:
            input_items = [
                {
                    "type": "function_call_output",
                    "call_id": message["tool_call_id"],
                    "output": message["content"],
                }
                for message in messages
                if message.get("role") == "tool" and message.get("tool_call_id") in self._pending_call_ids
            ]
            previous_response_id = self._previous_response_id
        else:
            input_items = [
                {"role": message["role"], "content": message["content"]}
                for message in messages
                if message.get("role") in {"system", "developer", "user"}
            ]
            previous_response_id = None

        try:
            payload = self.create_response(
                input_items,
                tools=tool_defs,
                previous_response_id=previous_response_id,
                # Responses instructions are not inherited through previous_response_id.
                instructions=instructions if previous_response_id else None,
                text={"format": {"type": "json_schema", "name": "dataset_spec", "strict": False, "schema": schema}},
            )
        except RuntimeError as exc:
            if (
                self.effective_reasoning_effort
                and self.allow_reasoning_fallback
                and _reasoning_is_unsupported(str(exc))
            ):
                self.fallback_reason = str(exc)
                self.effective_reasoning_effort = None
                self._emit("reasoning fallback", reason=self.fallback_reason)
                return self.complete(messages, tools, schema)
            raise

        status = payload.get("status")
        if status != "completed":
            raise ResponsesStatusError(payload)
        self._previous_response_id = payload.get("id")
        calls = _function_calls(payload)
        self._pending_call_ids = {call["call_id"] for call in calls}
        return {
            "tool_calls": calls,
            "content": _output_text(payload),
            "usage": payload.get("usage"),
            "response_id": payload.get("id"),
            "status": status,
            "error": payload.get("error"),
            "incomplete_details": payload.get("incomplete_details"),
            "output_item_types": _output_item_types(payload),
        }

    def _post(self, body: dict[str, Any], *, stage: str) -> dict[str, Any]:
        self._request_number += 1
        number = self._request_number
        self._emit(
            f"sending request {number}",
            stage=stage,
            model=self.model,
            reasoning_effort=body.get("reasoning", {}).get("effort"),
            timeout=self.timeout,
            previous_response_id=body.get("previous_response_id"),
        )
        request = Request(
            f"{self.base_url}/responses",
            data=json.dumps(body).encode(),
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
        )
        try:
            with self._opener(request, timeout=self.timeout) as response:  # nosec B310 -- configured official endpoint
                payload = json.load(response)
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            self._emit("request exception", stage=stage, exception_type=type(exc).__name__, exception_message=detail)
            raise RuntimeError(f"OpenAI Responses API rejected the request ({exc.code}): {detail}") from exc
        except (TimeoutError, socket.timeout, URLError) as exc:
            self._emit("request exception", stage=stage, exception_type=type(exc).__name__, exception_message=str(exc))
            raise RuntimeError(f"OpenAI Responses API request failed during {stage}: {type(exc).__name__}: {exc}") from exc
        except (json.JSONDecodeError, OSError) as exc:
            self._emit("request exception", stage=stage, exception_type=type(exc).__name__, exception_message=str(exc))
            raise RuntimeError(f"OpenAI Responses API response failed during {stage}: {type(exc).__name__}: {exc}") from exc
        self._emit(
            f"received request {number} response",
            stage=stage,
            response_id=payload.get("id"),
            status=payload.get("status"),
            error=payload.get("error"),
            incomplete_details=payload.get("incomplete_details"),
            output_item_types=_output_item_types(payload),
        )
        return payload

    def _emit(self, event: str, **fields: Any) -> None:
        if self.diagnostic_logger:
            self.diagnostic_logger(event, fields)


def _instructions(messages: list[dict[str, Any]]) -> str | None:
    parts = [str(message["content"]) for message in messages if message.get("role") in {"system", "developer"}]
    return "\n\n".join(parts) or None


def _output_item_types(payload: dict[str, Any]) -> list[str]:
    return [str(item.get("type")) for item in payload.get("output", [])]


def _output_text(payload: dict[str, Any]) -> str | None:
    # output_text is an SDK convenience property; raw HTTP responses keep text in output items.
    if isinstance(payload.get("output_text"), str):
        return payload["output_text"]
    chunks: list[str] = []
    for item in payload.get("output", []):
        if item.get("type") != "message":
            continue
        for content in item.get("content", []):
            if content.get("type") == "output_text" and isinstance(content.get("text"), str):
                chunks.append(content["text"])
    return "".join(chunks) or None


def _function_calls(payload: dict[str, Any]) -> list[dict[str, Any]]:
    calls = []
    for item in payload.get("output", []):
        if item.get("type") != "function_call":
            continue
        try:
            arguments = json.loads(item.get("arguments") or "{}")
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Invalid function-call arguments for {item.get('name')!r}: {exc}") from exc
        calls.append(
            {
                # The continuation protocol requires call_id, not the output item's id.
                "id": item["call_id"],
                "item_id": item.get("id"),
                "call_id": item["call_id"],
                "name": item["name"],
                "arguments": arguments,
            }
        )
    return calls


def _reasoning_is_unsupported(detail: str) -> bool:
    normalized = detail.casefold()
    return "reasoning" in normalized and any(term in normalized for term in ("not supported", "unsupported", "invalid value"))

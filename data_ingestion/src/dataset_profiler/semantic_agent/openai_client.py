"""Minimal OpenAI Chat Completions adapter; no SDK dependency required."""

from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.error import HTTPError
from typing import Any
from urllib.request import Request, urlopen

class OpenAIChatClient:
    provider = "openai"

    def __init__(self, model: str = "gpt-4.1-mini", api_key: str | None = None, base_url: str | None = None, reasoning_effort: str | None = None) -> None:
        self.model = model
        dotenv = _dotenv_values()
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY") or dotenv.get("OPENAI_API_KEY")
        self.base_url = (base_url or os.environ.get("OPENAI_BASE_URL") or dotenv.get("OPENAI_BASE_URL") or "https://api.openai.com/v1").rstrip("/")
        self.reasoning_effort = reasoning_effort
        self.effective_reasoning_effort = reasoning_effort
        if not self.api_key:
            raise ValueError("OPENAI_API_KEY is required (set it in your environment; do not commit it).")

    def complete(self, messages: list[dict[str, Any]], tools: dict[str, dict[str, Any]], schema: dict[str, Any]) -> dict[str, Any]:
        definitions = [{"type": "function", "function": {"name": name, "description": f"Bounded evidence tool: {name}", "parameters": parameters}} for name, parameters in tools.items()]
        body = {"model": self.model, "messages": messages, "tools": definitions, "tool_choice": "auto", "response_format": {"type": "json_schema", "json_schema": {"name": "dataset_spec", "strict": False, "schema": schema}}}
        if self.effective_reasoning_effort:
            body["reasoning_effort"] = self.effective_reasoning_effort
        request = Request(f"{self.base_url}/chat/completions", data=json.dumps(body).encode(), headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"})
        try:
            with urlopen(request, timeout=120) as response:  # nosec B310 -- fixed official endpoint
                payload = json.load(response)
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            if self.effective_reasoning_effort and "reasoning_effort" in detail and "not supported" in detail:
                self.effective_reasoning_effort = "none"
                return self.complete(messages, tools, schema)
            raise RuntimeError(f"OpenAI API rejected the request ({exc.code}): {detail}") from exc
        message = payload["choices"][0]["message"]
        calls = [{"id": item["id"], "name": item["function"]["name"], "arguments": json.loads(item["function"]["arguments"])} for item in message.get("tool_calls", [])]
        return {"tool_calls": calls, "content": message.get("content"), "usage": payload.get("usage")}

def _dotenv_values() -> dict[str, str]:
    """Read the supported settings from a local, gitignored .env file."""
    dotenv = Path(".env")
    if not dotenv.is_file():
        return {}
    values = {}
    for line in dotenv.read_text(encoding="utf-8").splitlines():
        key, separator, value = line.partition("=")
        if separator and key.strip() in {"OPENAI_API_KEY", "OPENAI_BASE_URL"}:
            values[key.strip()] = value.strip().strip("\"'")
    return values

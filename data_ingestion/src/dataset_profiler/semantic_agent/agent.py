"""A deliberately small tool-calling agent over EvidenceSession only."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import json
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4

from ..evidence import Evidence, EvidenceError, EvidenceSession
from ..models import DatasetProfile
from ..semantic_spec import DatasetSpec

SYSTEM_PROMPT = """You construct one DatasetSpec v0.1 from bounded dataset evidence. Investigate using the provided tools before making semantic assertions. You have no raw arrays, filesystem, web access, connector code, reference specifications, or dataset-specific prior facts. Classify semantic and unit claims as observed, documented, inferred, or unresolved. Never fabricate evidence IDs: use only IDs returned by tools. Documented claims require documentation evidence; inferred claims must be conservative with evidence and confidence. Prefer unresolved over guessing.

Documentation discovery is iterative and bounded. Begin from dataset-level context: identity, subset names, summary, metadata, and documentation source names. Do not expect author documentation to use DatasetSpec terms, raw variable names, or ontology labels. For an important unknown, form several short, concrete lexical hypotheses using vocabulary from that context. Each documentation search must use one short concept or phrase, never a comma-separated list of alternatives. If a specific search has no useful result, broaden it: remove schema wording, use a shorter concept, or try an author-facing/domain term. Do not immediately conclude documentation is absent. When a broad search returns an excerpt, treat its wording as new vocabulary and, when useful, refine with a later search. Prefer broad → informative → refined queries; do not exhaust the budget with near-duplicates. Variable names are clues, not the only search terms. Search effort should focus on fields that materially affect the specification.

Return only a DatasetSpec wire object, never a dataset summary. Its exact top-level keys are schema_version (the literal string \"0.1\"), identity, record_discovery, source_variables, signals, time_axes, events, provenance, tasks, and record_defaults. Use this exact wire shape: identity={dataset_id,source_subsets,compatible_profile_ids}; record_discovery={record_unit,boundary,included_run_ids}; source_variables is an array of {name,role} where role is signal/time/event/metadata; each signal={source_variable,semantic_type,channels:{count,source_indices,target_names},dtype,unit:{name,symbol,resolution:{status,confidence,evidence}},sampling:{rate_hz,time_axis},semantics:{status,confidence,evidence}}; each time axis={name,source_variable,kind,unit,monotonic,embedded_signal_row}; each event={source_variable,semantic_type,source_index_base,index_conversion,time_axis,timestamp_rule,mapping_type,preserve_fields,semantics:{status,confidence,evidence}}; provenance is an array of {field,scope,required}; tasks is an array of {name,task_type,target}; record_defaults={subject_ids,start_time}. Never use null for an array: channels.source_indices and target_names must each be arrays of length count (use indices 0 through count-1 and neutral names such as channel_0); subject_ids and preserve_fields are arrays. A claim status is observed/documented/inferred/unresolved; confidence is low/medium/high or null (unresolved must be null and have empty evidence). Time kind is regular/irregular; embedded_signal_row is an integer or null. Event values must be source_index_base zero_based/matlab_one_based, index_conversion identity/subtract_one, timestamp_rule lookup_time_axis, mapping_type timef_point_annotation. Provenance scope is record/series. Use [] for no items and null only for nullable scalar fields."""

TOOLS = {
    "dataset_summary": {"type": "object", "properties": {}, "additionalProperties": False},
    "variable_schema": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"], "additionalProperties": False},
    "signal_statistics": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"], "additionalProperties": False},
    "metadata_summary": {"type": "object", "properties": {}, "additionalProperties": False},
    "documentation_sources": {"type": "object", "properties": {}, "additionalProperties": False},
    "documentation_search": {"type": "object", "properties": {"query": {"type": "string"}, "max_results": {"type": "integer", "minimum": 1, "maximum": 5}}, "required": ["query"], "additionalProperties": False},
}

class SemanticAgentError(RuntimeError):
    def __init__(self, message: str, trace: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.trace = trace

class ChatClient(Protocol):
    provider: str
    model: str
    def complete(self, messages: list[dict[str, Any]], tools: dict[str, dict[str, Any]], schema: dict[str, Any]) -> dict[str, Any]: ...

@dataclass(frozen=True)
class SemanticAgentRun:
    spec: DatasetSpec
    trace: dict[str, Any]
    def write_trace(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps(self.trace, indent=2, allow_nan=False) + "\n", encoding="utf-8")

class SemanticAgent:
    def __init__(self, client: ChatClient, *, max_turns: int = 16) -> None:
        self.client, self.max_turns = client, max_turns

    def run(self, profile: DatasetProfile, session: EvidenceSession, *, user_context: str | None = None) -> SemanticAgentRun:
        messages: list[dict[str, Any]] = [{"role": "system", "content": SYSTEM_PROMPT}]
        if user_context:
            messages.append({"role": "user", "content": user_context})
        trace: dict[str, Any] = {"run_id": str(uuid4()), "started_at": datetime.now(UTC).isoformat(), "dataset_id": profile.dataset_id, "provider": self.client.provider, "model": self.client.model, "base_url": getattr(self.client, "base_url", None), "reasoning_effort": getattr(self.client, "reasoning_effort", None), "tool_calls": [], "raw_structured_output": None, "parsed_candidate": None, "failure": None}
        for _ in range(self.max_turns):
            response = self.client.complete(messages, TOOLS, dataset_spec_json_schema())
            if response.get("usage"): trace.setdefault("usage", []).append(response["usage"])
            calls = response.get("tool_calls", [])
            if calls:
                messages.append({"role": "assistant", "tool_calls": [_wire_tool_call(call) for call in calls]})
                for call in calls:
                    result = _dispatch(session, call["name"], call.get("arguments", {}))
                    rendered = _result_dict(result)
                    trace["tool_calls"].append({"name": call["name"], "arguments": call.get("arguments", {}), "returned_evidence_ids": _evidence_ids(result), "result": rendered})
                    messages.append({"role": "tool", "tool_call_id": call["id"], "content": json.dumps(rendered)})
                continue
            raw = response.get("content")
            trace["raw_structured_output"] = raw
            try: spec = DatasetSpec.from_dict(_normalize_wire(json.loads(raw if isinstance(raw, str) else json.dumps(raw))))
            except (TypeError, ValueError, KeyError) as exc:
                trace["failure"] = {"kind": "parse_or_schema", "message": str(exc)}
                raise SemanticAgentError(f"Model returned invalid DatasetSpec: {exc}", trace) from exc
            trace["parsed_candidate"] = spec.to_dict()
            trace["effective_reasoning_effort"] = getattr(self.client, "effective_reasoning_effort", None)
            return SemanticAgentRun(spec, trace)
        trace["failure"] = {"kind": "turn_limit", "message": f"Exceeded {self.max_turns} turns"}
        raise SemanticAgentError(trace["failure"]["message"], trace)

def generate_dataset_spec(profile: DatasetProfile, evidence_session: EvidenceSession, client: ChatClient) -> SemanticAgentRun:
    """Generate a candidate without accepting any reference-spec or connector input."""
    return SemanticAgent(client).run(profile, evidence_session)

def _dispatch(session: EvidenceSession, name: str, arguments: dict[str, Any]) -> Any:
    if name not in TOOLS: return {"ok": False, "error": f"Unknown bounded tool {name!r}"}
    try: return getattr(session, name)(**arguments)
    except (TypeError, ValueError) as exc: return {"ok": False, "error": str(exc)}

def _result_dict(result: Any) -> Any:
    if isinstance(result, (Evidence, EvidenceError)): return result.to_dict()
    if isinstance(result, list): return [_result_dict(item) for item in result]
    return result

def _evidence_ids(result: Any) -> list[str]:
    if isinstance(result, Evidence): return [result.id]
    if isinstance(result, list): return [item.id for item in result if isinstance(item, Evidence)]
    return []

def _wire_tool_call(call: dict[str, Any]) -> dict[str, Any]:
    """Convert the provider-neutral call to the OpenAI Chat message wire shape."""
    return {"id": call["id"], "type": "function", "function": {"name": call["name"], "arguments": json.dumps(call.get("arguments", {}))}}

def dataset_spec_json_schema() -> dict[str, Any]:
    keys = ["schema_version", "identity", "record_discovery", "source_variables", "signals", "time_axes", "events", "provenance", "tasks", "record_defaults"]
    properties = {key: {} for key in keys}
    properties["schema_version"] = {"const": "0.1"}
    return {"type": "object", "properties": properties, "required": keys, "additionalProperties": False}

def _normalize_wire(value: dict[str, Any]) -> dict[str, Any]:
    """Generic boundary normalization: unresolved scalar values are represented by null."""
    for signal in value.get("signals", []):
        unit = signal.get("unit", {})
        resolution = unit.get("resolution", {})
        if resolution.get("status") == "unresolved":
            unit["name"] = None
            unit["symbol"] = None
    return value

# ADR 0001: Evidence-complete dataset scout

- Status: accepted
- Date: 2026-09-12

## Context

The hackathon needs a demonstrable agentic data-sourcing submodule for robot observability. Search
results alone do not establish licence, usable files, schema, labels or acquisition feasibility,
and publisher sources may contradict a linked code repository.

## Decision

Use LangGraph 1.2.11 `StateGraph` with typed, JSON-serializable state and a SQLite checkpointer. The
API run UUID is the graph thread ID. The graph performs bounded requirements, hypothesis,
discovery, canonicalization, native verification, gap search and deterministic scoring stages. It
uses `interrupt()` for approval and `Command(resume=...)` for resumption.

Tavily, GitHub, Zenodo and Hugging Face remain ordinary adapters. Search discovers candidates;
only native adapters create verified evidence. Optional OpenAI structured extraction can enrich
search vocabulary, but code-owned mandatory gates and deterministic scoring cannot be weakened by
model output. Evidence confidence is reported separately from recommendation confidence.

Persist `run.json`, `evidence.jsonl`, `report.md`, graph checkpoints and the approved
`manifest.json`. End the module boundary at the manifest.

## Consequences

Runs are replayable, inspectable and safe to resume after a process restart. Unresolved mandatory
gaps cause abstention. Conflicting claims remain visible with source precedence instead of being
silently overwritten. SQLite and synchronous adapters are appropriate for the demo but should be
replaced with production persistence and distributed execution before horizontal scaling.

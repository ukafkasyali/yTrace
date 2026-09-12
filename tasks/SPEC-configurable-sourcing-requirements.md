# Spec: Configurable sourcing requirements

## Objective

Let a dataset researcher inspect and edit the research contract before spending search credits.
Generated requirements can be mandatory, preferred, or disabled. The user can add natural-language
mandatory requirements, which are verified against native-source text with grounded evidence.

The feature preserves non-disableable system integrity checks: the source must be a real dataset,
have canonical provenance, and directly expose usable data files.

## Tech Stack

- Python, FastAPI, Pydantic, LangGraph and the existing direct OpenAI-compatible client
- React, TypeScript, Vitest and the existing frontend service layer
- No new dependencies or persistence schema migrations

## Commands

```bash
cd data_sourcing && .venv/bin/pytest -q
cd data_sourcing && .venv/bin/ruff check src tests
cd frontend && npm test -- --run
cd frontend && npm run build
```

## Project Structure

- `data_sourcing/src/data_sourcing/models.py`: additive preview and selected-requirement contracts
- `data_sourcing/src/data_sourcing/planning.py`: generated and custom requirement drafts
- `data_sourcing/src/data_sourcing/relevance.py`: grounded custom semantic evidence
- `data_sourcing/src/data_sourcing/graph.py`: preserve and enforce the confirmed contract
- `data_sourcing/src/data_sourcing/api.py`: requirements-preview resource
- `frontend/src/sourcing/`: contract editor and run creation flow
- `docs/DATA_SOURCING_API.md`: public request/response semantics

## Code Style

Use typed, additive wire models and fail-closed verification:

```python
class RequirementDefinition(WireModel):
    priority: RequirementPriority
    category: RequirementCategory
    expected_values: list[str]
    is_system_required: bool = False
```

Existing clients may omit `requirements`; an explicit empty list means every configurable
requirement is disabled.

## Testing Strategy

- Contract tests for preview validation, compatibility, and idempotent run creation.
- Planner tests for omitted empty labels and stable custom requirement IDs.
- Scoring tests proving preferred/disabled requirements cannot reject a candidate.
- Semantic-evidence tests proving custom requirements need a validated native quote.
- Component and service tests for accessible priority controls and the preview-before-run flow.
- Full backend, frontend, lint, and production build verification.

## Boundaries

- Always: validate confirmed requirements server-side; preserve fixed integrity gates; attach custom
  evidence to the candidate and exact native source; fail closed on model errors.
- Ask first: add dependencies, add new source hosts, or allow arbitrary executable predicates.
- Never: let user input disable dataset identity/provenance/file integrity; treat an LLM assertion
  without a source quote as evidence; silently enforce an uninterpretable requirement.

## Success Criteria

1. The user previews generated requirements before starting a run.
2. Every configurable requirement can be set to `MUST`, `SHOULD`, or disabled.
3. Custom natural-language requirements default to `MUST` and appear in the preview.
4. Custom requirements pass only with a verbatim quote from a fetched native source.
5. `SHOULD` requirements affect scoring/evidence but cannot fail a hard gate.
6. An explicit confirmed requirement set survives background execution and checkpoints.
7. Existing `{ "brief": ... }` clients retain automatic planning behavior.
8. A brief with no extracted task labels no longer creates an impossible empty-label requirement.

## Open Questions

- A future iteration may add typed numeric custom predicates beyond sampling rate. This increment
  uses grounded semantic evaluation for arbitrary natural-language requirements.

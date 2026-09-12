# Spec: Explainable Dataset Suitability Levels

## Objective

Replace reviewer-facing 1–100 scores with `LOW`, `MEDIUM`, and `HIGH` suitability. Every assessed dataset must explain its level with evidence-linked strengths, limitations, or blockers. Evidence confidence remains a separate concept.

## Tech Stack and Commands

- Backend: Python, Pydantic, LangGraph. Test with `cd data_sourcing && uv run pytest`; lint with `uv run ruff check .`.
- Frontend: React and TypeScript. Test with `cd frontend && npm test`; build with `npm run build`.

## Project Structure

- `data_sourcing/src/data_sourcing/models.py`: additive API types.
- `data_sourcing/src/data_sourcing/scoring.py`: deterministic level and factor derivation.
- `frontend/src/services/sourcing.ts`: validated client contract and legacy fallback.
- `frontend/src/sourcing/`: reviewer presentation.
- `docs/DATA_SOURCING_API.md`: public contract.

## Code Style

Use explicit typed factors rather than prose assembled in UI components:

```python
SuitabilityFactor(kind="BLOCKER", label="Explicit licence", explanation="Mandatory requirement is unsupported by native evidence")
```

## Testing Strategy

- Unit-test high, medium, and low classification and their explanations.
- Contract-test additive API fields and compatibility with older saved runs.
- Render-test that no reviewer-facing `/100` score remains and explanations are visible.
- Run the full backend/frontend suites and production build.

## Boundaries

- Always: failed mandatory gates force `LOW`; explanations come from deterministic gates and verified evidence.
- Ask first: changing hard-gate semantics, score weights, or recommendation eligibility.
- Never: let the LLM invent a suitability level or explanation; conflate suitability with evidence confidence.

## Success Criteria

- Reviewers see only Low, Medium, or High suitability.
- Each candidate displays why it received that level.
- Numeric scores remain internal/backward-compatible and are not shown in reports or reviewer UI.
- Existing ranking, approval, abstention, and evidence rules remain unchanged.

## Open Questions

None. The compatibility field can be removed in a future versioned API migration.

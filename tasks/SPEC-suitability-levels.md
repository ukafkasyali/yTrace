# Spec: Explainable Dataset Suitability Levels

## Objective

Replace 1–100 scoring entirely with `LOW`, `MEDIUM`, and `HIGH` suitability. Every assessed dataset must explain its level with evidence-linked strengths, limitations, or blockers. Evidence confidence remains a separate concept.

## Tech Stack and Commands

- Backend: Python, Pydantic, LangGraph. Test with `cd data_sourcing && uv run pytest`; lint with `uv run ruff check .`.
- Frontend: React and TypeScript. Test with `cd frontend && npm test`; build with `npm run build`.

## Project Structure

- `data_sourcing/src/data_sourcing/models.py`: categorical API types and legacy read adapter.
- `data_sourcing/src/data_sourcing/scoring.py`: deterministic categorical level, factor, confidence and ranking rules.
- `data_sourcing/src/data_sourcing/storage.py`: persisted run boundary.
- `frontend/src/services/sourcing.ts`: validated categorical client contract.
- `frontend/src/sourcing/`: reviewer presentation.
- `docs/DATA_SOURCING_API.md`: public contract.

## Code Style

Use explicit typed factors rather than prose assembled in UI components:

```python
SuitabilityFactor(kind="BLOCKER", label="Explicit licence", explanation="Mandatory requirement is unsupported by native evidence")
```

## Testing Strategy

- Unit-test high, medium, and low classification, ranking and recommendation confidence.
- Contract-test removal of numeric fields and compatibility with older saved runs.
- Render-test that no reviewer-facing `/100` score remains and explanations are visible.
- Run the full backend/frontend suites and production build.

## Boundaries

- Always: failed mandatory gates force `LOW`; explanations come from deterministic gates and verified evidence.
- Ask first: changing hard-gate semantics or recommendation eligibility.
- Never: let the LLM invent a suitability level or explanation; conflate suitability with evidence confidence.

## Success Criteria

- Reviewers see only Low, Medium, or High suitability.
- Each candidate displays why it received that level.
- `LOW`: any mandatory requirement or integrity gate fails.
- `MEDIUM`: mandatory requirements pass, but a preferred requirement or evidence limitation remains.
- `HIGH`: mandatory and preferred requirements pass with high, conflict-free evidence confidence.
- Ranking within a level uses preferred-requirement coverage, evidence confidence, native-source authority and a stable candidate ID.
- Numeric score fields are absent from new API responses, reports and reviewer UI.
- Existing saved numeric assessments are converted when read; new writes contain only categorical assessments.
- Approval remains available for medium and high candidates; only high candidates become the agent recommendation.

## Open Questions

None.

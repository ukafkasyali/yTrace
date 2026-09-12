# Implementation Plan: Configurable sourcing requirements

## Overview

Add a preflight research-contract preview, preserve its confirmed requirement definitions through
the durable run, verify custom natural-language requirements from native evidence, and expose an
accessible Must/Preferred/Disabled editor in the existing dataset scout.

## Architecture Decisions

- Add `POST /api/sourcing-requirement-previews` as a stateless typed preview resource.
- Add optional `requirements` to run creation; absence keeps legacy automatic planning, while an
  explicit list is authoritative for configurable requirements.
- Mark system requirements in the contract and reinsert them server-side if a client omits them.
- Represent arbitrary custom requirements as `OTHER` and verify them with a validated native quote.
- Make hard gates conditional on mandatory categories; preferred requirements never reject.

## Task List

### Phase 1: Preview contract

- [x] Add requirement-definition and preview API models.
- [x] Generate stable previews, omit empty task labels, and expose the preview endpoint.
- [x] Add backend contract and planner regression tests.

### Checkpoint: Preview

- [x] Focused API and planner tests pass.

### Phase 2: Confirmed execution

- [x] Preserve explicit requirement selection across persistence and background execution.
- [x] Ground custom requirements in native source quotes.
- [x] Make hard gates respect Must versus Preferred versus Disabled.

### Checkpoint: Execution

- [x] Graph, relevance, scoring, and lifecycle tests pass.

### Phase 3: Frontend workflow

- [x] Add preview service contracts and response validation.
- [x] Add custom requirement entry and accessible priority controls.
- [x] Require a current preview before starting and submit the confirmed contract.

### Checkpoint: Complete

- [ ] Frontend tests and production build pass.
- [ ] Full backend tests and lint pass.
- [ ] Documentation, security review, and compatibility review are complete.

## Risks and Mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Client removes integrity requirements | High | Reinsert fixed requirements server-side and retain fixed gates |
| LLM claims unsupported custom evidence | High | Require exact source URL, document index, and verbatim quote |
| Preview becomes stale after brief edits | Medium | Invalidate preview on every brief/custom change |
| Old clients break | High | Optional additive request field preserves automatic planning |
| Preferred requirement rejects data | High | Hard gates use mandatory requirements only |

## Open Questions

- None blocking under the surfaced assumptions.

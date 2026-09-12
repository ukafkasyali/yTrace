# Implementation Plan: Dataset source integrity

## Overview

Replace URL-component evidence aggregation with bounded lead exploration and explicit artifact
promotion. Keep the wire contract additive and make the review UI distinguish usable datasets from
discovery pages that were inspected and rejected.

## Architecture Decisions

- Preserve every native URL as a separate identity; links form provenance edges, not identity.
- Use one semantic identity judgment over the primary native record, validated by a verbatim quote.
- Require direct, non-empty data/archive files before semantic support can promote a source.
- Keep deterministic gates authoritative; the LLM may support or abstain but cannot override files.
- Use native adapters first. Do not add Playwright/MCP dependencies in this increment.

## Task List

### Phase 1: Source exploration

- [x] Add source-role and traversal metadata to the additive API contract.
- [x] Canonicalize native URLs independently and expand related URLs as bounded child leads.
- [x] Add regression tests for identity preservation and two-hop/count limits.

### Checkpoint: Exploration

- [x] Focused adapter and graph tests pass.

### Phase 2: Artifact verification

- [x] Add source-local dataset identity judgment and evidence.
- [x] Add the deterministic `dataset_identity` hard gate and artifact-first ranking.
- [x] Prove linked evidence cannot promote a guide or code-only repository.

### Checkpoint: Verification

- [x] Backend test suite and lint pass.

### Phase 3: Review presentation

- [x] Rank only promoted dataset artifacts.
- [x] Show inspected non-dataset leads separately with exclusion reasons.
- [x] Update API documentation and regression fixtures.

### Checkpoint: Complete

- [x] Frontend tests and production build pass.
- [x] Cached end-to-end sourcing flow reaches approval and manifest generation.
- [x] Diff review finds no evidence-boundary, security, or compatibility regressions.

## Risks and Mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| False promotion from persuasive README text | High | Require source-local files and validated quote |
| Valid dataset hidden behind a guide | Medium | Follow related native URLs as separate bounded leads |
| Traversal exhausts demo budget | High | Two-hop, sixteen-lead, eight-artifact and time caps |
| Persisted runs lack new fields | Medium | Add defaults and keep frontend compatibility fallbacks |
| Cached demo loses combined coverage | Medium | Preserve the dataset repository as its own verified source |

## Open Questions

- Browser fallback runtime is intentionally deferred pending explicit dependency/integration approval.

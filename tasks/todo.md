# Dataset source integrity tasks

## Task 1: Preserve source identity during exploration

- [x] Add source role, parent and depth metadata with backward-compatible defaults.
- [x] Stop grouping linked URLs into one candidate; create distinct child leads.
- [x] Verify: focused canonicalization and graph traversal tests pass.

## Task 2: Promote only proven dataset artifacts

- [x] Evaluate dataset identity from the primary native record only.
- [x] Require direct data/archive files and validated semantic evidence.
- [x] Add a hard gate and regression tests for guides, lists and code-only repositories.
- [x] Verify: complete backend test suite and lint pass.

## Checkpoint: Backend integrity

- [x] Existing cached run still reaches approval.
- [x] No parent source inherits child identity evidence.

## Task 3: Separate datasets from discovery leads in review

- [x] Rank promoted datasets only.
- [x] Render excluded leads and reasons as a separate audit section.
- [x] Verify: frontend tests and production build pass.

## Task 4: Document and review

- [x] Update the sourcing API/README behavior.
- [x] Run security, compatibility and simplicity review.
- [x] Commit the completed slices and update the existing PR.

## Checkpoint: Complete

- [x] All backend and frontend checks pass.
- [x] Original reported failure has explicit regression coverage.

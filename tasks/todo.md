# Dataset source integrity tasks

## Task 1: Preserve source identity during exploration

- [x] Add source role, parent and depth metadata with backward-compatible defaults.
- [x] Stop grouping linked URLs into one candidate; create distinct child leads.
- [x] Verify: focused canonicalization and graph traversal tests pass.

## Task 2: Promote only proven dataset artifacts

- [ ] Evaluate dataset identity from the primary native record only.
- [ ] Require direct data/archive files and validated semantic evidence.
- [ ] Add a hard gate and regression tests for guides, lists and code-only repositories.
- [ ] Verify: complete backend test suite and lint pass.

## Checkpoint: Backend integrity

- [ ] Existing cached run still reaches approval.
- [ ] No parent source inherits child identity evidence.

## Task 3: Separate datasets from discovery leads in review

- [ ] Rank promoted datasets only.
- [ ] Render excluded leads and reasons as a separate audit section.
- [ ] Verify: frontend tests and production build pass.

## Task 4: Document and review

- [ ] Update the sourcing API/README behavior.
- [ ] Run security, compatibility and simplicity review.
- [ ] Commit the completed slices and update the existing PR.

## Checkpoint: Complete

- [ ] All backend and frontend checks pass.
- [ ] Original reported failure has explicit regression coverage.

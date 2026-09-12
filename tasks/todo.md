# Approved source library and generic ingestion tasks

## Task 1: Freeze shared contracts and licence handling

**Description:** Jointly define manifest v1.1, approved-source summary/detail, provider asset,
ResourceProfile, MappingSpec, job, and receipt contracts. Resolve dataset-file versus processing-code
licence semantics before any source is marked ready.

**Acceptance criteria:**

- [ ] Versioned JSON schemas and golden fixtures cover all boundary objects.
- [x] Old sourcing manifests remain readable but report incomplete acquisition metadata.
- [x] Dataset and code licences are separate; unresolved mismatch blocks READY.
  - [x] Producer contract separates `datasetLicenseId` from `codeLicenseId`.
  - [x] Ingestion readiness blocks unresolved dataset-file rights.

**Verification:**

- [ ] Sourcing, ingestion, and frontend owners review the relevant contract shapes.
- [ ] Fixtures contain no credentials, executable instructions, or local absolute paths.

**Dependencies:** None

**Files likely touched:** docs/contracts/, docs/DATA_SOURCING_API.md,
docs/FRONTEND_INTEGRATION.md, HUMAN_MAPPING_DECISIONS.md

**Estimated scope:** Medium (3-5 files)

## Task 2: Emit provider assets in approved manifests

**Description:** Extend native verification to retain credential-free, revision-pinned asset
metadata from GitHub, Zenodo, and Hugging Face. Preserve name, role, size, provider locator, validated
download URL, and available source checksum.

**Acceptance criteria:**

- [x] Each acquisition-ready approved dataset artifact contains at least one exact non-empty data asset.
- [x] GitHub assets pin a commit, Zenodo assets pin a record revision, and Hugging Face assets pin a
  dataset revision.
- [x] Asset evidence comes from the candidate's native source, never a discovery lead.

**Verification:**

- [x] Provider mock tests cover metadata, missing checksum, mutable/unknown revision, and bad links.
- [x] Run the complete sourcing tests and lint.

**Dependencies:** Task 1

**Files likely touched:** sourcing models, adapters/native.py, graph manifest generation, tests,
API documentation

**Estimated scope:** Medium (3-5 implementation files plus tests/docs)

## Task 3: Persist the approved-source catalog

**Description:** Add a durable catalog keyed by source kind, canonical URL, and source revision.
Approval appends an immutable event and idempotently upserts one visible source. Startup
reconciliation repairs catalog entries missing after an interrupted file/database write.

**Acceptance criteria:**

- [x] Same source/revision has one catalog ID and multiple immutable approval events.
- [x] New revisions receive new catalog IDs without overwriting old approvals.
- [x] Approval creates no download or ingestion side effect.

**Verification:**

- [x] Tests cover first approval, repeated approval, new revision, restart, and crash reconciliation.
- [x] Existing sourcing-run persistence and repeated-approval behavior remain compatible.

**Dependencies:** Tasks 1-2

**Files likely touched:** sourcing storage/service/catalog model and persistence tests

**Estimated scope:** Medium (3-5 files)

## Task 4: Expose approved-source APIs

**Description:** Add paginated list, detail, and exact-manifest endpoints. Filtering supports source
kind and a bounded text query. Ordering is stable and newest approval appears first.

**Acceptance criteria:**

- [x] GET /approved-sources returns data plus pagination metadata.
- [x] Detail returns approval history; manifest returns the exact approved handoff.
- [x] Shared error envelope, validation, and tenant scoping rules match existing APIs.

**Verification:**

- [x] API tests cover pagination, filters, missing IDs, duplicate approvals, and legacy manifests.
- [ ] API docs and typed frontend contracts are updated additively.

**Dependencies:** Task 3

**Files likely touched:** sourcing API/models/service, API tests and docs

**Estimated scope:** Medium (3-5 files)

## Checkpoint: Persistent approvals

- [x] Approved sources survive restart and appear without starting ingestion.
- [x] Duplicate and revision behavior is demonstrated.
- [x] Full sourcing tests and lint pass.

## Task 5: Create one ingestion job per approved source

**Description:** Add an ingestion-owned manifest parser and internal approved-source resolver.
POST /ingestions accepts approvedSourceId plus asset IDs, resolves the manifest, and atomically
gets or creates the source's single persisted ingestion job.

**Acceptance criteria:**

- [x] Missing, stale, incomplete, unauthorized, or browser-substituted manifest data fails before
  acquisition.
- [x] A unique approvedSourceId constraint prevents a second logical ingestion even with another
  idempotency key.
- [x] Repeated matching requests return the existing job; conflicting asset selection returns 409.

**Verification:**

- [x] Parser passes shared golden fixtures without importing data_sourcing.
- [x] API tests cover success, duplicate in-flight request, payload conflict, and resolver failure.

**Dependencies:** Tasks 1 and 4

**Files likely touched:** new ingestion handoff/job/API modules and tests

**Estimated scope:** Medium; split persistence and API into separate commits

## Task 6: Add secure acquisition core and Zenodo

**Description:** Stream manifest-listed Zenodo files into quarantine, enforce size/time limits,
verify provider metadata/checksums, compute SHA-256, and atomically promote verified content.

**Acceptance criteria:**

- [x] Only exact assets on the approved record/revision can be fetched.
- [x] Redirect, DNS, timeout, truncation, size, and checksum failures fail closed.
- [x] Partial files never appear in the verified cache.

**Progress:**

- [x] Zenodo streams exact manifest assets into staging, verifies size/provider checksum, computes
  SHA-256, and atomically promotes content.
- [x] Redirect, public-address, cross-host, truncation, size, and checksum guards are implemented.
- [x] Persist source-asset-to-content receipts and connect acquisition to the queued job worker.

**Verification:**

- [x] Mock tests cover every failure class and deterministic success.
- [x] Mock retry proves a previously verified asset is reused without another network request.
- [ ] A previously downloaded KUKA archive can be verified without another network request.

**Dependencies:** Task 5

**Files likely touched:** acquisition core, Zenodo adapter, settings/dependencies, tests

**Estimated scope:** Medium (3-5 files)

## Task 7: Add pinned GitHub and Hugging Face acquisition

**Description:** Add file-only adapters for GitHub repository commits and Hugging Face dataset
revisions/LFS assets. Never clone, import, execute, or enable remote code.

**Acceptance criteria:**

- [x] GitHub retrieval verifies repository, commit, blob/file identity, and size.
- [x] Hugging Face retrieval verifies dataset ID, revision, sibling/LFS identity, and size.
- [x] Executable files may be inventoried as unsupported but are never run.

**Verification:**

- [x] Mock tests cover revision drift, LFS pointers, missing assets, redirects, and checksums.
- [x] One small mocked fixture per provider reaches the verified content cache.

**Dependencies:** Task 6

**Files likely touched:** GitHub/Hugging Face adapters and focused tests

**Estimated scope:** Medium (3-5 files)

## Task 8: Store content and extract archives safely

**Description:** Add content-addressed storage and bounded ZIP/TAR/TAR.GZ/TAR.ZST extraction.
Extraction uses atomic staging and rejects paths, links, devices, or expansion beyond configured
limits.

**Acceptance criteria:**

- [x] Identical content is reused across jobs without conflating source receipts.
- [x] Traversal, absolute paths, symlinks, hard links, devices, bombs, and truncation are rejected.
- [x] Failed jobs leave no ready directory or ambiguous receipt.

**Verification:**

- [x] Synthetic archive tests cover all guards and valid nested data.
- [x] Cache retention and cleanup are documented before deployment.

**Dependencies:** Task 6

**Files likely touched:** content store, extraction module, tests, ingestion documentation

**Estimated scope:** Medium (3-5 files)

## Task 9: Inventory resources through format adapters

**Description:** Define FormatAdapter and bounded ResourceProfile contracts. Probe extension plus
magic/schema, inventory files, and return a stable format or UNSUPPORTED_FORMAT without loading whole
datasets into memory.

**Acceptance criteria:**

- [ ] Every verified provider source reaches a visible inventory result.
- [ ] Adapter selection is deterministic and extensions alone are insufficient.
- [ ] Per-file, row, column, array, nesting, memory, and time limits are enforced.

**Verification:**

- [ ] Conformance tests apply to every format adapter.
- [ ] Mismatched extension/content, nested archives, empty files, and unknown formats are covered.

**Dependencies:** Task 8

**Files likely touched:** format base/registry, resource models, inventory service, tests

**Estimated scope:** Medium (3-5 files)

## Checkpoint: Universal provider intake

- [ ] GitHub, Zenodo, and Hugging Face fixtures acquire and inventory safely.
- [ ] Unsupported formats have precise visible outcomes.
- [ ] No downloaded code executes.

## Task 10: Support CSV/TSV and Parquet

**Description:** Add bounded tabular adapters. Inspect schema without full reads and support confirmed
wide time-plus-channel layouts and long record/time/channel/value layouts.

**Acceptance criteria:**

- [ ] CSV/TSV dialect, header, types, and malformed-row limits are deterministic.
- [ ] Parquet uses schema/row groups and projected reads.
- [ ] Values, missingness, record keys, and source row provenance survive mapping.

**Verification:**

- [ ] Wide, long, multi-record, gapped, malformed, and oversized fixtures are tested.
- [ ] Adapter conformance and exact round-trip value tests pass.

**Dependencies:** Task 9

**Files likely touched:** tabular adapters and focused fixtures/tests

**Estimated scope:** Medium (3-5 files)

## Task 11: Support NPY/NPZ, MAT, and HDF5

**Description:** Add numeric array/container adapters using NumPy, the existing MATLAB loader, and
bounded HDF5 traversal. Disable object/pickled arrays and executable or opaque payloads.

**Acceptance criteria:**

- [ ] NPY/NPZ exposes safe numeric shapes/dtypes and named arrays without pickle.
- [ ] Existing MAT formats retain current behavior; MAT v7.3 uses HDF5.
- [ ] HDF5 traversal is bounded and numeric datasets are selected only through mapping.

**Verification:**

- [ ] Named time/channel arrays, transposed arrays, nested groups, object arrays, and limit failures
  are tested.
- [ ] Existing KUKA parser/profiler tests remain unchanged and pass.

**Dependencies:** Task 9

**Files likely touched:** array/HDF5 adapters and focused fixtures/tests

**Estimated scope:** Medium (3-5 files)

## Task 12: Validate and confirm MappingSpec

**Description:** Produce deterministic mapping candidates for recognized structural layouts. When
record boundary, time, channel, unit, or annotation semantics are ambiguous, persist NEEDS_INPUT and
accept an idempotent, version-checked mapping decision.

**Acceptance criteria:**

- [ ] Mapping includes explicit layout, record, time, channels, units, annotations, and version.
- [ ] Unknown units or competing mappings never auto-resolve.
- [ ] Stale or source-incompatible mapping decisions return a structured conflict.

**Verification:**

- [ ] Tests cover unambiguous proposal, every ambiguity class, valid resume, and stale resume.
- [ ] Existing bounded semantic-agent evidence may inform a proposal but cannot bypass validation or
  user confirmation.

**Dependencies:** Tasks 10-11

**Files likely touched:** mapping models/service, job resume endpoint, tests

**Estimated scope:** Medium (3-5 files)

## Task 13: Build generic TimeF and receipts

**Description:** Convert validated mappings through one generic TimeF builder and emit a public
receipt linking source approval, assets, resource profiles, mapping, output version, and validation.

**Acceptance criteria:**

- [ ] Wide, long, and named-array fixtures preserve exact values, axes, units, channels, and records.
- [ ] Sourced claims and ingestion observations remain separate in receipts.
- [ ] READY requires deterministic TimeF read-back validation.

**Verification:**

- [ ] One fixture per supported format family builds, reloads, and matches its source.
- [ ] Receipt hashes reproduce; attempts to replace a ready job's input or mapping fail.

**Dependencies:** Task 12

**Files likely touched:** generic TimeF builder, receipt/validation modules, tests

**Estimated scope:** Medium (3-5 files)

## Task 14: Preserve specialized KUKA dispatch

**Description:** Map the two exact KUKA Zenodo source identities to the current specialized Part I
and Part II connectors before generic fallback. Resolve the dataset licence and preserve every
existing annotation and mapping decision.

**Acceptance criteria:**

- [ ] Part I and Part II select the correct connector and never merge event semantics.
- [ ] Seven torque plus seven position series, 1 kHz time, and publisher annotations persist.
- [ ] Generic adapters cannot silently replace specialized output.

**Verification:**

- [ ] Existing full-array TimeF validation passes for both parts.
- [ ] Manifest/profile/licence/revision mismatch prevents READY.

**Dependencies:** Tasks 1, 5, and 13

**Files likely touched:** dispatch registry, KUKA metadata/cards, validation tests/docs

**Estimated scope:** Small/Medium (2-4 files)

## Checkpoint: Generic conversion

- [ ] All supported format families have exact read-back tests.
- [ ] Ambiguous fixtures stop and resume through confirmed mappings.
- [ ] KUKA specialized validation remains green.

## Task 15: Display approved sources

**Description:** Add an Approved sources view backed by the paginated catalog. Show source name,
provider, revision, licence, file formats, total size, latest approval time, approval-history link,
limitations, and an ingest action.

**Acceptance criteria:**

- [ ] Sources remain visible after reload without rerunning sourcing.
- [ ] Repeated approvals are not duplicate cards; new revisions are distinct.
- [ ] Empty, loading, pagination, protocol-error, and unavailable-service states are accessible.

**Verification:**

- [ ] Frontend service validators and component tests cover all states.
- [ ] No manifest field is reduced to a bare URL before ingestion.

**Dependencies:** Task 4 and frontend-owner availability

**Files likely touched:** frontend sourcing/services/data-workspace components and tests

**Estimated scope:** Medium (frontend owner)

## Task 16: Start and resume on-demand ingestion

**Description:** Let the user select approved assets, start one idempotent ingestion, inspect job
steps, confirm a requested mapping, and refresh ready datasets. Keep sourcing approval separate from
the ingestion action.

**Acceptance criteria:**

- [ ] Ingest sends approved-source/asset IDs and preserves one idempotency key across retries.
- [ ] NEEDS_INPUT, unsupported format, failure, and ready receipt are displayed distinctly.
- [ ] After a job exists, the source shows Resume or View result; status/operational retries reuse
  its ingestion ID, and ready data is not exposed before validation.

**Verification:**

- [ ] Frontend tests cover start, duplicate retry, mapping resume, unsupported format, and failure.
- [ ] Frontend tests prove a ready source cannot start another ingestion.
- [ ] Full frontend tests and production build pass.

**Dependencies:** Tasks 5, 12-15

**Files likely touched:** frontend services/workspace/mapping UI and integration docs

**Estimated scope:** Medium; split service and UI changes if needed

## Task 17: Record provider and format acceptance

**Description:** Run compact end-to-end fixtures covering all providers and formats, plus one
authorized real KUKA batch. Archive manifests, approval records, receipts, profiles, mappings, and
validation reports, but no large raw archives.

**Acceptance criteria:**

- [ ] Every provider reaches inventory and every supported format family reaches exact TimeF
  validation.
- [ ] At least one ambiguous case demonstrates NEEDS_INPUT and successful confirmed resume.
- [ ] Reports distinguish mock/cached/local/live input and do not claim full-corpus coverage.

**Verification:**

- [ ] Sourcing tests/lint, ingestion unit/pytest suites, frontend tests/build all pass.
- [ ] Clean-environment smoke checks reproduce public receipt hashes.
- [ ] Product documentation accurately states formats, provider coverage, and limitations.

**Dependencies:** Tasks 1-16

**Files likely touched:** compact integration fixtures, component READMEs, integration/submission docs

**Estimated scope:** Medium (3-5 files plus generated compact receipts)

## Checkpoint: Complete

- [ ] Approved sources are durable, browsable, revisioned, and retained for provenance.
- [ ] All three providers enter generic intake without executing downloaded code.
- [ ] Supported formats reach validated TimeF or pause for explicit user mapping.
- [ ] Approval never automatically starts ingestion.

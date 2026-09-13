# Implementation Plan: Approved source library and generic ingestion

## Overview

Connect data_sourcing and data_ingestion through a persistent approved-source library and a
versioned, replayable manifest. After a human approves a sourced dataset, the source and exact
revision are retained and displayed. The user may later start its one logical ingestion job without
rerunning discovery.

Ingestion accepts every approved GitHub, Zenodo, and Hugging Face source into a generic intake
pipeline. It safely acquires and inventories the source, then converts supported time-series layouts
using format adapters. The first supported families are CSV/TSV, Parquet, NumPy NPY/NPZ, MATLAB MAT,
and HDF5. An unfamiliar format or ambiguous record/time/channel/unit mapping remains stored and
visible, but pauses as NEEDS_INPUT instead of being guessed or reported as ready.

This plan is based on origin/main at ed10804 (13 September 2026). Implementation must begin from
current origin/main.

## User Flow

    Run bounded sourcing
      -> review evidence and approve a source
      -> source/revision appears in Approved sources
      -> select source and assets
      -> start ingestion on demand
      -> safely acquire and inspect files
      -> confirm mapping if required
      -> build and validate TimeF
      -> retain ingestion receipt and ready dataset

Approval never automatically starts a download. Sourcing evidence, ingestion observations,
publisher annotations, deterministic measurements, and model predictions remain separate sources.

## Current State and Gaps

- Sourcing already writes one manifest.json inside each approved run directory.
- There is no cross-run approved-source index or list/detail API. A user cannot currently return to
  previously approved sources without remembering a sourcing run ID.
- Reapproving the same source/revision creates another run artifact rather than one catalog entry
  with immutable approval history.
- The manifest lacks exact provider assets, stable download identities, per-file sizes, and
  source-provided checksums required for reproducible acquisition.
- The frontend reduces an approved manifest to canonicalUrl; approval identity and evidence are lost
  before the speculative ingestion request.
- data_ingestion accepts local MATLAB directories and has specialized KUKA connectors. It has no
  generic source resolver, downloader, format-adapter registry, persistent job API, or mapping
  confirmation flow.
- The KUKA TimeF cards declare MIT, while current Zenodo evidence and the training dataset card
  attribute dataset files under CC BY 4.0. The integration must resolve this rather than substituting
  the GitHub processing-code licence.

## Scope

### In scope

- Persist and list every human-approved source independently of its sourcing run.
- Preserve immutable approval events and group repeated approval of the same provider URL/revision
  into one approved-source catalog entry.
- Generic acquisition from approved GitHub, Zenodo, and Hugging Face sources.
- Safe ZIP, TAR, TAR.GZ, and TAR.ZST extraction.
- Bounded structural inspection of CSV/TSV, Parquet, NPY/NPZ, MAT, and HDF5.
- Generic mapping of common wide/long tabular layouts and named-array layouts into TimeF.
- Explicit user confirmation when record boundaries, timestamps, channels, units, or annotations
  are ambiguous.
- Preserve specialized KUKA connectors as the preferred path for their exact source identities.
- One idempotent on-demand ingestion job and receipt per approved source revision, plus frontend
  display of approved sources.

### Out of scope

- Claiming every arbitrary dataset can be converted automatically.
- Executing repository code, notebooks, dataset scripts, Hugging Face remote code, or downloaded
  binaries.
- Inferring missing units, physical meaning, annotations, or labels solely from filenames.
- Supporting databases, media-only datasets, live streams, proprietary formats, or every nested
  JSON layout in the first increment.
- Automatically ingesting immediately after approval.
- Passing source annotations or sourcing evidence into OpenTSLM prompts.
- Replacing the KUKA connector with a less precise generic mapping.
- Full-corpus downloads as unit or acceptance tests.

## Interpretation of Every Dataset

Every approved GitHub, Zenodo, or Hugging Face dataset receives:

1. A durable approved-source entry.
2. A reproducible provider/revision identity.
3. Safe acquisition or a precise acquisition failure.
4. A bounded file inventory and format classification.
5. An ingestion result: READY, NEEDS_INPUT, UNSUPPORTED_FORMAT, or FAILED.

READY requires a supported format, a valid time-series mapping, and deterministic output
validation. Universal intake must not be confused with fabricated universal conversion.

## Architecture Decisions

1. **Approved sources are first-class resources.** A sourcing run is an audit trail; an approved
   source is a catalog item. One source revision may have multiple approval events but at most one
   logical ingestion job.
2. **Source revisions are immutable.** The catalog key is derived from source kind, canonical URL,
   and provider revision. A new upstream revision is a new catalog item requiring approval.
3. **Approval and catalog persistence are recoverable.** Manifest creation and catalog upsert are
   idempotent. Startup reconciliation repairs an approved run whose catalog upsert was interrupted.
4. **No direct Python-package dependency.** Sourcing publishes JSON; ingestion owns a strict consumer
   model. Golden producer/consumer fixtures detect drift across separate environments.
5. **Ingestion starts from approvedSourceId.** A browser does not supply an arbitrary download URL.
   The ingestion service resolves the immutable manifest from the internal approved-source endpoint
   and records its SHA-256.
6. **Provider and format adapters are separate.** Provider adapters acquire immutable bytes; format
   adapters inspect and decode them.
7. **Downloaded content is data, never instructions.** No code, import hook, notebook, macro, plugin,
   or Hugging Face remote implementation is executed.
8. **Mapping is explicit and resumable.** Deterministic rules may propose a mapping, but ambiguous
   semantics produce NEEDS_INPUT. A versioned MappingSpec is validated before conversion.
9. **Specialized adapters win when available.** Exact KUKA identities dispatch to the current Part I
   and Part II connectors. Generic loaders are the fallback.
10. **Expensive effects are idempotent.** A unique constraint on approved-source ID prevents a
    second logical ingestion. Retries, mapping confirmation, and crash recovery continue the same
    ingestion ID and never create another ready dataset.

## Approved Source Model

An approval creates or updates one catalog entry and appends an immutable approval event.

    ApprovedSource
      approvedSourceId
      canonicalUrl + sourceKind + sourceRevision
      current display summary
      latest manifest SHA-256
      createdAt + latestApprovedAt

    ApprovalEvent
      sourcingRunId + candidateId
      manifest snapshot + SHA-256
      approvedAt
      approvedBy (server-derived only when authentication exists)

Repeated approval of the same source revision does not duplicate the visible source. A new revision
does not overwrite the old one. The local demo is single-user; a shared deployment must scope catalog
queries to the authenticated tenant/user and must not trust a reviewer identifier from the request.

### Approved-source API

    GET /api/approved-sources?page=1&pageSize=20&sourceKind=ZENODO&query=robot
      -> { data: ApprovedSourceSummary[], pagination: {...} }

    GET /api/approved-sources/{approvedSourceId}
      -> ApprovedSourceDetail with approval history

    GET /api/approved-sources/{approvedSourceId}/manifest
      -> exact latest approved manifest

The existing run and manifest endpoints remain compatible. No delete/unapprove endpoint is needed
for the first increment; archival can be designed later without destroying audit history.

## Sourcing Manifest Contract

Keep existing fields and add acquisition fields with safe defaults so persisted legacy manifests
still deserialize. Legacy manifests remain displayable but require metadata refresh before download.

    {
      "schemaVersion": "1.1",
      "runId": "...",
      "candidateId": "...",
      "canonicalUrl": "https://zenodo.org/records/21927431",
      "sourceKind": "ZENODO",
      "sourceRevision": "21927431.r4",
      "licenseId": "cc-by-4.0",
      "assets": [
        {
          "assetId": "...",
          "name": "collision-batch-01.tar.zst",
          "role": "DATA",
          "sizeBytes": 123,
          "providerLocator": "...",
          "downloadUrl": "https://...",
          "sourceChecksum": {"algorithm": "sha256", "value": "..."}
        }
      ],
      "evidenceIds": ["..."],
      "limitations": ["..."]
    }

Rules:

- Assets come only from the approved candidate's native record.
- providerLocator is the stable identity; downloadUrl is credential-free and revalidated at use.
- GitHub assets pin a commit SHA, Hugging Face assets pin a dataset revision, and Zenodo assets pin
  the exact record revision.
- At least one non-empty DATA asset makes a manifest acquisition-ready. Documentation and checksum
  assets may accompany it.
- Provider checksums retain their actual algorithm. Ingestion also computes SHA-256.
- The manifest contains no credentials, local paths, executable instructions, or its own hash.

## Generic Ingestion Pipeline

    approvedSourceId
      -> resolve and hash manifest
      -> select manifest-listed assets
      -> provider adapter downloads to quarantine/cache
      -> safe archive expansion
      -> format adapters produce bounded ResourceProfiles
      -> specialized connector or confirmed MappingSpec
      -> TimeF conversion
      -> deterministic validation and receipt

### Provider adapters

- **Zenodo:** exact record/revision, API file identity, size, checksum, and content link.
- **GitHub:** exact repository and commit SHA, tree/blob identity, raw file retrieval; never clone and
  execute the repository.
- **Hugging Face:** exact dataset repository and revision, sibling/LFS identity and size; never enable
  trust_remote_code.

All adapters repeat URL, DNS, redirect, response-size, timeout, and checksum validation. Credentials
stay server-side and never enter manifests or frontend variables.

### Format adapters

    FormatAdapter
      probe(path, magicBytes) -> supported format
      inspect(path, limits) -> ResourceProfile
      read(mapping, limits) -> bounded typed batches/arrays

- **CSV/TSV:** bounded dialect/header/type inspection; wide and long layouts after mapping.
- **Parquet:** schema and row-group inspection; column projection rather than whole-file loading.
- **NPY/NPZ:** dtype, shape, and named-array inventory; object arrays disabled.
- **MAT:** existing SciPy loader for supported files; v7.3 delegated to HDF5; no macros/code.
- **HDF5:** bounded group/dataset traversal; numeric arrays only initially.

Extension and magic bytes must agree. Archives are containers, not inferred data formats. Per-file,
row, column, array, nesting, returned-value, memory, and elapsed-time limits are enforced.

### Mapping contract

A MappingSpec records:

- dataset and record identity fields;
- one explicit or regular time axis and its unit/timezone where applicable;
- channel selectors, stable names, units, and value dtypes;
- optional source annotations, retained as publisher annotations;
- layout type: WIDE_TABLE, LONG_TABLE, NAMED_ARRAYS, or specialized connector;
- mapping version and evidence/decision notes.

Unambiguous structural rules may draft a mapping, but unknown units or semantic ambiguity require a
user decision. PUT /api/ingestions/{ingestionId}/mapping is idempotent and resumes a job only after
the mapping validates against the inspected source and current job revision.

## Ingestion API

    POST /api/ingestions
    Idempotency-Key: <stable key for this user intent>
    {
      "approvedSourceId": "...",
      "assetIds": ["..."]
    }
      -> 202 { ingestionId, statusUrl }

    GET /api/ingestions/{ingestionId}
      -> existing state plus source ID, steps, mappings, warnings, and receipt

    PUT /api/ingestions/{ingestionId}/mapping
    {
      "jobRevision": 3,
      "schemaVersion": "1.0",
      "resourceId": "...",
      "layout": "WIDE_TABLE",
      "...": "..."
    }
      -> resumed job or 409 on stale/conflicting state

The states are queued, acquiring, inspecting, mapping, validating, importing, ready, needs_input,
unsupported_format, and failed. POST is a get-or-create operation backed by a unique approvedSourceId
constraint: an existing source returns its existing ingestion resource. Asset selection is fixed
when that job is created; a conflicting later selection returns 409. Retries and mapping completion
advance the same job. Once ready, its source, assets, mapping, receipt, and output are immutable.

## Ingestion Receipt

    approved source + approval event + manifest SHA-256
      -> selected provider assets
      -> expected and observed sizes/checksums
      -> extracted inventory and ResourceProfile hashes
      -> MappingSpec hash and connector/format-adapter versions
      -> TimeF dataset/version
      -> validation report hash and terminal status

The receipt records sourced claims and observed facts separately. Mismatches prevent READY. It
contains no raw arrays, credentials, or machine-specific absolute path in its public form.

## Task List

### Phase 1: Approved source library

- [ ] Task 1: Freeze manifest, catalog, mapping, and licence contracts.
- [x] Task 2: Emit acquisition-ready assets for all three providers.
- [x] Task 3: Persist and reconcile approved-source catalog entries.
- [x] Task 4: Expose paginated approved-source list/detail/manifest APIs.

### Checkpoint: Persistent approvals

- [x] Approving a source makes it visible without starting ingestion.
- [x] Reapproving the same revision adds history but not a duplicate source.
- [x] A new revision is a distinct source and legacy manifests remain displayable.

### Phase 2: Generic intake

- [x] Task 5: Create one idempotent ingestion job per approved source.
- [x] Task 6: Implement secure acquisition core and Zenodo adapter.
- [x] Task 7: Add pinned GitHub and Hugging Face acquisition adapters.
- [x] Task 8: Add content-addressed storage and safe archive extraction.
- [x] Task 9: Add the format-adapter registry and bounded resource inventory.

### Checkpoint: Universal provider intake

- [x] Approved fixtures from GitHub, Zenodo, and Hugging Face can be acquired and inventoried.
- [x] Unsupported formats return UNSUPPORTED_FORMAT; unsafe content fails closed.
- [x] No downloaded code is executed.

### Phase 3: Common time-series formats

- [x] Task 10: Add CSV/TSV and Parquet adapters.
- [x] Task 11: Add NPY/NPZ, MAT, and HDF5 adapters.
- [x] Task 12: Add validated MappingSpec proposals and user-confirmed resume.
- [x] Task 13: Build generic TimeF datasets and deterministic receipts.
- [x] Task 14: Preserve and verify specialized KUKA dispatch.

### Checkpoint: Generic conversion

- [x] One fixture per format family reaches READY with exact value/time/channel checks.
- [x] Ambiguous layouts reach NEEDS_INPUT and resume from a valid user mapping.
- [x] KUKA Part I/II still use specialized connectors and pass full-array validation.

### Phase 4: User-facing on-demand ingestion

- [x] Task 15: Display the approved-source library in the frontend.
- [x] Task 16: Start and resume ingestion from an approved source.
- [ ] Task 17: Run and document provider/format/end-to-end acceptance.

### Checkpoint: Complete

- [x] Users can return to approved sources and start ingestion without rerunning sourcing.
- [x] A source with an ingestion already created shows that job/result instead of another ingest
  action.
- [x] The UI displays source revision, licence, formats, approval history, and ingestion status.
- [x] Ready datasets have reproducible manifests, mappings, TimeF output, and receipts.

## Ownership and Coordination

- **Data sourcing owner:** Tasks 1-4; provider metadata in Task 2, catalog persistence, APIs, and
  sourcing contract tests.
- **Data ingestion owner:** Tasks 1, 5-14, and 17; approved-source resolver, acquisition, extraction,
  format adapters, mapping, TimeF output, job state, and receipts.
- **Frontend coordinator/owner:** Tasks 15-16 after backend contracts are frozen.
- **Joint review:** Task 1, API handoff before Task 15, and end-to-end receipts in Task 17.

Work should use separate focused codex/ branches/worktrees. The manifest contract is frozen before
parallel provider/format work. One coordinator integrates sourcing, ingestion foundation, format
adapters, and frontend changes in dependency order.

## Risks and Mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Generic becomes unsupported auto-inference | High | Universal intake, explicit format/mapping outcomes, fail closed |
| Duplicate approved sources | Medium | Stable source/revision key plus immutable approval events |
| Crash between approval and catalog update | Medium | Idempotent upsert and startup reconciliation |
| Upstream revision changes | High | New revision requires a new approved-source entry |
| Provider-specific URL drift | Medium | Stable provider locator plus URL re-resolution/revalidation |
| Downloaded repository executes code | High | File-only adapters; no clone/import/notebook/remote code |
| SSRF or malicious archives | High | Allowlists, DNS/redirect checks, quarantine, traversal/link/bomb limits |
| Large datasets exhaust storage/memory | High | Asset selection, streaming, content cache, resource ceilings |
| Extension misidentifies content | Medium | Extension plus magic/schema probing |
| Ambiguous timestamps/channels/units | High | NEEDS_INPUT and validated MappingSpec |
| Format adapters diverge | Medium | One ResourceProfile/MappingSpec and conformance tests |
| Licence conflict propagates | High | Separate code/data licences and block readiness |
| Duplicate ingestion of one source | Medium | Unique approvedSourceId constraint and get-or-create API |
| Frontend races active ownership | Medium | Backend checkpoints first; frontend owner implements |

## Open Questions for Task 1

1. Confirm the initial formats: CSV/TSV, Parquet, NPY/NPZ, MAT, and HDF5. JSON/JSONL is deferred
   unless a concrete approved source needs it.
2. Should repeated approval of an identical source revision retain all approval events
   (recommended) or only the latest event?
3. In a shared deployment, which authenticated tenant/user owns an approved-source entry? The
   client should not supply this identity.
4. Resolve the KUKA dataset-file licence against the exact upstream record before changing TimeF
   cards.
5. Select one small non-KUKA acceptance source per provider without committing large raw data.

## Verification Commands

    cd data_sourcing && uv run pytest && uv run ruff check src tests
    cd ../data_ingestion && PYTHONPATH=src python -m unittest discover -s tests -v
    cd ../data_ingestion && PYTHONPATH=src python -m pytest -q
    cd ../frontend && npm test && npm run build

Provider tests use mock HTTP by default. Real downloads are separately authorized, bounded, and
reported as cached or live. Final validation loads each produced TimeF dataset and compares selected
values, axes, channels, annotations, and provenance against the acquired source.

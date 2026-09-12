# Deterministic dataset ingestion

This project discovers MATLAB time-series runs, inspects variables, builds a JSON-serializable dataset profile, and runs deterministic quality checks. KUKA-specific interpretations are isolated in `src/dataset_profiler/datasets/kuka_collision.py`; the MATLAB loader and profiler remain reusable for later datasets.

## Usage

Use Python 3.11 or newer. From this directory, either install the package:

```bash
python -m pip install -e '.[hdf5,plot,dev]'
```

or run directly from the checkout by setting `PYTHONPATH=src`:

```bash
PYTHONPATH=src python -m dataset_profiler.cli inspect \
  "$KUKA_PART1_ROOT/03-15-12-53/JK_MsrExtTrq.mat"

PYTHONPATH=src python -m dataset_profiler.cli profile \
  "$KUKA_PART1_ROOT" \
  --dataset-id kuka-collision-part1-batch01 \
  --hints kuka-collision \
  --output outputs/dataset_profile.json

PYTHONPATH=src python -m dataset_profiler.cli plot \
  "$KUKA_PART1_ROOT/03-15-12-53"
```

HDF5/MATLAB v7.3 support is provided by the `hdf5` extra. Plotting uses the `plot` extra. Figures are displayed interactively and are never saved automatically.

## Verification

Run the dependency-light test suite from this directory:

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
```

The generated Batch 01 artifact is at [outputs/dataset_profile.json](outputs/dataset_profile.json), and the observed facts, interpretations, and unresolved questions are documented in [docs/KUKA_BATCH_01_INSPECTION.md](docs/KUKA_BATCH_01_INSPECTION.md).

## Approved-source ingestion handoff

`dataset_profiler.ingestion` owns the strict consumer for sourcing manifest schema 1.1 and the
SQLite ingestion-job identity. It does not import `data_sourcing`; both modules validate the shared
[`approved-source-manifest-v1.1.json`](../docs/contracts/approved-source-manifest-v1.1.json)
fixture. Legacy or incomplete manifests, provider-mismatched URLs, and manifests without data assets
fail before acquisition.

One `approvedSourceId` has at most one logical ingestion job. Matching retries return the existing
job, including after restart; another asset selection returns a conflict rather than creating a
second ready dataset. The acquisition worker downloads only manifest-selected assets and never
executes source code or marks a dataset ready.

Run the local ingestion API separately from the scout:

```bash
export INGESTION_SOURCING_API_URL=http://127.0.0.1:8001
export INGESTION_DATA_DIR=var/ingestion
dataset-ingestion-api
dataset-ingestion-worker
```

`POST /api/ingestions` accepts `{"approvedSourceId":"..."}` and an optional non-empty
`assetIds` list. The service resolves and hashes the approved manifest itself; it never accepts a
browser-supplied source or download URL. `GET /api/ingestions/{ingestionId}` returns the persisted
job, and `GET /api/ingestions/{ingestionId}/assets` returns immutable public acquisition receipts.
A missing dataset-file license creates the one job in `needs_input` rather than substituting a
repository code license.

The first acquisition adapter handles manifest-listed Zenodo assets only. It revalidates the exact
HTTPS provider URL, optionally resolves only public addresses, disables redirects, streams into an
isolated staging file, enforces both manifest and configured byte limits, verifies the original MD5
or SHA-256 when supplied, computes SHA-256 for content addressing, and atomically promotes verified
bytes. The single-worker process claims queued jobs atomically, re-resolves the approved manifest,
checks its stored SHA-256, and records expected provider claims separately from observed content
size and SHA-256. A restart requeues interrupted acquisition, and a retry reuses already verified
content through its persisted source-asset receipt. Successful jobs advance to `inspecting`; archive
extraction and format inventory are later stages.

The worker also supports pinned GitHub repository files and Hugging Face dataset files. GitHub
acquisition verifies that the approved path resolves to the approved blob at the exact commit, then
recomputes Git's blob identity over the downloaded bytes. Hugging Face acquisition requires the
exact dataset revision in both the locator and download URL, validates the provider revision header,
verifies LFS SHA-256 when present, and rejects unresolved LFS pointer text. Provider redirects and
DNS are revalidated at every hop. Optional `GITHUB_TOKEN` and `HF_TOKEN` values are read only by the
worker and never persisted in manifests or receipts.

ZIP, TAR, TAR.GZ, and TAR.ZST assets are expanded into the same content-addressed cache before
inventory. Extraction writes into isolated staging and atomically publishes only after every member
passes file-count, size, expansion-ratio, path-depth, and path-length limits. Absolute/traversal
paths, duplicate paths, encryption, symlinks, hard links, devices, and other special members are
rejected. The extraction marker contains only relative paths, sizes, and hashes and is revalidated
before reuse.

The cache is intentionally retained while any ingestion receipt refers to it; there is no automatic
age-based deletion. Operators may remove an unreferenced `cache/content/<prefix>/<sha256>` and its
matching `cache/extracted/<prefix>/<sha256>` only while the API and worker are stopped. A later
explicit retry safely reacquires missing content. Staging directories are temporary and are removed
after success or failure.

After materialization, the worker inventories every regular file and exposes the persisted result at
`GET /api/ingestions/{ingestionId}/resources`. Selection is deterministic: Parquet, NPY, NPZ,
MATLAB v5, and HDF5 require matching magic bytes and extensions; CSV/TSV require bounded UTF-8
samples with a consistent dialect matching the extension. Empty, executable, unknown, mismatched,
and nested archive files remain visible as `UNSUPPORTED` with a stable reason and are never opened
as code. Jobs with at least one supported resource advance to `mapping`; jobs with none end in
`unsupported_format`.

## Declarative semantic specs

`dataset_profiler.semantic_spec` defines the typed, JSON-serializable `DatasetSpec` v0.1 model and
`validate_dataset_spec(profile, spec)`. Validation returns a structured `ValidationResult`; ordinary
unsupported or inconsistent claims are reported as stable issue codes rather than exceptions. The
canonical KUKA Part I / Batch 01 mapping is the packaged
[`kuka_collision_part1.json`](src/dataset_profiler/semantic_spec/specs/kuka_collision_part1.json)
artifact and can be loaded with `load_kuka_collision_part1_spec()`. It describes semantics only and
does not drive or duplicate the trusted KUKA parser/connector implementation.

## Bounded Evidence API

`EvidenceSession(profile)` exposes deterministic `dataset_summary()`, `variable_schema(name)`,
`signal_statistics(name)`, and `metadata_summary()` queries without exposing absolute source paths or
full arrays.
Every successful query returns a structured `Evidence` object with a deterministic evidence ID;
normal failures return a structured `EvidenceError`. Per-query limits and cumulative session budgets
are configured with `EvidenceLimits` and `EvidenceBudget`.

Source excerpts are disabled by default. A trusted host can opt in with
`EvidenceSession(profile, allow_bounded_source_excerpts=True)`. The caller still supplies only a
profiled run ID, variable, start, length, and channel indices. The implementation resolves the
profile-owned source internally and enforces sample, channel, query, excerpt-query, returned-value,
and serialized-response limits.

Defaults are 32 samples and 8 channels per excerpt, 16 per-channel statistic entries, 50 metadata
entries per collection, and 32 KiB per serialized response. A session permits 32 total queries,
4 successful excerpt queries, and 256 returned excerpt values by default.

Trusted hosts can also register Markdown and UTF-8 text documentation explicitly. Search callers
receive logical source IDs and bounded excerpts, never document paths or semantic mappings:

```python
from dataset_profiler.evidence import DocumentationSource, EvidenceSession

session = EvidenceSession(
    profile,
    documentation_sources=[
        DocumentationSource("dataset-readme", "/trusted/dataset/README.md", "Dataset README"),
    ],
)
sources = session.documentation_sources()
matches = session.documentation_search("signal name", max_results=3)
```

Documents are read once at session construction and identified by a content hash in evidence
identity. Search is deterministic, case-insensitive, and line based, with exact-case matches ranked
first. Defaults allow 16 registered sources of at most 1 MiB each, 5 results, 800 characters per
excerpt, 3,200 excerpt characters per search, 8 documentation searches, and 12,000 returned
documentation characters per session. Unsupported, malformed, missing, and unavailable sources use
structured statuses or `EvidenceError` responses.

## KUKA Part I to TimeF

The connector is owned by this package under `dataset_profiler.timenet.kuka_collision`. Its dataset
identity is `kuka/collision-part1`; Batch 01 is only the current source subset. TimeNet core is a
direct Git dependency pinned to an immutable commit. The pin is recorded in
[`timenet.lock`](timenet.lock), the interface study in
[`docs/TIMENET_INTERFACE_NOTE.md`](docs/TIMENET_INTERFACE_NOTE.md), and semantic choices in the
repository-level [`HUMAN_MAPPING_DECISIONS.md`](../HUMAN_MAPPING_DECISIONS.md).
Keeping discovery, conversion, and validation here ensures the single submission repository is the
authoritative product implementation; the external TimeNet package supplies only its public API and
TimeF model.

From a clean checkout, install and build the source directly. No TimeNet source checkout or native
connector discovery is required:

```bash
cd data_ingestion
export KUKA_PART1_ROOT=/path/to/extracted/part-i-or-batch
python -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/python scripts/build_kuka_timef.py "$KUKA_PART1_ROOT" outputs/timef
.venv/bin/python -c \
  "from timenet.client import TimeNet; TimeNet(registry='outputs/timef').load('kuka/collision-part1').describe()"
```

Validate the reader result against the deterministic profile and selected raw MAT values:

```bash
.venv/bin/python scripts/validate_kuka_timef.py outputs/timef \
  --profile outputs/dataset_profile.json \
  --source "$KUKA_PART1_ROOT" \
  --output outputs/kuka_timef_validation.json
```

The validator emits JSON and exits nonzero if any check fails. The checked-in result for the verified
Batch 01 build is [`outputs/kuka_timef_validation.json`](outputs/kuka_timef_validation.json). The
generated TimeF registry used for that result remains at `outputs/timef/` (ignored by Git because it
is a reproducible 36 MB build artifact).

## KUKA Part II intentional contacts to TimeF

Part II is deliberately separate as `kuka/contact-part2`; it uses the same shared KUKA parser and
signal representation as Part I, but emits `intentional_contact` point annotations. Build and
validate it with:

```bash
export KUKA_PART2_ROOT=/path/to/extracted/contact-batch-or-part-ii-root
.venv/bin/python -m dataset_profiler.cli profile "$KUKA_PART2_ROOT" \
  --dataset-id kuka/contact-part2 --hints kuka-contact-part2 \
  --output outputs/kuka_contact_part2_profile.json
.venv/bin/python scripts/build_kuka_contact_part2_timef.py "$KUKA_PART2_ROOT" outputs/timef-part2
.venv/bin/python scripts/validate_kuka_contact_part2_timef.py outputs/timef-part2 \
  outputs/kuka_contact_part2_profile.json --source "$KUKA_PART2_ROOT" \
  --output outputs/kuka_contact_part2_timef_validation.json
```

The checked-in profile, full-array validation result, and the raw comparison are documented in
[`docs/KUKA_CONTACT_PART2_INSPECTION.md`](docs/KUKA_CONTACT_PART2_INSPECTION.md).

## Unified KUKA batch builder

Use one command for either extracted batch; `--part` deliberately requires a single part, so Part I
and Part II remain separate datasets:

```bash
.venv/bin/python scripts/build_kuka_timef_dataset.py /path/to/collision-batch-01 outputs/timef \
  --part part1
.venv/bin/python scripts/build_kuka_timef_dataset.py /path/to/contact-batch-01 outputs/timef \
  --part part2
```

The resulting dataset versions are written below the registry as `kuka/collision-part1` and
`kuka/contact-part2`, respectively. The existing per-part build scripts remain available.

## Semantic Agent v0.1

The bounded semantic agent uses only `EvidenceSession` tools and calls OpenAI without an agent
framework or SDK. Keep the key out of files and source control; export it in the launch shell:

```bash
export OPENAI_API_KEY='your-key'
PYTHONPATH=src python scripts/run_semantic_agent.py \
  --profile outputs/dataset_profile.json \
  --documentation /trusted/KUKA_README.txt \
  --output-dir outputs --evaluate-kuka-part1
```

If you launch through Codex and its process does not inherit your terminal environment, create
`data_ingestion/.env` (which is gitignored) containing `OPENAI_API_KEY=your-key` instead.
For a self-hosted OpenAI-compatible server, set `OPENAI_BASE_URL=http://localhost:PORT/v1` there
as well; its model name is supplied with `--model`.

Pass only original dataset documentation. Do not register this repository's inspection notes,
human mapping decisions, semantic reference JSON, or connector files. The command writes the
candidate, trace, validation result, and post-generation evaluation report.

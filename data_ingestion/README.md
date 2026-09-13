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

## Job-oriented onboarding

`dataset_profiler.onboarding` wraps the existing profiler, semantic agent, validator/repair loop,
human implementation handoff, and native TimeNet workflow in a persisted state machine. Structured
state lives under `outputs/onboarding_jobs/<job-id>/`; callers never need to parse logs.

```python
from dataset_profiler.onboarding import (
    OnboardingOrchestrator,
    SourceDescriptor,
    bosch_reference_backend,
)

service = OnboardingOrchestrator(
    "outputs/onboarding_jobs", bosch_reference_backend()
)
job = service.create_job(SourceDescriptor(
    source_type="local_directory",
    local_path="/path/to/CNC_Machining",
    dataset_id="bosch-cnc",
))
job = service.run_job(job.id)
```

An unresolved implementation requirement pauses with
`status == "needs_human_resolution"`. Resume the same job with
`service.resolve_blocker(...)` followed by `service.continue_job(job.id)`. The thin `onboard`
command exposes the same create/status/run/resolve/continue operations. The Bosch preset reuses the
audited semantic artifacts and native TimeNet connector, then runs connector tests, `timenet-build`,
`TimeNet.load()`, and a full raw-to-TimeF comparison.

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

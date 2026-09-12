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

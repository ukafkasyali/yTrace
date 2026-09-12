# Deterministic dataset ingestion

This project discovers MATLAB time-series runs, inspects variables, builds a JSON-serializable dataset profile, and runs deterministic quality checks. KUKA-specific interpretations are isolated in `src/dataset_profiler/datasets/kuka_collision.py`; the MATLAB loader and profiler remain reusable for later datasets.

## Usage

Use Python 3.10 or newer with NumPy and SciPy. From this directory, either install the package:

```bash
python -m pip install -e '.[hdf5,plot,dev]'
```

or run directly from the checkout by setting `PYTHONPATH=src`:

```bash
PYTHONPATH=src python -m dataset_profiler.cli inspect \
  /home/ugur/data/collision-batch-01/03-15-12-53/JK_MsrExtTrq.mat

PYTHONPATH=src python -m dataset_profiler.cli profile \
  /home/ugur/data/collision-batch-01 \
  --dataset-id kuka-collision-part1-batch01 \
  --hints kuka-collision \
  --output outputs/dataset_profile.json

PYTHONPATH=src python -m dataset_profiler.cli plot \
  /home/ugur/data/collision-batch-01/03-15-12-53
```

HDF5/MATLAB v7.3 support is provided by the `hdf5` extra. Plotting uses the `plot` extra. Figures are displayed interactively and are never saved automatically.

## Verification

Run the dependency-light test suite from this directory:

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
```

The generated Batch 01 artifact is at [outputs/dataset_profile.json](outputs/dataset_profile.json), and the observed facts, interpretations, and unresolved questions are documented in [docs/KUKA_BATCH_01_INSPECTION.md](docs/KUKA_BATCH_01_INSPECTION.md).

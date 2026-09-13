# Robot telemetry observability training

This package prepares raw KUKA LWR4+ external-joint-torque recordings and fine-tunes a generative
OpenTSLM model to answer natural-language questions about contact, event semantics, affected joints,
and event timing. It is the data/training half of the Foxglove-like observability demo.

The canonical path is **raw source -> TimeNet connector -> TimeF dataset -> training-window
adapter -> model**. TimeF records preserve complete recordings, signal metadata, units, and event
provenance. The adapter validates that contract and materializes memory-mapped windows once so GPU
training does not repeatedly decode Parquet. A new robot dataset should add a TimeNet connector and
mapping rather than fork the training loop.

## Audited submission comparison

The completed feature, OpenTSLM, Qwen plot and zero-signal predictions are archived in
[`docs/submission/evaluation`](../docs/submission/evaluation/report/comparison.md). Recompute the
matched 512-window comparison without heavy dependencies using:

```bash
PYTHONPATH=src python3 -m robot_observability.comparison \
  --source ../docs/submission/evaluation/source --output /tmp/trace-comparison
```

The report validates example/target/split identity and distinguishes positive-contact F1 from
answer availability, complete-summary usability and recording-group uncertainty. The feature
baseline is stronger on classification; no OpenTSLM superiority is claimed.


## Locked decisions

- Full raw collision and intentional-contact corpora, not the leakage-prone quick-start CSVs.
- 1.024 s, seven-channel external-torque windows at 1 kHz.
- Manual `JK_moments.mat` marker as event-onset ground truth.
- Event position jittered from 205–716 ms; recording folders are split before window generation.
- 70/15/15 recording-grouped train/validation/test split, stratified by experiment class.
- Train-only robust median/MAD scaling; signed torque and physical-unit statistics retained as
  metadata but excluded from model prompts to prevent a textual shortcut.
- Strongest-joint pseudo-label is calibrated top-5%-mean disturbance, not contact-location truth.
- Natural-language evidence plus strict JSON output. Free motion uses `null` onset/joint fields.
- Primary TSLM: OpenTSLM SoftPrompt + Llama 3.2 1B + HAR warm start.
- Training checkpoints contain only tensors and primitive flags, so inference can
  validate them with PyTorch's weights-only loader before release.
- Baselines: transparent signal features and Qwen3-VL 4B over equivalent seven-panel plots.
- Retrospective observability only; this is not a causal collision detector or safety controller.

See [the design decisions](docs/design.md), [dataset documentation](docs/dataset.md), and
[remote runbook](docs/runbook.md).

## Local commands

```bash
uv venv
uv pip install -e '.[train,dev,timenet]'

robot-observe download --output data/raw
PYTHONPATH=../data_ingestion/src python ../data_ingestion/scripts/build_kuka_timef_dataset.py \
  data/raw/collision artifacts/timef-raw --part part1
PYTHONPATH=../data_ingestion/src python ../data_ingestion/scripts/build_kuka_timef_dataset.py \
  data/raw/contact artifacts/timef-raw --part part2

robot-observe prepare-timef --config configs/data_timef.yaml \
  --timef-version artifacts/timef-raw/kuka/collision-part1/1.0.0 \
  --timef-version artifacts/timef-raw/kuka/contact-part2/1.0.0
robot-observe baseline --prepared-root data/prepared/timef-v1 \
  --output-root artifacts/baseline/signal-features-512 --limit 512

python -m robot_observability.train_opentslm \
  --prepared-root data/prepared/timef-v1 \
  --run-name llama-har-sp-smoke \
  --smoke --max-steps 200
```

Every expensive operation is resumable or refuses to overwrite prior artifacts. Training emits an
atomic `status.json`, append-only `metrics.jsonl`, TensorBoard events, generated validation samples,
W&B prediction tables with seven-channel signal previews, real-versus-zero-signal canaries, and
`best_model.pt`/`last_model.pt` adapters. Every run also keeps a fixed, class-balanced probe from the
actual training subset. Its teacher-forced loss, decoded task metrics, exact-answer fit, matched-prompt
validation gap, and zero-signal response are tracked separately so a rapid token-loss drop cannot be
mistaken for task learning.

Run the capacity/fit check before another expensive run:

```bash
python -m robot_observability.train_opentslm \
  --prepared-root data/prepared/timef-v1 \
  --run-name llama-har-sp-fit-probe \
  --smoke --max-steps 320
python scripts/report_fit_probe.py --run-dir runs/llama-har-sp-fit-probe
```

Use `--strict` on the reporter only when it should block the next launch. Its thresholds are diagnostic
defaults, not benchmark acceptance criteria.

After a full run, audit checkpoint-selection drift, first-pass generation reliability, intent slices,
rationale coverage, signal ablations, and validation-methodology limitations from the immutable JSON
receipts:

```bash
python scripts/analyze_failure_modes.py \
  --run-dir runs/llama-har-sp-timef-rationale-focused-v6 \
  --output-dir artifacts/failure-analysis/v6
```

The JSON report is machine-readable and the Markdown report is presentation-ready. This is a
validation audit, not a replacement for one-time evaluation on unused recording groups.

# Robot telemetry observability training

This package prepares raw KUKA LWR4+ external-joint-torque recordings and fine-tunes a generative
OpenTSLM model to answer natural-language questions about contact, event semantics, affected joints,
and event timing. It is the data/training half of the Foxglove-like observability demo.

The design is intentionally extensible: raw-source readers produce canonical window records, while
the TimeNet connector, model adapters, baselines, prompts, and UI payload consume that common
contract. A new robot dataset should add a reader and mapping rather than fork the training loop.

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
robot-observe prepare --config configs/data.yaml
robot-observe baseline --prepared-root data/prepared/v1 --output-root artifacts/baseline/v1

python scripts/build_timef.py --registry artifacts/timef-registry --cache data/raw
python -m robot_observability.train_opentslm \
  --prepared-root data/prepared/v1 \
  --run-name llama-har-sp-smoke \
  --smoke --max-steps 200
```

Every expensive operation is resumable or refuses to overwrite prior artifacts. Training emits an
atomic `status.json`, append-only `metrics.jsonl`, TensorBoard events, generated validation samples,
W&B prediction tables, real-versus-zero-signal canaries, and `best_model.pt`/`last_model.pt` adapters.

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

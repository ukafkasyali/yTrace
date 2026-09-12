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
- Train-only robust median/MAD scaling; signed torque and physical-unit summary statistics retained.
- Strongest-joint pseudo-label is calibrated top-5%-mean disturbance, not contact-location truth.
- Natural-language evidence plus strict JSON output. Free motion uses `null` onset/joint fields.
- Primary TSLM: OpenTSLM SoftPrompt + Llama 3.2 1B + HAR warm start.
- Baselines: a multi-task 1D CNN, transparent signal features, and Qwen3-VL 4B over equivalent
  seven-panel plots in zero-shot and one-shot modes.
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

# Required learned baseline: event semantics, onset, and joint evidence.
python scripts/train_cnn.py \
  --prepared-root data/prepared/v1 \
  --run-name cnn-1d-seed-20260912

# Direct multimodal LLM: use the same command with --shots 0 for zero-shot.
python scripts/eval_plot_vlm.py \
  --prepared-root data/prepared/v1 \
  --output artifacts/evaluation/qwen3-vl-one-shot \
  --shots 1

python scripts/build_timef.py --registry artifacts/timef-registry --cache data/raw
python -m robot_observability.train_opentslm \
  --prepared-root data/prepared/v1 \
  --run-name llama-har-sp-smoke \
  --smoke --max-steps 200
```

All learned models select checkpoints on validation only. CNN, direct-LLM, and OpenTSLM test
evaluation use the same fixed 512-example sample (or the complete test split when smaller), selected
without replacement with seed `20260912`. The one-shot example is selected independently from the
training split and its record ID is saved in the run manifest.

Combine completed metric files without silently accepting mismatched sample counts:

```bash
python scripts/compare_models.py \
  --result CNN=runs/cnn-1d-seed-20260912/metrics.json \
  --result Direct-zero=artifacts/evaluation/qwen3-vl-zero-shot/metrics.json \
  --result Direct-one=artifacts/evaluation/qwen3-vl-one-shot/metrics.json \
  --result OpenTSLM=artifacts/evaluation/opentslm-test/metrics.json \
  --output artifacts/evaluation/model-comparison.json
```

Every expensive operation is resumable or refuses to overwrite prior artifacts. Training emits an
atomic `status.json`, append-only `metrics.jsonl`, TensorBoard events, generated validation samples,
and `best_model.pt`/`last_model.pt` adapters.

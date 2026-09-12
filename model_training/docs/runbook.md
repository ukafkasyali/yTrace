# Nebius H100 runbook

Remote workspace: `/home/atakantopaloglu/ysamet-training`.

## Persistent jobs

```bash
ssh nebius-h100
tmux list-sessions
tail -f /home/atakantopaloglu/ysamet-training/download.log
```

The data downloader also writes `data/raw/download_state.jsonl`. It resumes partial curl transfers,
validates Zenodo checksums, and marks each extracted archive.

## Authentication

```bash
/home/atakantopaloglu/ysamet-training/.auth-venv/bin/hf auth whoami
```

Never put the token in this repository or a command log.

## Observing a training run

```bash
tail -f runs/<run>/stdout.log
tail -f runs/<run>/metrics.jsonl
watch -n 2 nvidia-smi
ssh -L 6006:127.0.0.1:6006 nebius-h100
```

On the remote host, serve TensorBoard with:

```bash
.venv/bin/tensorboard --logdir runs --host 127.0.0.1 --port 6006
```

Then open `http://127.0.0.1:6006` locally. `status.json` is safe for the demo backend to poll while
the append-only JSONL retains full history.

The main W&B run exposes these complementary views:

- `data/training_manifest`: all selected training examples with stable source index, record/session ID,
  prompt variant, complete supervised answer, and target. The local JSONL and prepared arrays are SHA-256
  pinned in `run_manifest.json`.
- `data/training_probe_examples`: 48 event×intent-balanced examples from that exact selection, including
  a seven-channel normalized-signal preview with target onset/evidence markers.
- `training_probe/*`: teacher-forced loss and decoded metrics on those same examples, including strict
  schema and answer fit.
- `validation_matched/loss`: a validation loss using the same deterministic mixed-prompt distribution
  as training. Use this—not the all-intents model-selection loss—for a generalization-gap comparison.
- `training_probe_zero_signal/*`: paired predictions after removing the numeric signal.
- `optimization/*`: pre-clip gradient norm, pre-step parameter norm, post-step learning rate, actual
  update norm/parameter ratio, and gradient coverage for the encoder, projector, and LoRA groups.
- `training_probe/predictions` and `validation/samples`: versioned prediction tables with signal plots.

Before a full launch, run a 32-example capacity probe and inspect/report it:

```bash
.venv/bin/python -m robot_observability.train_opentslm \
  --prepared-root data/prepared/v1 \
  --run-name llama-har-sp-fit-probe \
  --smoke --max-steps 320
.venv/bin/python scripts/report_fit_probe.py \
  --run-dir runs/llama-har-sp-fit-probe
```

The report refuses to pass an unfinished run and checks decoded per-intent answers plus meaningful
paired signal ablation in addition to token loss.
Add `--strict` only if a nonzero exit should gate the next run.

After training, evaluate a fixed held-out subset without using it for model selection:

```bash
.venv/bin/python scripts/evaluate_opentslm.py \
  --checkpoint runs/<run>/best_model.pt \
  --output artifacts/evaluation/<run>-test
```

For an unattended run, `scripts/post_training.py` waits for the training tmux session to exit, then
runs the same held-out evaluation plus temporal-shift and channel-permutation grounding checks. It
writes an atomic `post_training_status.json` and can resume the W&B run to attach final metrics.

## Plot baseline environment

This baseline does no fitting. It sends leak-free seven-channel plots to a frozen Qwen3-VL model and
scores the same summary JSON contract as OpenTSLM. Do not start its server while an OpenTSLM process
is using the H100.

Keep vLLM separate so its Torch/CUDA dependency resolution cannot alter the training environment:

```bash
uv venv .vlm-venv
uv pip install --python .vlm-venv/bin/python 'vllm>=0.11,<1' qwen-vl-utils==0.0.14 \
  -e '.[plot-baseline]'
.vlm-venv/bin/vllm serve Qwen/Qwen3-VL-4B-Instruct \
  --revision ebb281ec70b05090aa6165b016eac8ec08e71b17 \
  --port 8000 --dtype bfloat16 --gpu-memory-utilization 0.70
```

Use BF16 for the primary plot baseline: this cached 4B checkpoint is about 8.3 GB and comfortably
fits an otherwise idle H100, while quantization could needlessly weaken the comparator. A vetted
pre-quantized AWQ checkpoint can be run as a separate secondary baseline:

```bash
.vlm-venv/bin/vllm serve <verified-awq-model-id> \
  --revision <exact-hugging-face-commit> \
  --port 8000 --dtype bfloat16 --gpu-memory-utilization 0.70
.vlm-venv/bin/python scripts/eval_plot_vlm.py \
  --model <verified-awq-model-id> --model-revision <exact-hugging-face-commit> \
  --quantization awq --output artifacts/evaluation/qwen3-vl-awq-plot-test
```

Do not label an unverified community checkpoint as the primary Qwen result. The installed vLLM 0.29
server has no BitsAndBytes load-format entry, so the older
`--quantization bitsandbytes --load-format bitsandbytes` recipe is not used here. Set `quantization`
to the server's actual mode before evaluation. The
evaluator checks the served model, captures the vLLM version, and writes an immutable manifest with
the prepared-record hash, exact record IDs, prompt hash, plot settings, and decoding settings. The
selection algorithm and seed intentionally match `scripts/evaluate_opentslm.py`, so the two models
are scored on identical windows and the natural held-out class distribution.

Run a 12-record plumbing check first, then use a new directory for the locked 512-record benchmark:

```bash
.vlm-venv/bin/python scripts/eval_plot_vlm.py \
  --output artifacts/evaluation/qwen3-vl-plot-smoke --limit 12 --wandb-mode disabled
.vlm-venv/bin/python scripts/eval_plot_vlm.py \
  --output artifacts/evaluation/qwen3-vl-plot-test
```

If the client or SSH connection is interrupted, rerun the exact command with `--resume`. Completed
record IDs are skipped; a truncated final JSONL line is repaired. Changing the model, subset,
prepared records, prompt, plot, or decoding settings causes a manifest mismatch instead of silently
mixing results.

W&B receives progress metrics and a class-balanced `test/examples` table containing the rendered plot,
target JSON, raw response, parsed prediction, per-task correctness/errors, and latency. `metrics.json` uses the same
`evaluate_rows` metric names as the trained model and deterministic feature baseline.

Run the train-fitted transparent signal baseline and the train-prior constant baseline on those same
512 record IDs with:

```bash
robot-observe baseline \
  --prepared-root data/prepared/v1 \
  --output-root artifacts/baseline/signal-features-512 \
  --limit 512 --selection-seed 20260912 \
  --wandb-project robot-observability
```

The output directory contains separate deterministic-signal and trivial-prior metrics plus the locked
record manifest. It refuses to overwrite an existing result.

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

Keep vLLM separate so its Torch/CUDA dependency resolution cannot alter the training environment:

```bash
uv venv .vlm-venv
uv pip install --python .vlm-venv/bin/python 'vllm>=0.11,<1' qwen-vl-utils==0.0.14
.vlm-venv/bin/vllm serve Qwen/Qwen3-VL-4B-Instruct --port 8000 --gpu-memory-utilization 0.70
```

Run `scripts/eval_plot_vlm.py` only when OpenTSLM training is not occupying the H100. Preserve the
existing zero-shot artifacts and put one-shot results in a new directory:

```bash
.venv/bin/python scripts/eval_plot_vlm.py \
  --prepared-root data/prepared/v1 \
  --output artifacts/evaluation/qwen3-vl-one-shot \
  --shots 1
```

## 1D CNN GPU run

The CNN is intentionally separate from the OpenTSLM trainer but consumes the identical prepared
split. On a fresh single-GPU instance, clone or copy the repository and prepared dataset, then run:

```bash
cd /home/<username>/ysamet/model_training
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -e '.[train,dev]'
.venv/bin/python -m pytest
nvidia-smi
tmux new-session -d -s cnn-1d \
  '.venv/bin/python scripts/train_cnn.py --prepared-root data/prepared/v1 --run-name cnn-1d-seed-20260912 2>&1 | tee runs/cnn-1d-seed-20260912.stdout.log'
```

The default command refuses CPU training and refuses to overwrite an existing run. Monitor
`runs/cnn-1d-seed-20260912/status.json`, `metrics.jsonl`, W&B, and `nvidia-smi`. The checkpoint is
chosen on validation semantics macro-F1 with onset MAE as the tie-breaker; test is evaluated only
after model selection.

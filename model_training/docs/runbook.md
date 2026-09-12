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

Run `scripts/eval_plot_vlm.py` only when OpenTSLM training is not occupying the H100.

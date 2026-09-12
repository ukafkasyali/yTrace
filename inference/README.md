# Trace → OpenTSLM on Nebius

Status (12 September 2026): deployed privately on the team's Nebius H100 VM in
`/home/samet/trace-inference`, using Python 3.12 and an isolated virtual environment.
CUDA and OpenTSLM imports pass; all 16 bridge/input tests pass on the VM. The SSH
tunnel and Vite API proxy reach the real service. **Model loading is blocked by
Hugging Face access to `meta-llama/Llama-3.2-1B`; no real model answer has passed the
smoke test yet.** The service reports unavailable and does not fabricate results.

A training endpoint does not serve predictions automatically: this separate process
loads an exported checkpoint once, then handles on-demand requests from Trace.

### Resume this deployment

The VM is `samet@89.169.110.3`. After your Hugging Face account has access to the
base model, authenticate privately (do not paste tokens into chat or source code):

```bash
ssh -t samet@89.169.110.3 '~/trace-inference/inference/.venv/bin/hf auth login'
```

The process is in the `trace-inference` tmux session. After authentication, restart
that process only; the inference service does not automatically reload failed
weights. From inside the VM:

```bash
tmux respawn-pane -k -t trace-inference 'cd /home/samet/trace-inference && TRACE_DEVICE=cuda inference/.venv/bin/python -m inference.server > inference/server.log 2>&1'
curl http://127.0.0.1:8000/api/health
```

Wait for `ready: true` before running the smoke test. The local frontend now uses
`VITE_API_BASE_URL=/api` in gitignored `.env.local`; reconnect the SSH tunnel below
if the local session ends. This VM has no passwordless sudo; its environment was
created with the official user-local `uv` installer without changing system Python:

```bash
~/.local/bin/uv venv --python /usr/bin/python3 ~/trace-inference/inference/.venv
~/.local/bin/uv pip install --python ~/trace-inference/inference/.venv/bin/python -r ~/trace-inference/inference/requirements.txt
```

Do not recreate the existing environment just to restart the service.

## 1. Find the machine and checkpoint

In the Nebius console, choose your team's project, then **Compute → Virtual
machines**. Open a running VM and inspect its GPU type/count, public IP and SSH
connection instructions. If the list is empty, check other projects/regions.
An allocated training job is not necessarily a persistent VM you can SSH into.

Ask Atakan for:

> Which architecture are you training (OpenTSLM-SP or Flamingo), which base LLM,
> and where is the exported checkpoint? Please share the training config, including
> normalization, channel order, sampling/window length, prompt template and LoRA
> settings. Is there a GPU VM we can use for inference without competing with training?

The first runtime supports **OpenTSLM-SP** with strict encoder/projector loading
and explicit LoRA settings. The initial config uses the official
`OpenTSLM/llama-3.2-1b-tsqa-sp` checkpoint. It was trained on TSQA, **not KUKA**;
seven robot channels and this prompt are out-of-domain. A plausible answer is
not a model-quality result. Keep local numerical calculations as the factual check.

For Flamingo or a modified training fork, share the config first: upstream uses a
different state format and permissive checkpoint loading. This service deliberately
fails on an unsupported architecture instead of silently serving partial weights.

## 2. Prepare one GPU VM

If no suitable VM exists, create a plain GPU VM in Nebius with an Ubuntu CUDA
image and your **public** SSH key. One GPU is the starting point; check the available
GPU's price, memory and quota before creation. A GPU cluster/Kubernetes is unnecessary
for this single-process smoke test. Use persistent storage for model caches and
checkpoints, and enough free disk for PyTorch/CUDA dependencies and weights (100 GiB
is a practical starting allocation, not a measured minimum).

Prefer a separate GPU or run after training pauses; two processes can exhaust VRAM.
From your Mac, test the connection using the username from the VM configuration:

```bash
ssh YOUR_USER@YOUR_VM_IP
nvidia-smi
python3 --version
```

These commands assume Python 3.12 on the VM. The supplied requirements pin the
upstream source and core ML dependencies; actual CUDA execution still needs its
first GPU smoke test. If necessary on Ubuntu:

```bash
sudo apt-get update
sudo apt-get install -y python3-venv git tmux
```

## 3. Copy this isolated service

From the repository root on your Mac (this also works before committing):

```bash
ssh YOUR_USER@YOUR_VM_IP 'mkdir -p ~/trace-inference/inference ~/trace-inference/frontend/public/data'
scp inference/*.py inference/requirements.txt inference/smoke.config.json YOUR_USER@YOUR_VM_IP:~/trace-inference/inference/
scp frontend/public/data/kuka-demo.json YOUR_USER@YOUR_VM_IP:~/trace-inference/frontend/public/data/
```

On the VM:

```bash
cd ~/trace-inference
python3 -m venv inference/.venv
source inference/.venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r inference/requirements.txt
python -m pip check
python -m pip freeze > inference/requirements.resolved.txt
python -c 'import torch; print(torch.__version__); print(torch.cuda.is_available())'
hf auth login
```

The Llama backbone may require your Hugging Face account to have accepted its
license/access terms. Authenticate on the VM interactively; never put its token in
the frontend, a URL, a screenshot or a committed config. Use only checkpoints from
the official publisher or your team's trusted training process.

Start a persistent terminal session, then the server:

```bash
tmux new -s trace-inference
cd ~/trace-inference
source inference/.venv/bin/activate
TRACE_DEVICE=cuda python -m inference.server 2>&1 | tee inference/server.log
```

Detach with Ctrl+B, then D. The server binds to **127.0.0.1:8000** by default. Keep
it private; this development bridge is not an authenticated public API. It exposes
readiness at `/api/health` and actual availability at `/api/models` while weights
load. No successful placeholder response is substituted on failure.

## 4. Connect Trace and run the real smoke test

On your Mac, keep this tunnel running:

```bash
ssh -N -L 127.0.0.1:8000:127.0.0.1:8000 YOUR_USER@YOUR_VM_IP
```

From the repository root, in another terminal:

```bash
curl http://127.0.0.1:8000/api/health
python3 -m inference.smoke
```

The smoke command fails unless it gets a completed answer, checkpoint identity,
the exact selected evidence window and a receipt for **1,024 samples per channel**.
It saves `inference/smoke-result.json` (gitignored). Server logs also record an input
hash. This checks the application path and interval handling; it is not an independent
GPU attestation or an accuracy benchmark.

Then restart Vite with connected services enabled:

```bash
cd frontend
VITE_API_BASE_URL=/api npm run dev -- --port 5174
```

The existing `/api` development proxy targets the local SSH tunnel. Alternatively
set `VITE_API_BASE_URL=/api` in `frontend/.env.local`, then restart Vite. For a
deployed frontend, configure a same-origin reverse proxy; Vite's dev proxy is not
included in a production build.

Open Trace, keep the initial interval **[5.787, 6.811) seconds** with the playhead at
8 seconds, and open **Models**. Run available models; only OpenTSLM is exposed by
this service. Use **Refresh status** in Models after model loading finishes.
The assistant's local numerical mode still works. Assistant orchestration, CNN,
direct text LLM, ingestion and search are intentionally unavailable from this bridge.

## 5. Switch to the team's checkpoint

Copy `smoke.config.json` to a gitignored `inference/team.local.json`. Set:

```json
{
  "architecture": "sp",
  "model_id": "team-kuka-sp",
  "base_model": "meta-llama/Llama-3.2-1B",
  "checkpoint_path": "/absolute/persistent/path/to/exported-checkpoint.pt",
  "normalization": "zscore_sample",
  "max_new_tokens": 128
}
```

This example is **not** a guess at your team's training configuration. Confirm every
field and align `prepare_sample()` with its prompt and preprocessing before using
the trained model. Supported normalization options are `zscore_sample` (per channel,
sample standard deviation; same numerical convention as upstream TSQA) and `none`.
If training used LoRA, add `lora` with the exact `lora_r`, `lora_alpha`,
`lora_dropout` and `target_modules` passed to upstream `enable_lora()`.

```bash
TRACE_MODEL_CONFIG=inference/team.local.json TRACE_DEVICE=cuda python -m inference.server
```

Stop the previous server before starting the replacement. Re-run the smoke test,
then evaluate held-out recordings against the CNN and numerical baseline.

## Boundaries and validation

- Queries retrieve signals server-side by recording/window/channel IDs. Annotation
  labels never enter model input; each request remains fixed if playback changes.
- This bundled fixture contains raw 1 kHz data only in **[4, 9) seconds**. Inference
  rejects other ranges and windows over two seconds. It does not feed the reduced
  100 Hz overview to the model. Full recording inference needs the ingestion/data
  team to supply raw windows behind the same API.
- No resampling: the selected channels are normalized individually and zero-padded
  to a multiple of four for the upstream encoder. The initial interval needs no
  padding. Ordering follows the submitted channel IDs.
- One query at a time. Cancellation requests stop generation at the next decoding
  step; the slot stays occupied until the model call returns. A GPU operation already
  running cannot be forcibly interrupted by HTTP cancellation. The 120-second
  stopping criterion also acts between decoding steps, not as a process watchdog.
- Evidence links identify **input telemetry**, not verified causal explanations.
  Checkpoint SHA256, configuration hash and resolved backbone revision accompany
  model identity; an input hash/sample count is included in the completion receipt.
- CPU-only tests use an explicitly injected test double and do not establish that
  the checkpoint runs on CUDA. Run from the repository root:

```bash
python3 -m unittest discover -s inference -p 'test_*.py' -v
```

Official references: [Nebius VM quickstart](https://docs.nebius.com/compute/quickstart),
[Nebius SSH connection](https://docs.nebius.com/compute/virtual-machines/connect),
[OpenTSLM source](https://github.com/OpenTSLM/OpenTSLM/tree/2968f4b891baab4307f7e9d0043e87677b593a30),
[TSQA checkpoint](https://huggingface.co/OpenTSLM/llama-3.2-1b-tsqa-sp).

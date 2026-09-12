# Trace Agent Guide

This repository is the team submission for the Aionic Labs × Agentic Systems Lab
Temporal AI Challenge. The product is **Trace**, a replay-only robot observability
workbench for investigating recorded KUKA LWR4+ torque events with OpenTSLM.

## Product contract

Keep the project centered on this user problem:

> Robot operators and maintenance engineers must inspect several synchronized
> torque signals to understand a recorded contact event. Trace lets them replay
> the event, ask what happened, and inspect an OpenTSLM interpretation linked to
> the responsible joints and time interval.

The core demonstration is:

```text
Replay real telemetry -> open/select an event -> run measurements + OpenTSLM
-> read the interpretation -> inspect its signal evidence
```

This is retrospective observability. Do not describe it as a causal collision
detector, safety controller, live-monitoring system, or verified physical diagnosis.
Publisher event markers are annotations. Measurements are deterministic calculations.
OpenTSLM outputs are generated predictions. Keep these three sources distinct.

Do not add another orchestration LLM unless a concrete requirement cannot be handled
by the current deterministic request router and the value is demonstrated. CNN,
Direct LLM, and other models belong in evaluation until they have real endpoints and
credible results. The 3D robot is optional context and is not reconstructed from this
dataset: the source data contains external joint torque, not complete joint-position
trajectories.

## Challenge alignment

The downloaded source brief is
`/Users/sametdegirmenci/Downloads/Aionic_Temporal_AI_Hackathon.pdf`. It requires:

1. A useful problem and open dataset.
2. A TimeNet connector for signals, metadata, and annotations.
3. TSLM training plus a baseline comparison on held-out data without leakage.
4. A working demo showing real input, model output, evidence, benefit, and limitations.

The submission also needs code/training configuration, a checkpoint or adapter,
dataset documentation, and a short evaluation with a baseline comparison. Agentic
dataset sourcing is a bonus rather than a prerequisite.

Current alignment:

- **Problem/data:** KUKA collision-event investigation using open seven-channel,
  1 kHz external-joint-torque recordings.
- **TimeNet:** the KUKA connector and deterministic validation are merged on `main`.
- **TSLM:** OpenTSLM SoftPrompt with Llama 3.2 1B and HAR warm start is trained and
  deployed privately on Nebius.
- **Demo:** the React workbench replays real telemetry and links readable model
  interpretations to evidence.
- **Submission proof:** an audited held-out comparison, clean TimeNet receipt,
  dataset limitations and demo script are packaged in `docs/submission/`.
- **Remaining:** measured user benefit and the challenge checkpoint/adapter handoff.

## Repository map

- `frontend/`: React + TypeScript + Vite replay workbench.
- `data_ingestion/`: deterministic source profiler and KUKA-to-TimeF connector.
- `model_training/`: dataset preparation, leakage-safe splits, baselines, OpenTSLM
  training, evaluation, and remote runbooks.
- `inference/`: private HTTP bridge, checkpoint release validation, trained prompt
  contracts, readable result presentation, and smoke tests.
- `docs/FRONTEND_INTEGRATION.md`: frontend/backend adapter contracts.
- `HUMAN_MAPPING_DECISIONS.md`: reviewed semantic mappings for TimeNet.

Read the component README before changing its contract. Prefer additions inside the
owning module and do not couple frontend work to teammates' training internals.

## Verified state on 12 September 2026

`main` includes commit `5e9094a`, which aligns inference prompts with the training
contracts and converts structured model generations into readable operator answers.
The latest promoted checkpoint is the evaluated canary-v4 artifact with SHA-256:

```text
8ff63b84ae5b64758f66b3e0527f4f225a04bb2b6b39193c806f58d94cec9f23
```

It is served from `/home/samet/trace-inference` on the private Nebius VM. The server
loads one immutable checkpoint at startup; it intentionally does not adopt the newest
file automatically. Promotion is evaluation -> safe release -> restart -> smoke test
-> rollback on failure. See `inference/README.md` for the current tunnel, tmux, release,
and smoke commands. Never commit Hugging Face tokens or expose the development server
publicly.

Recorded evaluation for canary-v4:

- Contact F1: 0.9871
- Interaction-semantics macro F1: 0.8845
- Strongest-joint accuracy: 0.7528
- Affected-joint set F1: 0.8946
- Onset MAE: 68.51 ms
- Structured-output validity: 0.7793

Treat schema validity and fine temporal accuracy as explicit limitations. Zero-signal
and perturbation checks show a material dependence on telemetry, but they do not
replace a baseline comparison.

The bundled frontend fixture has full raw 1 kHz samples only for `[4, 9)` seconds of
one recording. Events outside that window can be displayed from reduced overview
data but cannot be claimed as equivalent raw-model inference. More recordings require
the data adapter to serve their raw windows through the existing integration contract.

## Submission evidence update (12 September 2026)

The audited comparison is now in `docs/submission/evaluation/report/comparison.md`.
Original predictions, targets, manifests and checksums are archived; rerun the dependency-free
`robot_observability.comparison` module to verify all 512 windows from 67 held-out recordings.
Signal-feature semantics macro-F1 is 0.9880 versus OpenTSLM 0.8845 and zero-shot Qwen plots
0.6951. OpenTSLM usable-summary rate is 0.7793. Positive-contact F1 alone hides missing
free-motion answers; use contact macro-F1 and answer coverage alongside it. Do not claim
OpenTSLM superiority. Qwen often emitted null contact values; plain Llama was not evaluated.

Both TimeNet parts passed a fresh build/load/raw-array validation for one original full recording
per part. See `docs/submission/timenet/`. This is not a full-corpus rebuild or proof that the
existing training run consumed those new TimeF artifacts.

The frontend now exports investigations with separate annotations, measurements and predictions,
plus available model/input receipts. The live checkpoint and browser export were verified.
The demo recording has measured joint articulation from separately validated position data;
geometry and global frame remain schematic. Torque alone does not reconstruct pose.

## Highest-priority next work

1. Run the prepared timed engineer pilot in `docs/submission/PILOT.md`; reduced debugging time
   is still a hypothesis. Do not invent participants, user quotes, results or ROI.
2. Complete the challenge's checkpoint/adapter handoff through an approved submission channel;
   weights are private on the VM, and the live service currently reports an unknown backbone revision.
3. Improve model output reliability on validation, then reserve new recording groups for confirmation.
   The current test set has already been inspected; do not optimize against it and call it untouched.
4. Expand raw-window access through the existing adapter before adding cross-run incident retrieval.
5. Integrate the dataset-scout PR through its existing contracts, without duplicating sourcing or
   the newly merged bounded documentation search. Add runtime RAG only for a demonstrated need.

The submission index, two-minute demo, verification and limitations are in `docs/submission/`.
Do not prioritize live telemetry, robot control, exact 3D reconstruction or more chat models.

## Repository setup checks

Before doing repository work in Codex, verify that Entire is active for the current worktree:

```bash
entire doctor
```

The expected Codex-related output is:

```text
✓ Codex hooks: ACTIVE
✓ Codex hook approval records: PRESENT
```

If `entire doctor` reports missing or untrusted Codex hooks, run:

```bash
entire enable --agent codex
```

Then open `/hooks` in Codex and approve the Entire hooks. Rerun `entire doctor` before continuing.

At the start of a new conversation:

```bash
entire doctor
git status --short
git fetch origin main
git log --oneline --decorate -5
```

Do not reset, delete, or overwrite work you did not create. The local directories
`.cursor/`, `.factory/`, `.gemini/`, `.github/`, `.opencode/`, and `.pi/` may appear
untracked as tool configuration; do not include them in product commits unless the
user explicitly asks for them.

Before pushing, sync with `origin/main`, preserve teammate changes, run the checks for
the modules touched, and stage only intended files. Entire checkpoints should remain
active for commits and pushes.

Useful checks:

```bash
cd frontend && npm test && npm run build
cd ../data_ingestion && PYTHONPATH=src python -m unittest discover -s tests -v
cd .. && python3 -m unittest discover -s inference -p 'test_*.py' -v
```

Inference server tests bind temporary localhost ports and may need sandbox approval.
Training has optional heavy dependencies; run the focused tests available in the
active environment and report any dependency-based collection limitation precisely.

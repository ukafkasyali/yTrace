# y/trace agent guide

y/trace is a replay-only robot observability workbench built for the Aionic Labs × Agentic Systems Lab Temporal AI Challenge. Team y/agent placed second out of 11 teams at the Zurich qualifier and advanced to the Munich finals.

## Product contract

Keep the project centered on this user problem:

> Robot operators and maintenance engineers must inspect several synchronized torque signals to understand a recorded contact event. y/trace lets them replay the event, ask what happened, and inspect an OpenTSLM interpretation linked to the responsible joints and time interval.

The core flow is:

~~~text
Replay real telemetry
  -> select an event
  -> run measurements and OpenTSLM
  -> inspect the interpretation and exact signal evidence
  -> compare another window
  -> export a reviewable handoff
~~~

This is retrospective observability. Do not describe it as a live safety controller, verified root-cause system, exact collision localizer, or production collision-prevention system.

Keep these sources distinct:

- Publisher event markers are dataset annotations.
- Measurements are deterministic calculations.
- OpenTSLM outputs are generated predictions.

The 3D robot provides context. It uses separately recorded PosMsr joint angles and schematic body geometry. Torque does not reconstruct pose or contact location. Missing position data must use an explicitly labeled fixed pose.

## Repository map

- frontend: React, TypeScript, Vite, Three.js replay workbench
- data_sourcing: LangGraph dataset discovery, native verification, ranking, and approval
- data_ingestion: acquisition, structural inspection, semantic mapping, and TimeNet conversion
- model_training: dataset preparation, recording-grouped splits, baselines, OpenTSLM training, and evaluation
- inference: strict request validation, checkpoint serving, release checks, and demo fixtures
- docs/submission: archived challenge evidence, evaluation artifacts, TimeNet receipts, and demo material

Read the owning module README before changing its contract. Keep additions within the owning module when possible.

## Model and evaluation facts

The primary model input is seven canonical external-joint-torque channels with 1,024 contiguous samples each at 1 kHz. Windows are 1.024 seconds and half-open. Normalization uses training-only robust statistics. Publisher markers are never passed to the model.

The promoted canary-v4 checkpoint SHA-256 is:

~~~text
8ff63b84ae5b64758f66b3e0527f4f225a04bb2b6b39193c806f58d94cec9f23
~~~

The model is OpenTSLM SoftPrompt with a Llama 3.2 1B backbone, HAR warm start, and LoRA adapters. The live service loads one immutable checkpoint at startup. A new checkpoint is not promoted automatically.

The audited held-out comparison uses 512 windows from 67 recording groups:

- Signal-feature semantics macro-F1: 0.9880
- 1D CNN semantics macro-F1: 0.9751
- OpenTSLM canary-v4 semantics macro-F1: 0.8845
- Qwen3-VL zero-shot plot semantics macro-F1: 0.6951
- OpenTSLM usable complete answer rate: 0.7793

Do not claim OpenTSLM beats the dedicated classifiers. Its contribution is a readable investigation object spanning event semantics, timing, affected joints, and evidence text.

The archived test set and demo cases have already been inspected. Use validation for further model development and reserve new recording groups for confirmatory claims.

## Data and replay facts

The primary open source contains KUKA LWR4+ accidental-collision and intentional-contact recordings with seven 1 kHz external-joint-torque channels.

The bundled frontend has:

- a 100 Hz overview of the original recording
- raw 1 kHz samples only for the documented detail interval
- additional backend-served collision, intentional-contact, and annotation-free examples
- per-recording position fixtures where measured PosMsr data was validated

Raw model requests must contain seven ordered channels and exactly 1,024 contiguous samples. Never silently resample, pad, borrow another recording's pose, or substitute overview data for model input.

Cross-window and cross-run similarity is a deterministic signal-pattern comparison. It does not establish a shared physical cause or verified normal motion.

## Dataset sourcing and ingestion

The sourcing agent may use Tavily for bounded discovery and an OpenAI-compatible model for planning or grounded semantic checks. Dataset approval always requires source-native evidence and a human decision.

Approved manifests pin provider identity and revision. Ingestion resolves those manifests server-side, downloads only selected assets, inspects content without executing source code, and either maps supported time-series formats or pauses when semantics are ambiguous.

TimeNet and TimeF receipts must preserve signal identities, time axes, units, annotations, and source provenance. Do not pass sourcing evidence or publisher annotations into OpenTSLM prompts.

## Claims and limitations

The repository supports the claim that y/trace creates an evidence-linked incident investigation from real recorded telemetry.

It does not yet establish:

- measured reduction in engineering time
- production safety or reliability
- generalization beyond the evaluated robot and dataset
- verified physical root cause
- calibrated maintenance recommendations

The prepared pilot remains the correct way to test the time-saving hypothesis. Do not invent participants, timings, testimonials, or ROI.

## Repository workflow

Before editing:

~~~bash
entire doctor
git status --short
git fetch origin main
git log --oneline --decorate -5
~~~

Expected Entire output includes installed Codex hooks and present approval records. If hooks are missing, run:

~~~bash
entire enable --agent codex
~~~

Preserve teammate work. Do not reset, overwrite, stage, or delete changes you did not create.

Local tool directories such as .cursor, .factory, .gemini, .github, .opencode, and .pi may be untracked. Do not include them in product commits unless the user explicitly asks.

Before pushing, sync with origin/main, stage only intended files, and run checks for every touched module. Entire checkpoints should remain active.

Useful checks:

~~~bash
cd frontend && npm test && npm run build
cd ../data_sourcing && .venv/bin/pytest -q
cd ../data_ingestion && PYTHONPATH=src python -m pytest tests -q
cd .. && python3 -m unittest discover -s inference -p 'test_*.py' -v
~~~

Training has optional heavy dependencies. Run focused tests available in the active environment and report dependency limitations precisely.

## Ongoing priorities

1. Measure investigation quality and completion time against chart-only review.
2. Improve structured-output reliability on validation data.
3. Expand exact raw-window coverage through the existing adapter.
4. Test generalization on a new robot or manufacturing dataset without weakening the current evidence contract.
5. Keep the portfolio README, reproducible evaluation, and Munich demo path current.

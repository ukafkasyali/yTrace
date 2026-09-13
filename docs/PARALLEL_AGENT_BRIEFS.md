# y/trace: parallel agent onboarding

Use a separate task and git worktree for each editing agent. Paste the shared
introduction followed by **one** workstream prompt. The current coordinating task
continues the frontend simplification. These are prompts to start tasks yourself;
writing this file does not launch agents.

## Shared introduction — paste into every task

```text
You are contributing to y/trace, the team's Aionic Labs × Agentic Systems Lab Temporal
AI Challenge submission. Work autonomously within the assigned workstream. First
read AGENTS.md and docs/PARALLEL_AGENT_BRIEFS.md. Run entire doctor, inspect git status,
fetch origin/main, and inspect recent commits and open PRs. Keep Entire active.
Preserve existing teammate and agent work. Use a separate codex/ branch/worktree;
do not make parallel edits in the coordinator's dirty checkout.

Gather context across the whole product before editing:
- frontend/README.md and docs/FRONTEND_INTEGRATION.md for the user flow and API.
- inference/README.md for serving, input shape, normalization and checkpoint identity.
- model_training/README.md for data preparation, splits, baselines and training.
- data_ingestion/README.md and HUMAN_MAPPING_DECISIONS.md for TimeNet and source mapping.
- docs/submission/QWEN_BASELINE.md for the exact verified plot-VLM prompt and input.
- docs/submission/README.md, docs/submission/evaluation/report/comparison.md,
  docs/submission/PILOT.md and docs/submission/UI_WALKTHROUGH.md for evidence and limits.
Read additional component instructions before changing that component's contract.
Verify the files and deployment state rather than assuming these notes are current.

Product: retrospective investigation of recorded KUKA joint-torque contact events.
The flow is replay -> select interval -> measurements + OpenTSLM -> interpretation
-> inspect evidence -> export investigation. Publisher annotations, deterministic
measurements and generated predictions are different sources. The value hypothesis
is less engineer debugging time; no user study has established the saving yet.
An AI classifier alone is not the product advantage: test whether the investigation
workflow helps an engineer find and verify useful evidence.

The deployed model is canary-v4, immutable at process startup, SHA-256
8ff63b84ae5b64758f66b3e0527f4f225a04bb2b6b39193c806f58d94cec9f23.
It uses seven canonical joints, 1024 contiguous raw samples each, 1 kHz, a 1.024 s
half-open interval, and training-only robust normalization. Do not substitute
reduced overview data, silently resample, pad incomplete windows or change the
checkpoint automatically. Two API modes do not mean two independent models.

The archived comparison uses 512 windows from 67 held-out recordings. Signal-feature
semantics macro F1 .9880 exceeds OpenTSLM .8845; usable OpenTSLM summary rate is .7793.
Do not claim model superiority. Test data and demo cases have already been inspected;
use validation for development and new recording groups for fresh confirmation.
Keep archived results immutable. No live robot control, verified root-cause diagnosis,
physical collision prevention, invented exact 3D motion, or extra orchestration LLM
without a concrete demonstrated need.

The coordinator owns the active frontend/3D/replay redesign. Do not edit frontend/
or its walkthrough from another workstream. Respect the ownership in your task.
Inspect other agents' PRs read-only for compatibility. Put proposed cross-module
contract changes into your handoff instead of silently editing outside your scope.
Do not interrupt shared GPU jobs or restart/promote the private inference service.
Do not expose the service publicly, commit credentials, or publish/send private
weights to a new destination.

Before editing, briefly state the concrete problem, current end-to-end behavior,
and challenge requirement your work advances. Implement one bounded useful result,
run focused checks, inspect the diff, commit only your intended files, and open a
focused draft PR against current main. Do not merge or deploy it from the worker
workstream; the coordinator integrates changes in sequence. End with the outcome,
measured evidence, exact commands/artifacts, limitations, changed contracts, PR link,
and next action. Clearly separate completed work from proposals and blocked work.
```

## Agent 1 — model reliability and evaluation

Owns `model_training/`. Read `inference/runtime.py` for compatibility, but propose
runtime changes in the handoff instead of editing the data agent's serving path.

```text
Your workstream is OpenTSLM output reliability. Inspect the latest training commits,
existing validation artifacts and run state before choosing work; avoid duplicating
an active teammate experiment. Identify the highest-value remaining cause of invalid
or unusable structured outputs. Preserve the seven-joint/1024-sample input contract.

Implement and verify a bounded validation-only improvement or evaluation that
measures usable-answer coverage alongside contact, semantics, joint and timing
quality. Compare against the current validation baseline on identical recording
groups; record configuration, seeds, split identities and artifact hashes. Count
invalid and missing answers explicitly. Do not repair a generation into an invented
correct answer. Do not train or select hyperparameters against the test set or the
three UI examples. Preserve archived submission metrics.

Start with existing artifacts and lightweight tests. Before any GPU work inspect
capacity and active jobs; do not provision resources, kill jobs or launch a competing
training run. If capacity is unavailable, finish the reproducible evaluation/config
and document the precise run command instead of claiming an experiment happened.
Do not promote a new checkpoint. Deliver evidence and a candidate recommendation
that the coordinator can evaluate for a separate controlled release.
```

## Agent 2 — raw recordings and adapter coverage

Owns `data_ingestion/` and, only if needed, recording catalogue/data endpoints in
`inference/server.py` and their tests. Does not own model runtime, frontend or releases.

```text
Your workstream is raw-data coverage through the existing adapter. Inspect recent
TimeNet and semantic-validator PRs first; preserve their merged behavior. Determine
how to serve exact historical 1-kHz windows beyond the small demo excerpts without
loading all recordings into browser memory or weakening the inference input contract.

Implement one reproducible build/load -> recording metadata/annotations -> raw-window
path using available authorized source data. Preserve recording identity, timebase,
seven-channel order, units and half-open intervals. Verify requested values against
original raw arrays and reject missing/gapped input explicitly. Reduced overviews
must remain clearly marked as display data. Source annotations must not enter model
prompts. Preserve compatibility with the current demo-cases, replay, signals/events,
query and input-receipt contracts; propose any necessary schema extension explicitly.

Do not duplicate the dataset-scout or semantic-agent work, add broad web ingestion,
or fetch private teammate files without appropriate existing access/authorization.
Use local fixtures and clean environment validation where possible. Deliver focused
adapter tests, source/artifact hashes, a reproducible command and a draft PR. Do not
deploy or restart the shared service; provide deployment and rollback instructions
for the coordinator after compatibility review.
```

## Agent 3 — business evidence and submission

Owns `docs/submission/` except `UI_WALKTHROUGH.md` and existing raw evaluation/receipt
artifacts. Read implementation freely; do not edit frontend or model code.

```text
Your workstream is the challenge submission and the business case. Audit the actual
challenge brief against the repository's current evidence. Build a concise gap list
where each requirement links to a real artifact or reproducible command. Distinguish
repository presence, test verification, deployed behavior and demonstrated user value.

Sharpen a two-minute story: engineer faces a recorded contact event -> locates the
interval -> compares generated interpretation with measured signals -> inspects
responsible joints -> exports evidence. Explain what y/trace contributes beyond a
standalone classifier and what is still unproven. Keep the model-vs-feature-baseline
comparison honest; use only the existing audited metrics, not an invented advantage.

Make the existing timed engineer pilot runnable: concrete task, comparison condition,
success criteria, timing/error capture sheet and interpretation rules. Do not invent
participants or results, contact people, or claim debugging-time savings. Prepare a
reviewable submission/checkpoint handoff checklist with precise missing metadata,
including the reported unknown backbone revision. Do not upload weights, send messages,
or submit externally. Deliver useful local submission documents and a draft PR.
Coordinate wording with the frontend workstream through a short handoff; its current
walkthrough is being changed, so do not independently rewrite it.
```

## Optional agent 4 — independent product critique (read-only)

This can be given to another model. No model-specific capabilities are assumed.

```text
Review y/trace as a robotics engineer and a skeptical challenge judge. Read the shared
context, then actually use the current UI if computer use is available. Do not edit
files, run inference in parallel with another tester, or redesign independently.
The coordinator is actively changing the UI; state the commit/build you reviewed.

Try to answer: What happened? Which interval and joints support the answer? What is
measured, annotated or predicted? Can I inspect the robot and signals without losing
my investigation? What can I export? What does this save compared with raw plots and
a standalone AI answer? Identify any unsupported claim or misleading physical cue.

Return at most five prioritized findings, each with a reproduction, user consequence,
and smallest proposed correction. Separate observed defects from taste and hypotheses.
Do not praise aesthetics without showing task success. Review an actual saved
investigation, including model identity, input count and evidence. Do not treat a
three-case demonstration as a model-quality benchmark. Finish with the strongest
reason this product could be useful and the most important missing proof.
```

## Integration handoff

Workers report: branch/PR and base SHA; owned files changed; outcome; verification
commands and results; artifacts; API/config compatibility; limitations; dependencies;
any deployment or checkpoint action still required. One coordinator reviews and
integrates PRs, reruns checks for affected boundaries, and serializes live inference
verification. An independent critique is useful after a concrete UI build is ready.

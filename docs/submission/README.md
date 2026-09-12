# Trace: evidence-backed robot contact-event investigation

Trace helps a robotics engineer investigate a recorded contact event, inspect synchronized torque measurements and a trained OpenTSLM interpretation, and export the evidence for a teammate. Its immediate business hypothesis is less time spent assembling a correct incident report. That time saving has **not yet been measured**.

## Challenge audit and evidence

The challenge asks for a useful problem and open data, a TimeNet connector, TSLM
training with a held-out baseline, and a working demonstration. It also requests
code/configuration, a checkpoint or adapter, dataset documentation and a short
evaluation. The table distinguishes what is in Git, what was verified, what was
observed in the private deployment, and what still needs evidence.

| Requirement | Repository artifact | Verified / observed evidence | Remaining gap |
|---|---|---|---|
| Useful problem and open data | [Dataset card](../../model_training/docs/dataset.md) | Open KUKA accidental-contact, intentional-contact and free-motion recordings, source links and confounds are documented | One robot and class/implement confounding limit generalization |
| TimeNet connector | [Connector](../../data_ingestion/README.md), [clean verification](timenet/README.md) | Two source parts build, load and match original arrays for one full recording each | This is not a full-corpus rebuild or proof of the historical training input |
| TSLM training and configuration | [Config](../../model_training/configs/opentslm_sp.yaml), [trainer](../../model_training/src/robot_observability/train_opentslm.py) | OpenTSLM SoftPrompt, Llama 3.2 1B and HAR warm start are recorded | Do not select further changes against the already-inspected test archive |
| Held-out baseline comparison | [Audited report](evaluation/report/comparison.md), [results](evaluation/report/comparison.json), [source inventory](evaluation/source/inventory.json) | 512 identical windows from 67 held-out recording groups, checksums and uncertainty are reproducible | A new recording group is needed for a future confirmatory claim |
| Working evidence-linked demo | [Demo script](DEMO.md), [smoke receipt](demo/smoke-result.json), [exported investigation](demo/investigation-example.json) | Private canary-v4 path was smoke-tested; export retains telemetry receipt, measurements and prediction separately | The three fixed examples are not a quality benchmark; raw coverage remains limited |
| Checkpoint or adapter handoff | [Release procedure](../../inference/README.md), [handoff checklist](CHECKPOINT_HANDOFF.md) | Service reports canary-v4 checkpoint digest `8ff63b84ae5b64758f66b3e0527f4f225a04bb2b6b39193c806f58d94cec9f23` | Approved submission-channel delivery is still required; live backbone revision is `unknown` |
| Benefit for engineers | [Pilot protocol](PILOT.md), [facilitator runbook](PILOT_RUNBOOK.md) | Protocol, task materials and empty capture sheet are ready | No participants, timings, error rates, testimonials or ROI have been collected |

## What the evaluation actually says

The trained signal-feature baseline has semantics macro-F1 **0.9880**, versus **0.8845** for OpenTSLM and **0.6951** for zero-shot Qwen3-VL 4B plots. OpenTSLM has a usable complete summary on **77.93%** of cases. Its conditional onset MAE is **68.51 ms**, versus **70.73 ms** for features; its median and all-contact success within 50 ms are worse. The paired interval for the latter difference crosses zero.

The contribution is a reproducible temporal-language investigation workflow and an honest account of where it works. These results do not establish OpenTSLM superiority. Deterministic measurements remain the source of numeric facts; generated semantics remain predictions. The stronger learned signal baseline currently belongs to offline evaluation and is not silently substituted for OpenTSLM in the app.

The Qwen result is limited by an output-contract failure: 508/512 responses supplied no boolean contact answer. Matching JSON keys alone concealed this. Report it as the observed behavior of this prompt/model configuration, not a general statement about vision-language models. Plain Llama was not benchmarked; the ready comparable model is Qwen. A future Llama experiment must specify numerical serialization, context budget and training regime.

The archived checkpoint is `8ff63b84ae5b64758f66b3e0527f4f225a04bb2b6b39193c806f58d94cec9f23`. It remains private on the team's VM under the release process. The weights are not included in Git; arrange the challenge's checkpoint/adapter handoff using the approved submission channel. The observed service reported `backbone:unknown`, so backbone revision provenance remains incomplete.

## Reproduce the comparison

Python standard library only; no GPU, model download, or private service needed:

```sh
PYTHONPATH=model_training/src python3 -m robot_observability.comparison \
  --source docs/submission/evaluation/source --output /tmp/trace-comparison
```

The compressed JSONL files preserve original bytes and include generated responses. The tool verifies every checksum, rejects duplicate/missing/mismatched records, confirms test-session membership, and resamples whole recordings for paired uncertainty. The zero-signal run is an ablation, not an independent baseline. Original legacy metrics remain archived; the submission report names its stricter metrics separately.

These test results were already inspected by the team. Further tuning must use validation; a new untouched recording-group test is needed for a confirmatory claim after changes.

## Team integration, checked 12 September 2026

- Main `126a8c6` includes PR #9's unified builder and PR #10's bounded documentation evidence. This work uses both existing dataset contracts and does not add another document-search backend.
- Open [PR #3, Add LangGraph evidence-complete dataset scout](https://github.com/ukafkasyali/ysamet/pull/3) is mergeable. It owns dataset discovery, requirement preflight, review and the Data source UI. It remains a separate reviewed integration; no duplicate scout or broad runtime web-search agent was added here.
- Training branch `b65bc22` contains the team's baseline and plot-evaluation improvements. The completed outputs were reused, rather than retraining or rewriting active training work. Its added fit-probe and benchmark orchestration code is not claimed to be merged by this submission work.
- The new comparison module and investigation export avoid the scout's edited frontend files, except for an additive style rule. The final integration check records compatibility in [verification](VERIFICATION.md).

## Boundaries

The demonstration is replay-only. Publisher markers do not prove physical causes or production safety stops. The dataset is one robot with experimental class/implement confounding and unknown subject identities. Strongest-joint labels and evidence intervals are derived signal targets. Measured joint articulation is available for the demo recording, but world pose and contact location are not reconstructed. Raw model input is available only within `[4,9)` seconds of the bundled recording; wider overview plots are reduced data.

## Next decision

Use the pilot to establish whether evidence navigation and export reduce correct-report time. Expand raw-window access through the existing adapter before claiming cross-run investigation. Evaluate output-contract changes on validation. Add incident retrieval or document RAG only for a demonstrated investigation question, and keep held-out answers out of retrieval.

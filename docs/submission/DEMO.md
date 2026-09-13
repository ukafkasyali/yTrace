# Two-minute demo

## Preparation

Follow `inference/README.md` to restore the private tunnel; do not restart training or change the served checkpoint. Configure `data_sourcing/.env` with the team's Tavily and OpenAI credentials, then start the integrated app with `./scripts/run-local.sh --live`. Run `python3 -m inference.smoke` and check the expected checkpoint digest. Complete one sourcing run to its review checkpoint before the presentation and keep its verified result available. Open the initial `[5.787,6.811)` second selection and run **Analyze interval** once before the presentation clock. Leave that result open. This recording is a development example, not the held-out accuracy demonstration.

Have the audited comparison and the committed [pre-run Markdown handoff](demo/investigation-example.md) open beside the app. If the live service is unavailable, explicitly present the committed smoke result as a previously recorded run; never present it as fresh inference.

Timing, measured 13 September 2026 on the current CPU-only deployment (two smoke runs through the SSH tunnel): one full analyze-interval round trip takes about **24 seconds**. Do not start another query during the timed demo. Say that the visible result is the current canary-v4 output for this exact 7×1,024 input. If a judge requests fresh inference, run it after the core story. Only one query is served at a time; do not queue clicks.

## Script and actions

| Time | Action | Suggested narration |
|---|---|---|
| 0:00–0:20 | Open **Data source** and show the pre-run verified result | “y/trace starts before training. An agent turns a dataset brief into requirements, searches with Tavily, verifies native evidence, and pauses for human approval. The approved revision becomes the input to our secure TimeNet ingestion path.” |
| 0:20–0:36 | Return to Replay and click **Replay interval · 0.5×** | “Now we investigate one recording from that open source. The marker is a publisher annotation, not a model detection. The robot uses separately recorded joint angles; its body and world frame remain schematic.” |
| 0:36–1:02 | Read the actual event class, strongest joint and onset, then point to **Cross-check** | “The model hypothesis and deterministic torque ranges stay separate. Different rankings tell the engineer which signals to inspect before handoff.” |
| 1:02–1:20 | Click **Inspect exact seven-channel input**, then return | “This is the model's complete input: seven raw 1 kHz channels, with the publisher label excluded. Our TimeNet connector preserves the original arrays.” |
| 1:20–1:34 | Click **Export incident handoff (.md)** | “This report carries the interval, measurements, model and input receipt to the next engineer.” |
| 1:34–1:50 | Briefly show the saved source or ingestion receipt if asked | “Every source and imported asset keeps its revision, license, content hash, and validation status. The agent cannot silently approve its own data.” |
| 1:50–2:00 | Open **Evaluation** | “On 512 held-out windows, simple features win classification. OpenTSLM contributes the readable handoff; its 77.93 percent usable-output rate stays visible as a limitation.” |

Do not memorize a generated answer as a guaranteed future result. Read the actual response. The model and measured quantities can disagree; that is a reason to inspect evidence. The exact archived live response is in `demo/smoke-result.json`.

## Jury questions

**Why use a TSLM when the feature baseline wins?** Use the feature baseline when the task is only a fixed label. y/trace is designed around a different benefit: one model output that carries event type, relative timing and joint attribution into a readable, evidence-linked investigation handoff. We have not yet measured whether that handoff benefit justifies the lower reliability. We do not claim the TSLM is necessary for calculating peaks, ranges or the fixed benchmark labels.

**How is this faster than just looking at charts?** The manual workflow is scrolling seven synchronized 1 kHz traces and writing notes by hand; in y/trace the same incident is marker → analyze → linked evidence → export in a few interactions, as shown in the demo. We deliberately state this as a workflow demonstration, not a measured time saving: the timed engineer study is prepared (`PILOT.md`) but has not been run.

**Is a similar incident the same failure?** No. Retrieval uses only raw torque range, variability and largest sample-to-sample change for each joint. It excludes publisher labels and model answers. The ranked cohort reduces search work, but an engineer must still check motion phase, payload, operating conditions and physical cause.

**What is agentic?** The data pipeline uses LangGraph to plan bounded searches, Tavily to discover sources, and native APIs to verify revisions, licenses, files, schemas, and labels. It pauses for human approval before a separate worker acquires selected assets, inspects them, resolves semantic blockers, builds TimeF, reloads it through TimeNet, and records a validation receipt. The incident workflow then combines deterministic measurements with the trained OpenTSLM; it does not need another orchestration model.

**What business value is demonstrated?** One recorded stop becomes a traceable handoff, and its signal profile retrieves related evidence across runs for review or labeling. Reduced debugging time is still a hypothesis with a defined pilot; no customer ROI or downtime reduction has been measured.

**Can it find an unknown event anywhere in a run?** The demo opens publisher-marked intervals. General unannotated incident localization and multi-recording raw-window inference are not demonstrated by the bundled fixture.

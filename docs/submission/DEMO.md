# Two-minute demo

## Preparation

Follow `inference/README.md` to restore the private tunnel; do not restart training or change the served checkpoint. Start the frontend with `VITE_API_BASE_URL=/api`. Run `python3 -m inference.smoke` and check the expected checkpoint digest. Open the initial `[5.787,6.811)` second selection and run **Analyze interval** once before the presentation clock. Leave that result open. This recording is a development example, not the held-out accuracy demonstration.

Have the audited comparison and the committed [pre-run Markdown handoff](demo/investigation-example.md) open beside the app. If the live service is unavailable, explicitly present the committed smoke result as a previously recorded run; never present it as fresh inference.

Timing, measured 13 September 2026 on the current CPU-only deployment (two smoke runs through the SSH tunnel): one full analyze-interval round trip takes about **24 seconds**. Do not start another query during the timed demo. Say that the visible result is the current canary-v4 output for this exact 7×1,024 input. If a judge requests fresh inference, run it after the core story. Only one query is served at a time; do not queue clicks.

## Script and actions

| Time | Action | Suggested narration |
|---|---|---|
| 0:00–0:18 | Start on the selected publisher event with the pre-run result ready | “After a robot stop, an engineer inspects seven traces and writes the incident handoff. Trace turns that event into a reviewable hypothesis, measured cross-check and exact evidence. This compact fixture comes from open KUKA data.” |
| 0:18–0:40 | Click **Replay interval · 0.5×**, then **Show generated onset cue in 3D** | “The marker is a publisher annotation, not a model detection. The robot replays separately recorded joint angles; the generated cue moves to the model's relative onset. It is not a reconstructed collision.” |
| 0:40–1:08 | Read the actual event class, strongest joint and onset, then point to **Cross-check** | “The model hypothesis and deterministic torque ranges stay separate. Here I read the actual output and the measured largest-range joint. Different rankings tell the engineer which signals to inspect before handoff.” |
| 1:08–1:28 | Click **Inspect exact seven-channel input** | “This opens the model's complete input across all seven raw 1 kHz channels. The publisher label was excluded. Separately, our TimeNet connector loads the original source arrays without changing them.” |
| 1:28–1:43 | Click **Export incident handoff (.md)** | “The Markdown report carries the recording, interval, annotation, measurements, model revision, input receipt, cross-check and limitations to the next engineer.” |
| 1:43–2:00 | Open **Evaluation** and close on the headline comparison | “Across 512 held-out windows, simple features win classification and OpenTSLM is usable 77.93 percent of the time. Trace’s contribution is the richer, reproducible handoff; a timed pilot must still prove that workflow value.” |

Do not memorize a generated answer as a guaranteed future result. Read the actual response. The model and measured quantities can disagree; that is a reason to inspect evidence. The exact archived live response is in `demo/smoke-result.json`.

## Jury questions

**Why use a TSLM when the feature baseline wins?** Use the feature baseline when the task is only a fixed label. Trace is designed around a different benefit: one model output that carries event type, relative timing and joint attribution into a readable, evidence-linked investigation handoff. We have not yet measured whether that handoff benefit justifies the lower reliability. We do not claim the TSLM is necessary for calculating peaks, ranges or the fixed benchmark labels.

**How is this faster than just looking at charts?** The manual workflow is scrolling seven synchronized 1 kHz traces and writing notes by hand; in Trace the same incident is marker → analyze → linked evidence → export in a few interactions, as shown in the demo. We deliberately state this as a workflow demonstration, not a measured time saving: the timed engineer study is prepared (`PILOT.md`) but has not been run.

**Why did you not show the reference comparison?** It is optional investigation depth for a matched motion phase and payload. The core two-minute proof is the exact input, cross-check and auditable handoff; open Compare only if the judge asks how to inspect change across runs.

**What is agentic?** The investigation workflow composes deterministic measurement tools with a specialized trained model. Separately, on the Bosch CNC dataset, the semantic agent produced a validator-valid, evidence-backed dataset specification after one repair. It did not build a connector and is not part of the KUKA demo.

**What business value is demonstrated?** A traceable investigation and export workflow is demonstrated. Reduced debugging time is a hypothesis with a defined pilot; no customer ROI or downtime reduction has been measured.

**Can it find an unknown event anywhere in a run?** The demo opens publisher-marked intervals. General unannotated incident localization and multi-recording raw-window inference are not demonstrated by the bundled fixture.

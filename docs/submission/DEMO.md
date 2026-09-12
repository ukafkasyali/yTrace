# Two-minute demo

## Preparation

Follow `inference/README.md` to restore the private tunnel; do not restart training or change the served checkpoint. Start the frontend with `VITE_API_BASE_URL=/api`. Run `python3 -m inference.smoke` and check the expected checkpoint digest. Open the initial `[5.787,6.811)` second selection with replay at 8 seconds. This recording is a development example, not the held-out accuracy demonstration.

Have the audited comparison and the exported JSON report open beside the app. If the live service is unavailable, explicitly present the committed smoke result as a previously recorded run; never present it as fresh inference.

## Script and actions

| Time | Action | Suggested narration |
|---|---|---|
| 0:00–0:15 | Show seven synchronized torque traces | “After a contact incident, an engineer needs a defensible account of what changed. Trace turns a recorded interval into measurements, a model interpretation, and evidence they can share.” |
| 0:15–0:30 | Open Event 01 marker details | “This mark comes from the dataset publisher. It is an annotation, not a verified cause or model detection.” |
| 0:30–0:55 | Read the actual automatic analysis | “These torque ranges are calculated from 1,024 raw samples per joint. OpenTSLM separately predicts event type, affected joints, and timing. The two sources remain visible.” |
| 0:55–1:10 | Click the input evidence link | “The answer links back to its exact input interval. Joint 2 has the largest measured range here; the model names J4 as its strongest disturbance. These are different quantities, and neither identifies the physical impact location.” |
| 1:10–1:20 | Click Export investigation | “The report preserves the recording, interval, annotation, measurements, generated output, checkpoint and input receipt for a teammate.” |
| 1:20–1:40 | Show the audited comparison | “We compared identical windows from 67 held-out recording groups. Simple signal features were strongest on classification. OpenTSLM achieved 0.8845 semantics macro-F1, with 77.93% usable summaries. We report missing outputs rather than hiding them.” |
| 1:40–1:50 | Show TimeNet receipt | “Both original dataset parts passed build, load and raw-array validation. Code, configuration, predictions and scoring are reproducible.” |
| 1:50–2:00 | Close on the product outcome and limitation | “The product hypothesis is faster correct investigation reports. The next validation is a timed engineer study. Today we demonstrate retrospective triage, not autonomous prevention or a verified physical diagnosis.” |

Do not memorize a generated answer as a guaranteed future result. Read the actual response. The model and measured quantities can disagree; that is a reason to inspect evidence. The exact archived live response is in `demo/smoke-result.json`.

## Jury questions

**Why use a TSLM when the feature baseline wins?** The experiment establishes that direct feature methods are very competitive on this controlled dataset. The temporal-language interface is a working integration with known limits; further investment must demonstrate benefit on richer queries or data. We do not claim the TSLM is necessary for calculating peaks or ranges.

**What is agentic?** The existing workflow composes deterministic measurement tools with a specialized trained model. The separate dataset-scout PR handles bounded sourcing and review. Bounded documentation search is merged infrastructure, not a claimed live forensic RAG agent.

**What business value is demonstrated?** A traceable investigation and export workflow is demonstrated. Reduced debugging time is a hypothesis with a defined pilot; no customer ROI or downtime reduction has been measured.

**Can it find an unknown event anywhere in a run?** The demo opens publisher-marked intervals. General unannotated incident localization and multi-recording raw-window inference are not demonstrated by the bundled fixture.

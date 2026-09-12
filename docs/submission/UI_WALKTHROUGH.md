# Three real examples through canary-v4

Verified on 12 September 2026 through Vite at `http://127.0.0.1:5174/`, the local
SSH tunnel, and the private Nebius inference bridge. This is a replay walkthrough,
not an additional accuracy benchmark.

1. Open Trace and choose **Recording → Collision**. On mobile use **Replay** for
   the robot and plots, or **Investigation** for the answer.
2. **Robot** is shown immediately, with two torque traces below. **All 7 signals**
   opens the full signal view; **Publisher markers** opens annotations and ranges.
   The two default traces have the largest measured ranges in the investigation
   interval. Clicking a robot joint focuses its trace.
3. Under **Event investigation**, check the selected interval and **canary-v4**, then
   click **Analyze interval**. No model query runs automatically on page load.
4. Read the compact generated prediction first and **Measured torque** beneath it.
   They remain different sources. **Model & input details** retains the full model
   response, raw generation, tool steps and the **1,024-sample input receipt**.
5. Click **Inspect 7 input channels** to return to the answer's exact input window and place the replay cursor at its beginning.
   **Export investigation** saves the answer, annotations, calculations and receipt.
6. Press **Play replay**. Recorded articulation and plots advance, while the answer
   and investigation interval stay fixed. **Select at cursor** explicitly changes
   the investigation to the last 1.024 seconds. Selecting a marker or dragging the
   plot also changes the interval. Earlier answers retain their own evidence.
7. Repeat with **Intentional contact** and **Normal / free motion**. Switching cases
   starts a fresh investigation; export first to retain the previous one.

| Example | Recording / split | Raw interval (seconds) | Observed canary-v4 output | Receipt |
|---|---|---|---|---|
| Collision | 03-15-12-53 / train | [5.641, 6.665) | Contact; accidental; strongest J4 | [JSON](demo/collision-case.json) |
| Intentional contact | 03-22-11-18 / test | [5.496, 6.520) | Contact; intentional; strongest J4 | [JSON](demo/intentional-case.json) |
| Normal / free motion | 03-15-12-53 / train | [1.000, 2.024) | No contact; free | [JSON](demo/free-case.json) |

The largest *measured torque range* is J2 in both contact examples; the model
predicts J4 as strongest disturbance under its training task. These are different
quantities, displayed separately, not silently reconciled.

```text
checkpoint-sha256:8ff63b84ae5b64758f66b3e0527f4f225a04bb2b6b39193c806f58d94cec9f23
config:4dbdd82f5ff7
```

To understand the input constraint on desktop, select the free-motion case, change
**to** from `2.024` to `2.000`, and click **Apply**. **Analyze interval** becomes disabled
with a specific explanation. Click **Use 1.024 s window** to restore valid input.
**Measurements only** still supports other historical selections and performs only
numerical calculations. The API separately rejects wrong channel order, channel
subsets, wrong window lengths, missing samples, non-1-kHz cadence, and future input.

Raw data is available in `[0,8)` for these two recordings, and `[4,9)` for the
original bundled recording. Other intervals are reduced overview data and cannot
be treated as equivalent model input. The free-motion case is annotation-free
before the first marker; that is not independently verified absence of contact.
The examples were fixed before inference and must not be reported as 100% accuracy.
The intentional example discloses its test split; no training or benchmark changed.

Reproduce these connection checks with the commands in [inference setup](../../inference/README.md#exact-input-contract-and-fixed-ui-examples).
Source SHA-256 identities and the selection policy are in
[the catalogue](../../inference/demo_cases.json). Excerpts preserve original numeric
values, with no resampling or decimal rounding. Case titles and annotations are
excluded from model requests.

Verification update, 13 September 2026: 44 frontend tests and production build pass.
New tests cover per-recording position routing, motion changes, end-of-recording hold,
reference-mapping disclosure, malformed prediction fallback and fractional-cursor input
fitting. Real model answers still pass through the existing private canary-v4 service.
Collision and intentional outputs were verified again in the simplified UI.

Browser reproduction before the fix measured a 34 px chat shift (conversation top
310→344 px) as input-repair controls appeared. After separating selection and replay,
a playthrough kept the investigation at [5.496,6.520), conversation top at 257 px,
height at 335 px and scroll offset at 0. The collision robot was visually observed
moving from upright to folded as replay advanced from about 7 to 25 seconds.
Desktop 1280×800 and mobile 390×844 views were checked with computer use. Mobile is
viewport emulation, not a physical-device test. The optional 3D bundle retains Vite's
existing size warning.

The original pose fixture has an independent numerical Jacobian check. The two added
recordings use their own measured angles with timestamp and joint-limit checks, and
reuse the original mapping convention. Their missing per-recording Jacobian validation
is explicitly disclosed in metadata and the robot view; body/world geometry remains
schematic. Playback holds the last available angle sample through the final partial
100 Hz interval, never beyond the source recording boundary.


## Incident-start navigation and motion catalogue (13 September)

All publisher markers are available immediately in **Publisher markers**, in a
scrollable list. Selecting one opens **Robot** at the start of its 1.024-second
context (normally 0.4 seconds before the annotation), not at the window end.
**Replay interval · 0.5×** plays that window and pauses at its end. Choosing a
recording example also starts before its incident. Marker navigation beyond raw
coverage still produces an explicitly reduced-resolution numerical summary.

Trace inspects a completed recording. Model/numerical analysis may therefore read
its entire selected window while the visual cursor remains at the beginning.
The existing API `playheadSec` remains the request's historical analysis cutoff,
at least the selected window end; it does not force the visual cursor to move.
Exports now include `analysisHorizonSec` and `replayCursorSec` separately. No samples
beyond the recorded source or outside raw coverage are introduced.

For supported contact predictions, **Show onset in 3D** positions the robot at the
model's generated onset and highlights its predicted strongest responding joint.
The amber cue is visible within 125 ms of that generated onset for readability;
this is a display tolerance, not measured impact duration or localization.
Free predictions and invalid/missing onsets do not create a contact cue.

### Three genuinely different additional trajectories

The previous four batch-41 choices repeated the original trajectory (about
0.07–0.08 degrees RMS difference over a shared sampled time grid). The prepared
replacement catalogue uses these three train recordings instead:

- `04-22-15-33`: reversed J3 motion relative to original `05-28-21-25`.
- `04-22-15-53`: reversed J1 and J2 motion relative to the original.
- `05-26-14-28`: reversed J1 motion relative to the original.

Each has its own source PosMsr and torque files, 100 Hz articulation/overview and
an eight-second raw excerpt around its first publisher marker. Source hashes,
train splits and archive MD5 identities are in `inference/demo_cases.json`.
Selection uses measured trajectories, never model correctness. These are different
joint trajectories within the dataset's sweeping experiments, not distinct
industrial task simulations. Position convention reuses the original validation;
independent per-recording Jacobian checks are not claimed.

### Deployment handoff

The replacement catalogue is active after explicit user approval of the CPU-only
restart on 13 September. The browser displays all three new motion choices; the
first loads its own recorded articulation at 4.684 s, before its 5.084 s annotation.
The same v5b training PID (95923) remained running across the restart. The previous
catalogue is backed up as `inference/demo_cases.before-motion-replacement.json`.
There are six distinct recordings including the original; collision/free share a
recording. The earlier approval block has been resolved by the user's authorization.

Inference is temporarily running on CPU (four OpenMP/MKL threads), using the same
checkpoint and config. GPU reload failed because a concurrent v5b training job
expanded to almost all GPU memory. That training job was not stopped. Restore CUDA
only after coordinating GPU availability; do not launch another competing process.


Verification for this update: 47 frontend tests, 25 inference tests and the frontend
production build pass. Browser checks found all 27 collision markers at cursor
5.641 s; selecting marker 27 at 165.529 s placed replay at 165.129 s. CPU canary-v4
returned accidental contact for the first collision example, with a generated
312 ms onset; **Show onset in 3D** displayed 5.953 s and J4. This verifies the path,
not accuracy or exact CPU/GPU output equivalence. On mobile the Robot tab uses the
available space for articulation; telemetry remains directly in All 7 signals.
The three replacement trajectories are data-validated locally and their catalogue
is active remotely; browser navigation confirms the first uses its own position fixture.


The post-activation CPU smoke on `04-22-15-33` completed with seven × 1,024 raw
samples and the unchanged canary-v4 checkpoint/config. It predicted accidental
contact, J1 strongest, onset 429 ms. The full request, stream and input receipt are
in [motion activation receipt](demo/motion-activation.json). This is connection
verification on a train recording, not an accuracy or CPU/GPU equivalence result.

## What changed? Incident/reference comparison

1. Choose **Collision · reversed J3 motion** (`04-22-15-33`). Its incident window
   is `[4.684,5.708)` seconds. Click **Compare with reference**, or the **Compare** tab.
2. The suggested earlier reference is `[3.160,4.184)` in the same recording. It has
   no nearby publisher annotation, but is not verified normal or phase-matched.
   Both windows contain 1,024 raw samples per joint at 1 kHz. The browser showed J2
   torque range changing from approximately **0.075 to 2.914 Nm**. This is a measured
   difference, not a diagnosed fault or new model prediction.
3. Use **Inspect joint** above the graph to inspect the shared-scale overlay, variability
   and mean shift. **Inspect selected signal** opens its selected-window evidence.
   Peak callouts point to original samples and show signed torque and recording time.
   **All 7 joint measurements** expands the table. A peak is not a model onset.
4. Expand **Change reference or compare another run**. Choose a recording and enter
   its reference start, then **Compare windows**. Match motion phase, payload and
   intended contact. The app enforces equal duration but cannot verify those conditions.
5. Choose **Export comparison** to save source recording IDs, both intervals, sample
   counts, all seven joints' metrics, annotations, method and investigation guidance.
   This is separate from the model investigation export. To compare an older answer,
   use **Compare this answer’s window** beneath it.

If either window lacks raw coverage, both use overview samples and disclose missed
peak risk. Browser verification compared the incident above with original recording
`05-28-21-25` at `[1,2.024)`: both switched to 100 Hz (102 selected / 103 reference
samples due to sampling-grid alignment). No resampling or mixed-resolution comparison
is performed. Same-recording overlap is rejected with a visible explanation.

Validation: 74 frontend tests and production build pass after integrating main's
evidence-boundary and submission-pilot updates. Desktop and 390 px mobile
browser checks covered reference editing, cross-recording loading, overlap rejection,
joint overlays and report download, with no horizontal overflow. These checks establish
working behavior, not measured debugging-time savings. Model training and inference
configuration are unchanged; comparison runs as deterministic local calculations.

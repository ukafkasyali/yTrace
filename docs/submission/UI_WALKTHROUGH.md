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
5. Click **Inspect 7 input channels** to return to the answer's exact input window and move the robot cursor to its end.
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

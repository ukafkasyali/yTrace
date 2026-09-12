# Three real examples through canary-v4

Verified on 12 September 2026 through Vite at `http://127.0.0.1:5174/`, the local
SSH tunnel, and the private Nebius inference bridge. This is a replay walkthrough,
not an additional accuracy benchmark.

1. Open Trace. Under **Example**, choose **Collision**. On a phone-sized screen,
   switch from **Signals** to **Assistant**.
2. Above the question box, check **canary-v4 · 8ff63b84** and
   **Ready: 7 joints × 1,024 raw samples · 1 kHz**. The selection is set automatically.
3. Click **Run OpenTSLM**. Read **Measured in this selected window** for calculations,
   then **OpenTSLM interpretation** for the generated prediction. The source note
   at the top describes publisher metadata; it is not a model answer.
4. Scroll within the answer and open **Model input receipt**. Confirm
   `samplesPerChannel: 1024`, seven `channelIds`, `normalization: train_robust`,
   the selected interval, and the checkpoint SHA below. The receipt belongs to
   that completed answer even if the selection changes later.
5. Click **Inspect 7 input channels** to select and highlight its original input.
   Use **Show recording context · annotations & ranges** for the marker and measured
   joint ranges. **Export investigation** saves the answer and its evidence as JSON.
6. Repeat for **Intentional contact** and **Normal / free motion**. Each uses its own
   raw samples. Switching cases starts a fresh investigation; export first to keep it.

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
**to** from `2.024` to `2.000`, and click **Apply**. **Run OpenTSLM** becomes disabled
with a specific explanation. Click **Use 1.024 s window** to restore valid input.
**Local analysis** still supports other historical selections and performs only
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

Verification: 40 frontend tests, 25 inference tests, production frontend build,
three real UI generations and matching CLI smoke results through `/api`. Desktop
1280×800 and mobile 390×844 layouts checked in the in-app browser. Invalid selection,
one-click repair, evidence navigation, and receipt expansion verified. Mobile is
viewport emulation, not a physical-device test. The existing optional robot-renderer
bundle-size warning remains.

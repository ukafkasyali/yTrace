# Trace · robot observability frontend

Samet's React/TypeScript dashboard: a recording explorer and assistant on the
left, seven synchronized torque strips on the right, and shared replay controls
below. It runs independently of the ingestion and training services.

## Run locally

From this directory, with Node.js and npm installed:

```sh
npm ci
npm run dev
```

Open the local URL printed by Vite. `npm run build` creates `dist/`;
`npm run preview` serves that build. `npm test` runs the unit and service-contract
tests. Serve the app at the site root: the bundled fixture uses `/data/…` URLs.

## What works now

- Switch the recording context between **Overview** and **3D reference**. The
  optional Three.js module loads on demand, renders original schematic geometry,
  supports orbit/zoom/reset and shares joint highlighting with the signal strips.
  Recorded articulation uses validated joint positions at 100 Hz, synchronized
  with replay. Body shape and global base orientation remain schematic. Positions
  and torque use samples at or before the cursor; switching views preserves the
  conversation and interval. Missing positions show a labeled fixed pose.
- Refresh one shared model registry from comparison. The assistant becomes
  available only when the backend declares an `assistant` language service;
  direct OpenTSLM availability does not imply orchestration. Comparison receives
  the assistant's draft or latest question and lets you edit it before running.
- Resume an interrupted ingestion-status check using the existing job ID, without
  accidentally submitting another import.

- Replay a real 170-second KUKA recording with pause, seek, speed and publisher
  marker navigation. The browser reveals samples up to its playback cursor;
  this is recorded-data replay, not a live robot feed.
- Select a time interval across the signal strips, inspect joints, and follow
  answer evidence back to the interval and channel.
- Ask local questions about ranges, absolute sampled peaks and variability.
  Responses are deterministic calculations, not LLM or TSLM output. The selected
  chart interval controls calculations; free-text timestamps are not parsed.
  Variability compares the first and last 30% of that interval.
- Inspect the data source, and view service-backed ingestion and model comparison
  surfaces. Without an API, these remain explicitly disconnected; no successful
  ingestion progress, model answer or benchmark score is simulated.

The initial selection is `[5.787,6.811)` seconds. Ask for torque ranges to reproduce
joint 2 at approximately **4.456 Nm** and joint 1 at **3.088 Nm**.

## Data and resolution

The checked-in fixture comes from Zenodo 21927431, recording `05-28-21-25`:
seven signed external-torque channels, 1 kHz original sampling, 35 publisher
annotations. Its full-recording overview keeps every tenth sample (**100 Hz**).
Only `[4,9)` seconds has **1 kHz** detail in this compact fixture. An interval
entirely inside that detail uses raw-rate samples; other intervals use the
display preview and report that limitation. Short peaks can be absent from the
overview. Values are rounded to six decimals.

Publisher annotations are not independently validated collision-onset labels.
They do not establish causes, contact locations, safety or repair actions. This
recording is a development sample, not a held-out evaluation. See
[data provenance and regeneration](public/data/README.md) for checksum, timing
conventions and source-license notes.

## Connect the team's services

The existing typed client is in `src/services/index.ts`; the wider handoff is
[FRONTEND_INTEGRATION.md](../docs/FRONTEND_INTEGRATION.md). Set the public API prefix
before starting or building Vite, for example:

```sh
VITE_API_BASE_URL=http://localhost:8000/api npm run dev
```

Or set `VITE_API_BASE_URL=/api` when serving a same-origin API. Vite currently has
**no API proxy**: a separate backend origin must provide appropriate CORS. The
client uses `credentials: 'same-origin'`; cross-origin cookie authentication is
not configured. Never place model keys or other secrets in `VITE_*` variables.
Setting the URL enables requests; it does not prove a service is healthy.

- **Ingestion team:** provide datasets, recording signals/events, dataset search
  and ingestion-job endpoints. The current viewer accepts seven synchronized,
  finite-valued channels; recordings with missing samples or gaps are rejected
  with a message rather than silently repaired.
- **Model team:** provide `/models`, `/queries`, query SSE streams and query
  cancellation. Expected model IDs are `cnn-1d`, `direct-llm` and `opentslm`.
  Assistant requests allow server-side tool orchestration; comparison requests
  run available models directly. CNN labels have their own results rendering.
- **Shared contract:** seconds from recording start, half-open intervals, stable
  channel IDs and explicit units. Query requests include `playheadSec`; the
  selected interval cannot extend past it. SSE streams must use the configured
  API origin and increasing decimal event IDs, followed by a terminal event.

The private Nebius bridge and Vite proxy have been verified against the real data
endpoint: seven channels and exactly 1,024 raw samples per channel for the initial
interval. Model loading is blocked by Hugging Face access to the Llama backbone;
no actual generated answer or agentic ingestion run has passed acceptance yet.
See [inference setup](../inference/README.md) for authentication and resume steps.

## Design and ownership

[PRODUCT.md](PRODUCT.md) records product intent and [DESIGN.md](DESIGN.md) describes
the interface decisions. The upper-left panel switches between the recording
explorer and measured 3D articulation. See
[position validation](public/robot/kuka/POSITION_VALIDATION.md) for the numerical
check and its calibration limits.
“Trace” is a provisional interface name. Samet owns this frontend; ingestion and
model-training implementations stay in the team's separately owned modules.

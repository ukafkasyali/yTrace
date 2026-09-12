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

No real model endpoint, trained checkpoint, agentic ingestion run or live backend
integration has been verified in this frontend. The next team acceptance check
is one retrieved recording, one actual model answer with evidence and one actual
ingestion job, including failure and cancellation behavior.

## Design and ownership

[PRODUCT.md](PRODUCT.md) records product intent and [DESIGN.md](DESIGN.md) describes
the interface decisions. The upper-left panel is a recording/data explorer;
3D robot animation is deferred until pose signals and joint mapping are verified.
“Trace” is a provisional interface name. Samet owns this frontend; ingestion and
model-training implementations stay in the team's separately owned modules.

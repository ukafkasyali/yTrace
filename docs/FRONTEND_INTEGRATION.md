# Frontend integration handoff

## First OpenTSLM connection

An isolated Python bridge is deployed privately on the Nebius H100; see
[`inference/README.md`](../inference/README.md) for the Nebius VM, SSH tunnel,
checkpoint configuration and real smoke-test commands. CUDA and OpenTSLM imports
are verified, and all 16 bridge tests pass on the VM. The real data path through
Vite and SSH returns the exact initial historical window. Model loading awaits
Hugging Face access to the Llama backbone; no generated answer has passed yet.
This first service exposes direct OpenTSLM-SP, the bundled dataset,
and query streaming/cancellation. It does not provide assistant orchestration or
ingestion. In local development, Vite proxies inference routes to `127.0.0.1:8000`, sourcing and
approved-source routes to `127.0.0.1:8001`, and ingestion routes to `127.0.0.1:8002`.

The completion payload adds an optional `inputTrace`: `window`, `playheadSec`,
`samplesPerChannel`, `inputSha256`, `model`, `revision`, `normalization`, `padding`
and `latencyMs`. The smoke client checks this receipt against the requested
historical interval. The initial selection contains 1,024 raw samples per channel.
Evidence identifies the model's input, not a verified physical explanation.

## Integration ownership

The header, assistant and model comparison now share a refreshable registry.
Return an available model with ID `assistant` and capability `language` to enable
orchestration mode. A direct `opentslm` model alone enables comparison, not the LLM
assistant. Refresh failures revoke previous availability. Comparison snapshots
the editable question together with the selected window. Ingestion status retries
reuse the existing job ID and never start a replacement import.

The optional 3D reference in `frontend/src/robot/` is independent of inference and
ingestion. It loads only when selected. It uses `public/robot/kuka/kinematics.json`
and original schematic geometry; no third-party CAD meshes are redistributed.
It replays `positions.json` using validated `PosMsr` joint articulation at 100 Hz.
The original 1 kHz positions reproduce the recorded Jacobian across all 169,997
nonstartup samples (maximum error 1.82e-7). Body shape and global base frame remain
schematic; see `public/robot/kuka/POSITION_VALIDATION.md`. Both positions and torque
sample at or before the shared cursor, never interpolating from future data.
The position fixture is recording-specific, independent of torque and inference.

Samet owns the dashboard. Uğur and Atakan provide ingestion and recording access; Atakan, Uğur and Ece provide model inference. The frontend currently replays a real measured recording and calculates local numerical answers. Model inference, ingestion and their progress are not simulated. The Nebius data connection is verified; real inference and ingestion still require acceptance checks. Nothing here requires a backend framework change.

The implementation contract is `frontend/src/services/index.ts`. Configure `VITE_API_BASE_URL` with the API prefix (for example `/api`); without it, backend controls remain disconnected. Setting a prefix enables requests but is not a health check. See `frontend/README.md` for development setup and CORS limitations.

The data-source workspace also consumes the evidence-complete dataset scout. In
local development Vite routes `/api/sourcing-runs` to `127.0.0.1:8001` before its
broader inference proxy. The UI polls the durable run, preserves `NEEDS_INPUT`,
shows hard gates and both confidence measures, and requires a human decision.
An approved manifest is retained in the approved-source library. Ingestion
remains a separate explicit action.

## Shared data contract

Use opaque, stable string IDs. Times are **seconds from recording start**, including event times and evidence links. Units belong to each channel; torque is `Nm`. Never normalize the values sent for display without also supplying the transform. All intervals use `[startSec, endSec)`.

```ts
type WindowRef = {
  datasetId: string;
  recordingId: string;
  startSec: number;
  endSec: number;
  channelIds: string[];
};

type Channel = { id: string; name: string; unit: string; sampleRateHz: number };
type Dataset = { id: string; name: string; sourceUrl?: string; revision: string };
type Recording = {
  id: string; datasetId: string; name: string; durationSec: number;
  channels: Channel[];
};

type SignalWindow = {
  window: WindowRef;
  series: { channelId: string; timeSec: number[]; values: (number | null)[] }[];
  resolution: "raw" | "display";
  aggregation?: string; // e.g. min/max envelope; never silently claim raw samples
};

type SignalEvent = {
  id: string; recordingId: string; startSec: number; endSec?: number;
  channelIds: string[]; label: string;
  origin: "publisher_annotation" | "model_prediction" | "derived_statistic";
  source: string; // annotation version, model/checkpoint, or calculation version
  confidence?: number; // only when supplied and defined by the producer
};

type Evidence = {
  id: string; window: WindowRef; eventId?: string;
  label: string; value?: number; unit?: string;
  source: string; // calculation version, annotation source, or model checkpoint
};
```

Channel IDs must remain stable across retrieval, inference and chart rendering. The wire format preserves gaps as `null`, not zero; the current seven-channel viewer rejects gapped or unsynchronized recordings with an explicit message. Reject mismatched sample lengths and invalid intervals. A point annotation is not automatically an exact physical onset label. The current event strips render only `publisher_annotation` entries; prediction and derived-statistic overlays are a later UI addition. Evidence links act on the open recording's interval and channels; references to another recording are currently ignored rather than switching context.

## Implemented client routes

The routes below are requested by the implemented service client with `/api` as its configured prefix. The local JSON fixture has a separate loader and remains available without a backend. If the backend already has different routes, adapt this client explicitly.

| Adapter method | Endpoint | Result |
| --- | --- | --- |
| `listDatasets()` | `GET /api/datasets` | `Dataset[]` |
| `searchDatasets(query)` | `GET /api/datasets/search?query=…` | `{ id, name, sourceUrl, description?, revision? }[]` |
| `listRecordings(datasetId)` | `GET /api/datasets/:id/recordings` | `Recording[]` |
| `getWindow(recordingId, startSec, endSec, channelIds, maxPoints)` | `GET /api/recordings/:id/signals` | `SignalWindow`; query keys are `startSec`, `endSec`, comma-separated `channelIds`, and `maxPoints` |
| `getEvents(recordingId)` | `GET /api/recordings/:id/events` | `SignalEvent[]` |
| `listModels()` | `GET /api/models` | model IDs, labels, availability and capabilities |
| `startQuery(request)` | `POST /api/queries` | `{ queryId, streamUrl }` |
| `cancelQuery(queryId)` | `DELETE /api/queries/:id` | cancellation acknowledgment |
| `startImport(approvedSourceId, assetIds?)` | `POST /api/ingestions` | persisted ingestion job |
| `getImport(ingestionId)` | `GET /api/ingestions/:id` | ingestion state and validation report |
| `getImportAssets(ingestionId)` | `GET /api/ingestions/:id/assets` | durable per-asset verification receipts used for byte progress |
| `listApprovedSources(page, pageSize)` | `GET /api/approved-sources` | durable reviewed source revisions |
| `getApprovedSource(approvedSourceId)` | `GET /api/approved-sources/:id` | source detail and approval history |
| `getApprovedSourceManifest(approvedSourceId)` | `GET /api/approved-sources/:id/manifest` | exact assets and limitations |
| `deleteApprovedSource(approvedSourceId)` | `DELETE /api/approved-sources/:id` | remove an unreferenced source from the approved library |
| `getImportForSource(approvedSourceId)` | `GET /api/ingestions/by-source/:id` | existing job after reload |
| `getMappingProposals(ingestionId)` | `GET /api/ingestions/:id/mapping-proposals` | bounded mapping candidates |
| `confirmMapping(ingestionId, mapping)` | `PUT /api/ingestions/:id/mapping` | version-checked mapping resume |
| `getImportReceipt(ingestionId)` | `GET /api/ingestions/:id/receipt` | validated TimeF receipt |
| `getImportedRecords(ingestionId, page, pageSize)` | `GET /api/ingestions/:id/records` | bounded, paginated TimeF record summaries |
| `getImportedReplay(ingestionId, recordKey)` | `GET /api/ingestions/:id/records/:recordKey/replay` | replay overview, publisher annotations and an initial raw detail window for a compatible imported record |
| `startSourcingRun(input, idempotencyKey)` | `POST /api/sourcing-runs` | durable run ID and initial status |
| `getSourcingRun(runId)` | `GET /api/sourcing-runs/:id` | requirements, evidence, gates and ranking |
| `reviewSourcingRun(runId, decision)` | `POST /api/sourcing-runs/:id/approvals` | resumed run after human review |
| `getSourcingReport(runId)` | `GET /api/sourcing-runs/:id/report` | evidence report as Markdown text |
| `getSourcingManifest(runId)` | `GET /api/sourcing-runs/:id/manifest` | approved ingestion handoff |

Signal decimation is for display only. Backend model inference and numerical evidence should use original data for the same window. Recording responses include total duration even when only a preview window is returned. The current viewer initially requests the whole recording with a 200,000-point budget; it does not yet retrieve raw windows on demand. Local analysis on a returned display series explicitly reports that reduced resolution.

## Assistant and direct-model modes

```ts
type QueryRequest = {
  mode: "assistant" | "direct";
  modelId?: string; // required in direct mode
  question: string;
  window: WindowRef;
  playheadSec: number; // finite historical analysis cutoff; window.endSec must be <= this
  conversationId?: string;
};
```

**Assistant mode:** the LLM can call TSLM inference, CNN classification, exact signal statistics and document/dataset retrieval. The server controls tool availability. Return the final answer and evidence separately. Display brief observable tool progress such as “Computing joint ranges”; do not expose private chain-of-thought.

**Direct mode:** the comparison workspace submits the same captured question/window to every available model. Expected IDs are `cnn-1d`, `direct-llm` and `opentslm`. Model metadata is `{ id, label, available, capabilities, reason?, revision? }`, with capabilities drawn from `language`, `classification` and `localization`. OpenTSLM and a direct LLM may produce language; CNN results use typed labels/scores. Unavailable models are disabled with a reason. Do not fabricate confidence or present predictions as verified measurements.

The frontend sends the selected window explicitly. Updating chart selection must not silently change an in-flight query; its answer retains the original window and model provenance.

## Streaming and failure behavior

After creating a query, consume its `streamUrl` as SSE. Its origin must match the configured API origin. Each JSON event contains an increasing decimal-string `id`, `queryId`, `type` and object `payload`. SSE `id` and `event` fields can supply the ID and type if omitted from JSON; when both IDs are present they must agree. All events in a stream must keep the same query identity.

| Type | Payload |
| --- | --- |
| `tool.started` | `{ callId, tool, label }` |
| `tool.completed` | `{ callId, summary, evidence?: Evidence[] }` |
| `answer.delta` | `{ text }` |
| `answer.completed` | `{ answer?, evidence?: Evidence[], modelId?, modelRevision?, labels?: { label, score? }[] }`; the final answer may use accumulated deltas |
| `query.error` | `{ code, message, retryable }` |
| `query.cancelled` | `{}` |

Completion, error and cancellation are terminal. The client ignores duplicate/older IDs and late events from replaced queries. Closing the browser stream is not server cancellation: the frontend also calls the cancellation endpoint. Partial output stays visible with an error or cancellation state. Ending a stream before a terminal event raises “Connection lost before the answer completed.” There is no resume or `Last-Event-ID` implementation. “Use this question again” returns the text to the composer; sending it creates a new query with the current selection.

Use a consistent `{ error: { code, message, retryable } }` envelope for HTTP errors. Distinguish unavailable model, unsupported question, invalid window, missing recording and inference failure. No successful sample response should substitute for a failed live request.

## Ingestion visibility and security

Ingestion states: `queued → acquiring → inspecting → mapping → validating → importing → ready`, with `needs_input`, `unsupported_format`, and `failed` branches. `POST /ingestions` receives `{ approvedSourceId, assetIds? }`; the server resolves the approved manifest and returns the persisted job. It does not accept a browser-supplied source or download URL. One approved source revision has one logical ingestion job: matching retries return that job, and an incompatible asset selection returns `409 INGESTION_CONFLICT`. Once ready, the UI offers the existing result instead of another ingest action. The UI polls active jobs and their durable asset receipts, showing verified bytes and completed assets rather than estimating uncommitted stream bytes. It presents explicit channel-unit confirmation when mapping is required and displays the immutable validation receipt when ready. Opening that receipt loads a paginated record browser from the ingestion registry. A record is replay-compatible only when it contains the seven canonical synchronized external-joint-torque channels at 1 kHz. Selecting one replaces the active replay and routes later raw-window/event requests through receipt-bound ingestion endpoints; other records remain metadata-only. Imported records currently support replay and deterministic measurements, not OpenTSLM inference or recorded joint-position animation. It does not silently select the inference bridge's unrelated fixture catalog. It does not implement ingestion-job cancellation.

An ingestion agent proposes mappings; deterministic validators check them. Unknown units or ambiguous channels produce `needs_input`, not invented metadata. Retrieval results identify their source documents separately from signal evidence. Server-side URL fetching must reject private/local network targets and enforce size/type limits.

Keep model keys and credentials on the server, never in `VITE_*` variables or browser code. Prefer a same-origin API with server-managed sessions. Do not log raw credentials or expose tracebacks in UI errors.

## First integration acceptance check

1. Load one real recording and verify seven synchronized channels, units and timing; confirm gaps are rejected rather than filled.
2. Select an interval; confirm inference receives that exact window, playback cursor and model ID in direct mode.
3. Render publisher annotations; clicking evidence highlights the correct time/channel on the open recording. Do not relabel a prediction as an annotation.
4. Stream one real query through tool progress, final answer and evidence; test cancellation and a model error.
5. Run comparison with a CNN and verify its classification labels/scores render alongside available language-model outputs.
6. Import one supported source; show validation findings and open the resulting dataset only after `ready`.
7. Retain “Real sample data” for the bundled recording and resolution labels for local calculations. Backend-loaded data must remain recorded replay unless an actual live source is implemented. Never remove disconnected-model notices just to stage a demo.


## Deployed raw-replay extension (12 September 2026)

The current bridge serves three recordings. `GET /demo-cases` returns
`{ id, title, recordingId, interval: { start, end }, note }[]`. These are navigation
presets; notes and titles never become model evidence. `GET /recordings/{id}/replay`
returns the frontend `DemoData` contract: recording metadata, overview times/channels,
publisher events, and a separate raw `detail` interval with its own times/channels.
The viewer prefers this endpoint so loading an overview does not discard raw detail;
404 falls back to the earlier signals/events adapter contract.

Model queries require `joint_1` through `joint_7` in order, exactly 1.024 seconds,
and 1,024 contiguous raw 1-kHz samples per joint, all before `playheadSec`.
Invalid shape/cadence returns 422 `MODEL_INPUT_SHAPE`; unavailable raw coverage
returns 422 `RAW_DATA_UNAVAILABLE`. Display and local calculation selections remain
arbitrary. `answer.completed` includes `modelOutput` (original generation) and
`inputTrace` with model revision, exact window, `samplesPerChannel`, `inputSha256`,
normalization and generation latency. The UI retains these with the completed
answer and its export, separately from current selection and publisher metadata.


### Completed-recording cursor separation

The visual replay cursor can be at the beginning of an incident while its complete
recorded window is analyzed. `playheadSec` in query/receipt payloads is the bounded
historical analysis cutoff (at least window.endSec, at most recording duration).
It no longer implies the current 3D cursor. Investigation exports retain that
legacy field and add explicit `analysisHorizonSec` and `replayCursorSec` fields.
Raw 7×1,024 sample validation is unchanged. This applies to completed recordings;
it is not permission to read future data from a live stream.

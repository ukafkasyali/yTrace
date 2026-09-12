# Dataset sourcing API

The dataset scout is a separate FastAPI module consumed by the frontend or an orchestration
service. It discovers and verifies datasets, then stops at a human-approved manifest. It does not
download dataset contents or start ingestion.

## Lifecycle

`QUEUED → PLANNING → DISCOVERING → VERIFYING → ASSESSING` ends in one of:

- `AWAITING_APPROVAL → APPROVED`, which writes `manifest.json` for the selected eligible candidate;
- `AWAITING_APPROVAL → DISCOVERING → … → AWAITING_APPROVAL` after reviewer feedback;
- `NEEDS_INPUT` when any mandatory evidence requirement is unsupported;
- `FAILED` for an unrecoverable workflow failure.

The service uses the public `runId` as LangGraph's `thread_id`. An approval resumes the durable
checkpoint rather than rebuilding prior research. Cached demo evidence is labelled `CACHED`; a mix
of cached and live results is labelled `PARTIAL`.

## Endpoints

### `POST /api/sourcing-runs`

Requires an `Idempotency-Key` header. Repeating the same key and body returns the same run; reusing
the key with another body returns `409 IDEMPOTENCY_CONFLICT`.

```json
{
  "brief": "Find 1 kHz robot collision and contact torque time series...",
  "constraints": {
    "mustHave": ["raw joint torque"],
    "preferred": ["MATLAB"],
    "allowedLicenses": ["cc-by-4.0"],
    "maxDownloadBytes": 25000000000
  }
}
```

The `202` response is:

```json
{
  "runId": "8a611605-3bee-4a45-b714-95d83ce7302d",
  "status": "QUEUED",
  "statusUrl": "/api/sourcing-runs/8a611605-3bee-4a45-b714-95d83ce7302d"
}
```

### `GET /api/sourcing-runs/{runId}`

Returns requirements, bounded hypotheses, canonical candidates, verified profiles, evidence,
deterministic assessments, separate evidence/recommendation confidence, retrieval errors and the
current status. The frontend should treat IDs as opaque. After a reviewer refinement,
`refinementOutcomes` makes the result explicit: it records the executed query, newly discovered
candidate and evidence IDs, the recommendation before and after rescoring, and one of
`RECOMMENDATION_CHANGED`, `RECOMMENDATION_WITHHELD`, `EVIDENCE_EXPANDED`, `CANDIDATES_ADDED`,
or `NO_CHANGE`. `excludedCandidateIds` records reviewer exclusions separately from deterministic
assessment results so clients can show the full audit trail without offering excluded choices.

Candidates also expose additive source-exploration fields: `sourceRole` is `DISCOVERY_LEAD` or
`DATASET_ARTIFACT`, `discoveryDepth` is zero to two, and `discoveredFromCandidateId` identifies the
page that exposed a child native URL. Profiles expose `isDatasetArtifact` and
`datasetIdentityReason`. Each URL remains a separate candidate identity; the service never merges
a guide with a dataset it links to. Clients should rank only dataset artifacts and may show leads
in a separate audit section. Older persisted payloads that predate these fields remain valid.

When the brief explicitly names equipment or an application domain, requirements include a
mandatory `DOMAIN` category. Verified profiles expose the native-source-supported terms in
`domains`, and the associated evidence uses `claimKey: "domains"`. Domain matching may use the
configured LLM to recognize semantic equivalents, but a match is accepted only when its returned
quote exists verbatim in a fetched native source. Unsupported or uncertain domains therefore fail
the `domain` hard gate instead of receiving task-fit credit. Candidate ranking places supported
domain matches ahead of higher-scoring domain mismatches; the raw deterministic scores remain
visible for auditability.

### `POST /api/sourcing-runs/{runId}/approvals`

Only valid in `AWAITING_APPROVAL`. The reviewer may approve the recommendation or another
assessed candidate whose score is at least 65 and which passes every mandatory hard gate. The
response retains `recommendedCandidateId` for auditability and records the reviewer override in
`approvedCandidateId`.

```json
{"decision": "APPROVE", "candidateId": "ds_0123456789ab", "note": "Team review"}
```

Reject with
`{"decision":"REJECT","candidateId":"ds_0123456789ab","note":"Find an alternative"}`. A
non-empty note is required. `candidateId` identifies the candidate to exclude; for compatibility,
the server uses the current recommendation when older clients omit it. Rejection adds a
review-directed query and resumes discovery in the same LangGraph thread, preserving prior
candidates and evidence. The excluded candidate remains visible in assessments and reports for
auditability, but cannot be recommended or approved later in the run. Attempts to approve it
return `409 RUN_CONFLICT`. Up to two reviewer refinements are allowed, subject to the original
time and Tavily-credit budgets. Repeating the same successful approval is safe; other
terminal-state approvals also return `409 RUN_CONFLICT`.

Reviewer feedback directs the next discovery query; it does not silently alter mandatory gates or
the deterministic score weights. Newly discovered candidates are considered before previously
unverified candidates while already verified, non-excluded choices remain available. When the
reviewer rejects the current recommendation and the search finds no eligible replacement, the
response records `RECOMMENDATION_WITHHELD` and sets `recommendedCandidateId` to `null`; it never
selects the rejected candidate again. The run pauses again while another refinement remains, then
transitions to `NEEDS_INPUT` if the final attempt still has no eligible alternative.

```json
{
  "iteration": 1,
  "feedback": "Find an alternative",
  "query": "robot collision dataset alternative ...",
  "outcome": "RECOMMENDATION_WITHHELD",
  "rejectedCandidateId": "ds_0123456789ab",
  "previousRecommendedCandidateId": "ds_0123456789ab",
  "recommendedCandidateId": null,
  "newCandidateIds": [],
  "newEvidenceIds": []
}
```

### `GET /api/sourcing-runs/{runId}/report`

Returns `text/markdown`. The dataset ranking contains only sources that pass the
`dataset_identity` hard gate; inspected guides, papers, lists, catalogues and other non-dataset
pages appear under `Discovery leads excluded` with their reason. Contradictions list every native
observation and precedence. Resolution uses the highest-precedence native value while
`evidence.jsonl` retains all observations.

### `GET /api/sourcing-runs/{runId}/manifest`

Returns the approved manifest. Before approval it returns `409 ARTIFACT_UNAVAILABLE`.

## Errors and operational bounds

Errors use the shared envelope:

```json
{"error":{"code":"INVALID_REQUEST","message":"Request validation failed","retryable":false}}
```

One run has three initial hypotheses, at most two evidence-gap hypotheses, at most two
review-directed refinements, eight deeply verified dataset artifacts, twelve Tavily credits and
ninety seconds of active research time. The scout may inspect up to sixteen discovery leads over
at most two native-link hops. Time spent waiting for human review does not consume the active
research clock. Tavily advanced search costs two credits per query, so reviewer refinements use
only the credits remaining after initial and gap searches. Mandatory gates are source-local
dataset identity, canonical provenance, explicit allowed licence, usable time-series files,
requested task labels, schema documentation and bounded acquisition size. Dataset identity first
requires a direct, non-empty supported data or archive file on the candidate's own native source.
Model-assisted semantic support must cite a verbatim excerpt from that same source and fails
closed on model errors; linked pages cannot lend evidence to a parent.

Server-side fetches accept only HTTPS native URLs on the exact GitHub, Zenodo and Hugging Face
allowlist, reject embedded credentials and non-default ports, resolve only to public addresses,
disable redirects and cap response sizes. Credentials belong in server-side `SOURCING_*`
environment variables, never frontend variables.

The demo process binds to `127.0.0.1` and does not implement application authentication. Any
shared deployment must put these endpoints behind the project's authenticated, same-origin API
gateway before exposing them beyond the local hackathon environment.

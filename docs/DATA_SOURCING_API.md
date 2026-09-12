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

### `POST /api/sourcing-requirement-previews`

Builds the research contract without spending Tavily credits or starting a run. The frontend uses
this preview to let a reviewer mark inferred requirements as `MUST` or `SHOULD`, disable them, and
add up to ten custom natural-language requirements.

```json
{
  "brief": "Find CNC telemetry with labelled accidental head contact...",
  "customRequirements": ["Contains at least 200 labelled contact events"]
}
```

The response is a list of typed requirement definitions. Canonical provenance has
`isSystemRequired: true`; clients may display it but cannot disable or replace it. Usable
time-series files defaults to `MUST` but is configurable, so reviewers may make it preferred or
disable it when sourcing non-time-series datasets. Custom text is represented as category `OTHER`
and defaults to `MUST`.

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
  },
  "requirements": [
    {
      "id": "req_schema",
      "label": "Schema documentation",
      "description": "Channels and units are documented.",
      "priority": "SHOULD",
      "category": "SCHEMA",
      "expectedValues": [],
      "isSystemRequired": false
    }
  ]
}
```

`requirements` is optional for backward compatibility. When absent, the planner uses its inferred
defaults. When present, it is authoritative for configurable requirements: omit a requirement to
disable it, use `MUST` to make missing evidence disqualifying, or use `SHOULD` to influence scoring
without creating a hard failure. The server always restores its fixed integrity requirements and
canonicalizes client definitions so they cannot claim system-required status.

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
`feedbackAllowed` tells clients whether the current durable checkpoint can accept another bounded
reviewer-directed search. It may be true for either `AWAITING_APPROVAL` or `NEEDS_INPUT`.

Candidates also expose additive source-exploration fields: `sourceRole` is `DISCOVERY_LEAD` or
`DATASET_ARTIFACT`, `discoveryDepth` is zero to two, and `discoveredFromCandidateId` identifies the
page that exposed a child native URL. Profiles expose `isDatasetArtifact` and
`datasetIdentityReason`. Each URL remains a separate candidate identity; the service never merges
a guide with a dataset it links to. Clients should rank only dataset artifacts and may show leads
in a separate audit section. Older persisted payloads that predate these fields remain valid.

Each assessment exposes reviewer-facing `suitabilityLevel` as `LOW`, `MEDIUM`, or `HIGH` and
`suitabilityFactors` as typed `STRENGTH`, `LIMITATION`, or `BLOCKER` explanations. Factors include
the relevant evidence IDs when evidence exists. A failed mandatory gate always produces `LOW`;
otherwise the existing deterministic weighted assessment maps to `MEDIUM` or `HIGH`. Evidence
confidence remains separate: it describes the completeness and consistency of support, not dataset
fitness. `totalScore`, `score`, and `tier` remain temporarily available for older clients and
deterministic tie-breaking, but new reviewer interfaces should not display them.

When the brief explicitly names equipment or an application domain, requirements include a
mandatory `DOMAIN` category. Verified profiles expose the native-source-supported terms in
`domains`, and the associated evidence uses `claimKey: "domains"`. Domain matching may use the
configured LLM to recognize semantic equivalents, but a match is accepted only when its returned
quote exists verbatim in a fetched native source. Unsupported or uncertain domains therefore fail
the `domain` hard gate instead of receiving task-fit credit. Candidate ranking places supported
domain matches ahead of unrelated candidates. The report records qualitative suitability factors
instead of exposing numeric scores.

### `POST /api/sourcing-runs/{runId}/approvals`

Only valid in `AWAITING_APPROVAL`. The reviewer may approve the recommendation or another
assessed candidate with `MEDIUM` or `HIGH` suitability which passes every mandatory hard gate. The
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

When a run is `NEEDS_INPUT` and `feedbackAllowed` is true, the same endpoint accepts
`{"decision":"REJECT","note":"Search specifically for ..."}` without a `candidateId`. The run is
already paused at a LangGraph interrupt, so this resumes the same thread and preserves all prior
evidence. If `feedbackAllowed` is false, the time, credit or two-refinement bound is exhausted and
the reviewer must start a new run.

Reviewer feedback directs the next discovery query; it does not silently alter mandatory gates or
the deterministic suitability policy. Newly discovered candidates are considered before previously
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
`evidence.jsonl` retains all observations. Every ranked source includes its suitability level and
the strengths, limitations, or blockers that produced it; numeric scores are omitted.

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
only the credits remaining after initial and gap searches. Mandatory gates always include
source-local dataset identity and canonical provenance. Usable time-series files, licence, task
labels, schema, acquisition, domain and other configurable checks become mandatory only when
their confirmed priority is `MUST`. Dataset identity first
requires a direct, non-empty supported data or archive file on the candidate's own native source.
Model-assisted semantic support must cite a verbatim excerpt from that same source and fails
closed on model errors; linked pages cannot lend evidence to a parent.

Custom `OTHER` requirements are evaluated only against fetched native-source documents. The model
must return the requirement ID, source-document index and a verbatim supporting quote; the service
independently validates all three before recording evidence. An absent model, invented quote or
uncertain result remains unsupported, and a custom `MUST` therefore prevents recommendation.

Server-side fetches accept only HTTPS native URLs on the exact GitHub, Zenodo and Hugging Face
allowlist, reject embedded credentials and non-default ports, resolve only to public addresses,
disable redirects and cap response sizes. Credentials belong in server-side `SOURCING_*`
environment variables, never frontend variables.

The demo process binds to `127.0.0.1` and does not implement application authentication. Any
shared deployment must put these endpoints behind the project's authenticated, same-origin API
gateway before exposing them beyond the local hackathon environment.

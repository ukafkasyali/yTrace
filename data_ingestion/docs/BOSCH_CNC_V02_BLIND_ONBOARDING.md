# Bosch CNC blind semantic onboarding: DatasetSpec v0.2

## Outcome

The definitive frozen v0.2 run produced a validator-valid `DatasetSpec` after one repair round.
The semantic boundary has been crossed; connector implementation was intentionally not started.

Bosch-specific semantic changes: **NONE**.

No Bosch-specific production rule, prompt hint, semantic mapping, documentation query, or validator
exception was added. The Semantic Agent used the full existing Bosch profile and the repository
README through the generic bounded documentation interface.

## Generic v0.2 changes

`DatasetSpec` now decodes both v0.1 and v0.2. Version 0.2 adds:

- `implicit_regular` time axes with no raw `source_variable`;
- a non-negative `sample_index_origin`;
- an evidence-bearing sampling-rate value and unit on the time axis;
- `observed_dtypes` for the complete set of physical per-record storage types; and
- nullable legacy `dtype` / signal-level `rate_hz` fields for a clean v0.2 representation while
  retaining v0.1 compatibility.

For a positive-rate implicit clock, monotonicity follows structurally from
`t[i] = (i - sample_index_origin) / sampling_rate`; it is not validated as a raw timestamp
observation.

The validator now:

- validates implicit-clock structure without demanding a fictional time variable;
- accepts documented rates when exact bounded-run evidence IDs exist and raw observations are
  silent;
- rejects documented rates that contradict any deterministic raw rate;
- requires documented claims to include at least one documentation evidence ID;
- verifies all v0.2 claim references against IDs actually returned during the run;
- validates the exact observed dtype set and rejects fake scalar values such as `mixed`; and
- preserves all v0.1 validation behavior for the KUKA reference specification.

Repair orchestration now scores every candidate as
`(invalid, total_issue_count, unsupported_claim_issue_count)`. A candidate replaces the best only
when its score is strictly lower; ties retain the earlier candidate. Subsequent rounds repair the
best candidate rather than a rejected regression. The repair summary records candidate scores,
accept/reject reasons, the best round after each attempt, final best score, and tie-breaking rule.

## Tests

Six focused tests cover:

- implicit regular clocks without raw timestamps;
- documented sampling when profiling is silent;
- contradiction by an observed raw sampling rate;
- homogeneous and heterogeneous physical dtype sets;
- documentation evidence kind and exact run-evidence membership; and
- deterministic retention of the 2-issue candidate for a synthetic 12 → 2 → 5 sequence.

Results before the definitive rerun:

- focused v0.2 tests: 15 passed;
- complete suite: 67 passed;
- Ruff on every changed production/test path: passed;
- `git diff --check`: passed;
- KUKA v0.1 reference round-trip and validation: passed as part of the suite.

## Definitive Bosch v0.2 trace

The initial generation used 5 Responses API turns and 17 tool calls, including 12 documentation
queries. It independently found evidence for acceleration/vibration, X/Y/Z channel order, 2 kHz
sampling, normal/anomalous labels, machine/process structure, timeframe, and classification use.
It preserved the physical unit as unresolved.

Initial validation had 7 issues:

- one exact record-boundary wording mismatch; and
- six required provenance fields that the current profile provenance contract did not advertise.

Repair round 1 used 2 Responses turns and 2 tool calls. It matched the observed boundary and made
the six documented-but-not-profile-advertised provenance fields optional. It changed no signal,
channel, clock, unit, dtype, label, or task semantics. Validation then had 0 issues.

Best-candidate selection chose round 1 with score `[0, 0, 0]`. A second repair round was neither
needed nor run. Total usage was 7 Responses turns and 19 tool calls. Reasoning stayed high; no model
fallback occurred.

The final spec contains:

- source dataset `vibration_data` mapped as documented acceleration;
- channel indices 0/1/2 mapped to documented x/y/z;
- exact observed dtypes `float32`, `float64`, and `int64`;
- `sample_time` as an implicit regular clock with origin 0;
- documented sampling rate 2000 Hz with returned documentation evidence;
- no events;
- a documented/inferred process-health binary-classification interpretation; and
- unresolved physical unit with null name/symbol.

Unsupported semantic claims: **0**. Formal unresolved semantic claims: **1** (physical unit).

## v0.1 versus v0.2

| Measure | v0.1 | v0.2 definitive |
|---|---:|---:|
| Files ingested | 1,702 | 1,702 |
| Initial issues | 12 | 7 |
| Repair 1 issues | 2 | 0 |
| Repair 2 issues | 5 | not run |
| Selected best round | latest / round 2 | round 1 |
| Final issues | 5 | 0 |
| Validator valid | no | yes |
| Unsupported semantic claims | 1 | 0 |
| Formal unresolved claims | 1 | 1 |
| Sampling rate | documented, rejected | documented, supported, not contradicted |
| Time axis | fictional null-source raw axis | valid implicit regular clock |
| Dtype | invalid `mixed` scalar | exact heterogeneous observed set |
| Repair regression prevented | no | yes, implemented and tested |

Original blocker classification:

- fictional/null time source: **fixed generically**;
- unsupported raw monotonicity: **fixed generically** by implicit-clock construction;
- heterogeneous dtype: **fixed generically**;
- documented rate rejected when raw data is silent: **fixed generically**;
- later repair regression: **fixed generically** and exercised by a 12 → 2 → 5 test;
- physical unit: **still unresolved**, correctly and conservatively;
- no new validator issue remains in the definitive result.

All fixes apply plausibly to unrelated sampled datasets, multi-record storage, evidence-grounded
metadata, and iterative semantic repairs.

## Connector boundary

The validated semantic result justifies starting the TimeNet connector workflow. The next step is:

```text
Use the TimeNet add-dataset-connector workflow to implement a Bosch CNC connector from
/home/ugur/ysamet/data_ingestion/outputs/bosch_cnc_v02_final/final_spec.json and
/home/ugur/ysamet/data_ingestion/outputs/bosch_cnc_profile.json. Preserve the documented implicit
2000 Hz clock, heterogeneous source dtypes, unresolved physical unit, path-derived provenance, and
the validated record boundary. Do not reinterpret or add semantics beyond the validated spec.
```

That command/prompt was not executed.

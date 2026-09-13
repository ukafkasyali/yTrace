# Bosch CNC blind semantic-onboarding experiment

## Outcome

The frozen semantic pipeline did **not** produce a validator-valid `DatasetSpec` after its two
allowed repair rounds. The result is not ready to cross the TimeNet connector boundary.

The experiment used the full cloned dataset at `/home/ugur/CNC_Machining/data`, its existing
`README.md` through `documentation_sources()` / `documentation_search()`, and no Bosch-specific
deterministic semantic hints.

## Generic capability changes

The profiler previously discovered only MAT files. Its MATLAB-v7.3 loader could internally use
HDF5, but no generic `.h5` / `.hdf5` discovery or profiling path existed. Generic HDF5 support now:

- recursively discovers `.h5` and `.hdf5` files and treats each file as one record;
- enumerates groups and datasets and captures bounded root, group, and dataset attributes;
- records dataset paths, shapes, dtypes, dimensions, sizes, hashes, and first-value samples;
- streams bounded chunks to calculate finite min/max/mean/standard deviation and NaN/Inf counts;
- structurally identifies sample/channel axes for two-dimensional arrays without assigning meanings;
- preserves unique record identity with the relative file path; and
- supports host-validated bounded HDF5 excerpts without exposing unrestricted array reads.

The existing `DatasetProfile` dataclasses were not changed. HDF5 details use existing open-ended
`nested_structure`, `observed_structure`, and `discovery` mappings. The evidence API and semantic
prompt were not changed. Validation was only generalized to respect structural channel/sample axes
instead of assuming MATLAB row-channel orientation.

## Deterministic observations

The five-file smoke subset covered M01/M02/M03, OP01/OP05/OP07/OP11, and both `good` and `bad`
source directories. All five files opened successfully. The full profile then observed:

- 1,702 HDF5 files and 1,702 file-bounded records;
- directory components with 3 first-level values, 15 second-level values, and 2 third-level values;
- one HDF5 hierarchy variant: no groups, no attributes, and one root dataset named
  `vibration_data`;
- `vibration_data` shape `(N, 3)`, with 299 distinct lengths from 26,793 to 317,440;
- channel axis 1, sample axis 0, and three structurally observed channels;
- 810 `float64`, 765 `int64`, and 127 `float32` datasets;
- zero NaN and zero Inf values; and
- two audit warnings: schema/dtype variation and sequence-length variation, with no errors.

These are observed structural facts. The profiler did not assign meanings to machine/process/label
directory components, the dataset name, axes, units, sampling, or task.

## Evidence discovered by the Semantic Agent

Across the initial run and repairs, the agent discovered documentation evidence stating that the
data is CNC-machine vibration/acceleration measured by a tri-axial sensor, the three channels map to
X/Y/Z, acquisition is at 2 kHz, `good` and `bad` describe normal and anomalous vibration, paths
encode machine and process, and filenames encode machine, timeframe, process, and example. Relevant
evidence IDs include:

- `ev_documentation_bosch_cnc_81a34444200c`
- `ev_documentation_vibration_b858d82ee8e7`
- `ev_documentation_sampling_rate_d4a72bc1f21b`
- `ev_documentation_process_health_8649ae438725`
- `ev_documentation_machine_number_a9a27568c63f`
- `ev_documentation_filename_b6a73bbcd152`

The agent found no documentation result for an acceleration unit. Unit semantics therefore remained
unresolved, as desired.

## Candidate and repairs

The initial candidate mapped `vibration_data` to documented acceleration with X/Y/Z channels,
documented 2 kHz sampling, unresolved units, a synthetic `sample_time`, directory-derived process
health classification, and required record provenance. It failed with 12 issues: record-boundary
wording, null/unobserved time source and unverified monotonicity, invalid mixed-dtype spelling,
sampling not observed by the profiler, and six unavailable required provenance fields.

Repair round 1 reduced the result to 2 issues. It matched the exact record boundary, selected the
observed `float64` dtype spelling, removed the synthetic time-axis object, and made the six provenance
fields optional. It left the documented 2 kHz rate and set the signal's time-axis reference to null,
which failed `UNKNOWN_TIME_AXIS`; the validator also still rejected the documented rate because the
profile cannot observe it.

Repair round 2 regressed to 5 issues. It restored synthetic `sample_time` with a null source and
claimed monotonicity, changed dtype to the invalid value `mixed`, and retained 2 kHz. The final issues
are `UNDECLARED_SOURCE_VARIABLE`, `TIME_AXIS_NOT_OBSERVED`, `TIME_AXIS_NOT_MONOTONIC`,
`DTYPE_MISMATCH`, and `SAMPLING_RATE_MISMATCH`.

## Evaluation

- **Observed:** one `(samples, 3)` source array per file; mixed raw dtypes; variable lengths; file
  record boundary; source-path provenance; no explicit time dataset; no NaN/Inf.
- **Documented:** acceleration/vibration semantics, X/Y/Z mapping, 2 kHz sampling, machine/process
  organization, normal/anomalous folder labels, and filename components.
- **Inferred:** a process-health classification task from the documented labels. The current spec
  schema has no evidence-bearing claim field for tasks or record-metadata parsing.
- **Unresolved:** physical acceleration unit (one formal unresolved `EvidenceClaim`). Record metadata
  extraction rules and mixed-dtype normalization also remain connector-level structural decisions.
- **Unsupported:** one semantic/time assertion in the final spec—monotonic synthetic `sample_time`
  despite no observed time source. The `mixed` dtype is an additional unsupported structural value.
  The 2 kHz rate is documentation-supported, but the validator currently treats any non-observed rate
  as a mismatch because `SamplingClaim` carries no evidence status.

The source-variable and channel mapping quality is otherwise strong: the sole source dataset and all
three channels are correctly mapped with direct structural and documentation evidence. No event
mapping was invented. Record discovery is correct after repair, but documented path metadata is not
expressible as a validated extraction mapping in `DatasetSpec` v0.1.

## y/trace and verification

The initial generation used 5 Responses API turns and 15 tool calls. Repair round 1 used 5 turns and
16 calls; repair round 2 used 4 turns and 10 calls. All used `gpt-5.6-sol`, Responses API, high
reasoning, with no fallback. Full tool results, excerpts, evidence IDs, response IDs, candidate specs,
validator outputs, grouped repair inputs, and final artifacts are under
`outputs/bosch_cnc_blind_onboarding/`.

Verification executed:

- targeted profiler/evidence/spec tests: 27 passed;
- complete `data_ingestion/tests` regression suite: 61 passed;
- five-file smoke profile: 5/5 opened, 2 warnings, 0 errors;
- full profile: 1,702/1,702 opened, 2 warnings, 0 errors, about 30.5 seconds and 109 MiB peak RSS.

No connector was implemented, `timenet-build` was not run, and no TimeF output was created.

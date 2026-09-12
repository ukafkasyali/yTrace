# Human mapping decisions: KUKA Part I to TimeF

This document separates semantic choices from deterministic observations. The implemented source
scope is local Batch 01, but the TimeNet dataset identity and connector discovery model cover Part I.

## Dataset identity

**Decision:** What TimeNet dataset does the connector represent?

**Evidence:** Batch 01 is one five-run archive within the upstream Part I accidental-collision
dataset. The upstream record and its DOI identify Part I independently of packaging batches.

**Chosen interpretation:** `kuka/collision-part1`. Batch membership is provenance, not dataset
identity. Recursive run discovery accepts either a batch directory or a future root containing
multiple batches.

**Confidence:** High.

**Alternative interpretations:** One dataset per batch; one combined Part I/Part II dataset.

**Whether deterministic validation is possible:** The card ID and discovered source subsets can be
checked deterministically. Whether Part I and Part II should remain separate is a semantic policy.

## Record boundary

**Decision:** What constitutes one TimeNet record?

**Evidence:** Each timestamp-named directory has one common time axis, synchronized matrices, one
collision list, and one run README/start time.

**Chosen interpretation:** One experimental run directory becomes one `Record`.

**Confidence:** High.

**Alternative interpretations:** One record per signal, collision, fixed window, or batch.

**Whether deterministic validation is possible:** Yes. Run count, common axes, source files, and
record IDs are validated against `dataset_profile.json`.

## Signal selection

**Decision:** Which torque matrix represents the requested joint-torque stream?

**Evidence:** Several seven-joint torque matrices exist. The upstream feature-generation scripts use
`MsrExtTrq` for collision processing, and the profiler identifies it as measured external joint
torque.

**Chosen interpretation:** Include rows 2–8 of `MsrExtTrq` and rows 2–8 of `PosMsr`. Do not include
`CmdTrq`, `Grt`, `MsrTrq`, force, Jacobian, or mass matrices in this milestone.

**Confidence:** High for `MsrExtTrq`; high for the explicitly requested `PosMsr` inclusion.

**Alternative interpretations:** Measured total torque (`MsrTrq`), commanded torque, all raw
modalities, or only external torque.

**Whether deterministic validation is possible:** Shapes and numerical values are deterministic;
which torque family is semantically primary requires human selection.

## Fourteen scalar series

**Decision:** Should torque and position be two seven-channel tensor series or fourteen scalar
series?

**Evidence:** TimeF supports tensor-valued series through `value_shape`, but its existing
multichannel sensor connectors represent each named lead/signal as one scalar `TimeSeries` sharing a
modality `TimeSeriesSpec`. Scalar series use the default Parquet backend and allow channel-specific
selection and span scoping without defining a tensor dimension convention.

**Chosen interpretation:** Seven scalar `TimeSeries` objects share the external-torque spec and seven
share the joint-position spec. Their signals are `joint_1` through `joint_7`; all fourteen share the
same 1 kHz regular axis.

**Confidence:** High under the pinned TimeF API.

**Alternative interpretations:** Two tensor-valued series with `value_shape=(7,)`, or one combined
fourteen-channel tensor. Those require Zarr and make modality/unit separation less natural.

**Whether deterministic validation is possible:** Series count, specs, channel labels, axes, and
values can be checked. Representation preference is a human modeling decision.

## Time and absolute start

**Decision:** How should time be represented?

**Evidence:** Every run has 232,001 samples from 0 to 232 seconds at an exact observed 1 kHz cadence.
README start times contain only time of day, without a year or timezone.

**Chosen interpretation:** `RegularAxis.from_rate_hz(1000)` with `start_index=0`; `Record.start_time`
remains `None`.

**Confidence:** High.

**Alternative interpretations:** Store every timestamp as an irregular axis; invent a calendar date
or timezone from surrounding context.

**Whether deterministic validation is possible:** Yes for the relative axis. No valid absolute
timestamp can be constructed from the available fields alone.

## Collision events and indexing

**Decision:** How should `JK_moments` be represented?

**Evidence:** The values are MATLAB one-based indices; event counts match README collision entries.
TimeF point annotations preserve raw events without creating supervised examples.

**Chosen interpretation:** Each collision becomes a `collision` point `Annotation`. Its map payload
stores `matlab_index`, `python_index`, `timestamp_seconds`, label, and object. The point span is exactly
`time[matlab_index - 1]` on the record timeline.

**Confidence:** High.

**Alternative interpretations:** Interval annotations, classification labels, localization tasks,
or pre-cut windows.

**Whether deterministic validation is possible:** Yes. Bounds, `i -> i - 1`, timestamps, event counts,
and round-trip payloads are explicitly tested.

## Tasks

**Decision:** Does the raw dataset carry a TimeNet task now?

**Evidence:** TimeF tasks are supervised targets. This milestone requires raw experimental-run
preservation and explicitly excludes windows and classification samples.

**Chosen interpretation:** No task is created.

**Confidence:** High for this milestone.

**Alternative interpretations:** A sparse `TemporalLocalizationTask` for collision detection, scoped
classification windows, or forecasting.

**Whether deterministic validation is possible:** The absence of tasks is testable. Selecting a
future learning task requires human intent and an evaluation protocol.

## Provenance and identity

**Decision:** Where should `source_run_id` and source files live?

**Evidence:** TimeF `Record` has stable identity, `subject_ids`, and annotations, but no arbitrary
structured metadata field. `TimeSeries.source_id` is intended for the raw recording identifier.

**Chosen interpretation:** Use a stable Part I record ID, repeat `source_run_id` as every series'
`source_id`, and add record-wide `source_run_id`, `source_files`, and `source_subset` annotations.
`source_run_id` is not a subject ID.

**Confidence:** High.

**Alternative interpretations:** Put the run in `subject_ids`, encode provenance only in IDs, or keep
it in an external sidecar.

**Whether deterministic validation is possible:** Yes. Uniqueness, annotation recovery, series source
IDs, and source-file lists are validated after TimeF read-back.

## Units

**Decision:** Which value units should the two specs declare?

**Evidence:** The official Part I record states that the seven joint torque signals are in N·m. The
five Batch 01 runs place all seven `PosMsr` channels between approximately -1.439 and +1.449. Read as
radians, those extrema are approximately -82.4 to +83.0 degrees: a plausible trajectory occupying
about 49% of the ±170-degree limits for joints 1, 3, 5, and 7 and about 69% of the ±120-degree limits
for joints 2, 4, and 6. Read as degrees, the same motion would use only about 0.8% to 1.2% of those
limits and would be implausibly small for the observed experiment.

The variable family (`PosMsr`, `MsrTrq`, `MsrExtTrq`, and `CmdTrq`) is consistent with KUKA Fast
Research Interface terminology. FRI documentation specifies radians for commanded joint positions,
and independent LWR work collecting positions through `GetMeasuredJointPositions()` reports them in
radians. A recursive search of every supplied Batch 01 source and metadata file found no `rad2deg`,
`deg2rad`, `180/pi`, or `pi/180` conversion. The original logger implementation is not included, so
the archive provides no direct statement of the position unit.

Evidence references:

- [FastResearchInterface class reference](https://rpc.lirmm.net/rpc-framework/assets/api_doc/html/classFastResearchInterface.html)
  documents measured/commanded joint-position access and radians for commanded joint positions.
- [Identifying the Dynamic Model Used by the KUKA LWR](https://www.diag.uniroma1.it/~labrob/pub/papers/ICRA14_LWR_RevMod.pdf)
  describes positions collected with `GetMeasuredJointPositions()` as radians.
- [KUKA Robot Learning Lab documentation](https://rll-doc.ipr.iar.kit.edu/rll_move_client.html)
  lists the LWR4+ joint limits in radians.

**Chosen interpretation:** External torque uses `newton_meter`; joint position uses `radian`. Every
record carries a small machine-readable `unit_resolution` annotation identifying the position unit as
an inference with high confidence. This records the evidence status without weakening the actual
TimeF unit or introducing a custom schema.

**Confidence:** High for both units. The torque unit is documented; the position unit is inferred.

**Alternative interpretations:** Degrees conflict strongly with the observed ranges and normal FRI
conventions. Leaving the series dimensionless would discard the best-supported physical semantics.

**Whether deterministic validation is possible:** The declared radian unit and inference marker are
checked after round trip. The original position values remain byte-for-byte numerically unchanged.

## Subject identity

**Decision:** Does a run name identify a subject?

**Evidence:** Batch 01 contains no robot serial, operator identity, or stable subject entity.

**Chosen interpretation:** Keep `subject_ids=()`.

**Confidence:** High.

**Alternative interpretations:** Treat the KUKA platform or run directory as a subject.

**Whether deterministic validation is possible:** Empty subject IDs are testable; assigning a shared
robot identity would require external metadata.

## Candidate future ML task

**Decision:** What task does the dataset naturally suggest without implementing it?

**Evidence:** Raw signals have sparse collision time points, and the upstream description names
collision detection as an intended use.

**Chosen interpretation:** A future sparse temporal-localization task is the clearest candidate. It is
not part of this connector milestone.

**Confidence:** Medium-high.

**Alternative interpretations:** Window classification, anomaly detection, collision diagnosis, or
forecasting.

**Whether deterministic validation is possible:** Event targets can be generated deterministically
once a task/window policy is specified; choosing that policy is semantic.

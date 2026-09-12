# KUKA Part II contact batch inspection

The corrected extracted `contact-batch-01` was profiled with:

```bash
PYTHONPATH=src python -m dataset_profiler.cli profile "$KUKA_PART2_ROOT" \
  --dataset-id kuka/contact-part2 --hints kuka-contact-part2 \
  --output outputs/kuka_contact_part2_profile.json
```

The complete result is [`outputs/kuka_contact_part2_profile.json`](../outputs/kuka_contact_part2_profile.json).

## Layout and Part I comparison

The batch contains five timestamp-named runs: `03-22-10-45`, `03-22-10-52`, `03-22-10-58`,
`03-22-11-04`, and `03-22-11-10`. Each has the ten expected KUKA MAT files, `ReadMe.txt`, and
`GenMoments.m`; three runs also contain non-required incidental files (`ReadMe.txt~` or `matlab.mat`).
One run maps to one TimeF record with fourteen scalar series.

| Aspect | Part II observation | Comparison to local Part I Batch 01 |
| --- | --- | --- |
| MAT variables | Same expected ten variables: `CmdTrq`, `Grt`, `Jcb`, `Mass`, `MsrExtTrq`, `MsrFrc`, `MsrTrq`, `PosMsr`, `JK_moments`, `rt_tout` | Same required filenames and variable names. An incidental `matlab.mat` contains a scalar `JK_moments` and is not a run input. |
| `MsrExtTrq` | `float64`, `8 × N`: time + seven joints; `N` is 170001 in four runs and 102455 in one | Same time-plus-seven-joints representation, but unlike the 232001-sample Part I runs, values and lengths differ. |
| `PosMsr` | `float64`, `8 × N`, aligned with `MsrExtTrq` | Same representation; values and lengths differ. |
| Time axis | `rt_tout` is `N × 1`, starts at 0; matrix row 0 matches it | Same layout. Four runs end at 170 s; one ends at 102.454 s. |
| Sampling | Exactly 1 kHz | Same. |
| Run structure | Five `03-22-*` directories; 12 required files each | Different timestamps and incidental-file inventory. |
| `JK_moments` | `int32`, one-based vectors of 31, 36, 36, 36, and 19 markers (158 total) | Same one-based representation; values/counts differ. |

The corrected payload is not a duplicate: root-normalized SHA-256 comparison against local Part I
finds different run names and MAT payloads. Profiler warnings describe expected variable event-vector
lengths, one shorter final run, and repeated identical time-axis files; they are not conversion errors.

## Connector and mapping

`KukaContactPart2Connector` is a 17-line identity subclass of the shared 221-line
`KukaPartConnector`. It reuses all MAT loading, discovery, time-axis/matrix validation, variable
length handling, series construction, and MATLAB-index conversion; no MAT decoder is duplicated.

The separate card is `kuka/contact-part2`. It retains Part I's `float64` measured-external-joint-
torque and radian joint-position representation. Every `JK_moments` item becomes an
`intentional_contact` point annotation with original `matlab_index`, zero-based `python_index`,
`timestamp_seconds`, and `source_variable`/`source_file` provenance. It adds no windows, tasks,
normalization, or cross-part merge.

## Validation and unresolved semantics

[`outputs/kuka_contact_part2_timef_validation.json`](../outputs/kuka_contact_part2_timef_validation.json)
passes all 73 checks after a TimeF write/read round trip: five records, fourteen series each, 1 kHz,
all 158 point annotations, retained provenance, and no tasks. It performs ten complete exact raw
MAT comparisons (seven channels × each run's native length), plus marker conversion checks. The test
suite also includes a synthetic Part II build/read/validate round trip.

The Part II identity and annotation label follow its intentional-contact dataset designation. The
per-run README still says `Collision(ball)`, so no contact object, direction, or subtype is inferred.
Other unresolved fields are robot serial/model, explicitly encoded units (position radians remain an
informed mapping), Jacobian/mass flattening order, date/timezone, and ML task/evaluation protocol.
Raw marker timestamps are preserved; they are not replaced with whole-second README timings.

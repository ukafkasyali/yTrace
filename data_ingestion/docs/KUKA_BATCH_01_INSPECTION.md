# KUKA Part I Batch 01 inspection

## Scope and layout

The inspected source is referred to as `$KUKA_PART1_ROOT`. It is already extracted; no archive is present inside that directory. It contains five timestamp-named run directories:

- `03-15-12-53`
- `03-15-13-08`
- `03-15-13-13`
- `03-15-13-18`
- `03-15-13-24`

Each run contains the same ten MAT filenames, a `ReadMe.txt`, and a `GenMoments.m`. The 60 source files are not copied into this repository. The broader dataset documentation is in the separately downloaded `robot-raw-collision-signals` repository and says Part I contains 42 packages; this milestone intentionally uses only the locally extracted Batch 01.

## Observed facts

- All five runs contain exactly 232,001 timestamp samples spanning 0–232 seconds.
- The timestamps are strictly increasing with a 0.001 second interval (1 kHz) in every run.
- Eight signal families use rows-by-samples matrices. Their first row exactly equals `rt_tout`.
- `CmdTrq`, `Grt`, `MsrExtTrq`, `MsrTrq`, and `PosMsr` are 8 × 232,001.
- `MsrFrc` is 7 × 232,001, `Jcb` is 43 × 232,001, and `Mass` is 50 × 232,001.
- `JK_moments` is a column vector containing 27, 27, 28, 28, and 29 integer-valued indices across the five runs.
- The large signal files are MATLAB v4; `JK_moments.mat` and `JsmoExp.mat` are MATLAB v5.
- No inspected numeric array contains NaN or infinity.
- The five run READMEs identify executable `C1`, a start time, and 27–29 `Collision(ball)` wall-clock entries.
- The event counts in the READMEs match the `JK_moments` counts.

## Evidence-backed interpretations

- A timestamped directory is one experimental run: all contained matrices share one time axis and the directory has one start time and event list.
- Rows 2–8 in the 8-row torque/position matrices are seven robot-joint channels. This follows from the dataset description and the included MATLAB plotting example (`a(1,:)` against `a(2:8,:)`).
- `JK_moments` values are MATLAB one-based collision sample indices. Dividing their zero-based offsets by 1 kHz aligns them with the README collision times.
- `MsrFrc` likely contains six force/torque channels after its time row; `Jcb` and `Mass` likely contain flattened robot-model quantities after their time rows. Exact ordering and physical units are not asserted.

## Unresolved questions

- Robot model, serial number, and other hardware identifiers are absent from Batch 01.
- Units are not encoded in the MAT files.
- The flattening order of Jacobian and mass-matrix values is not documented here.
- The run date lacks a year and timezone.
- The intended prediction target and evaluation protocol are not part of the raw batch.

The generated JSON keeps observations separate from confidence-scored interpretations and includes stable run IDs and source paths so future windows can retain `source_run_id`, `window_start`, and `window_end`.

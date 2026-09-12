# Clean TimeNet verification

On 12 September 2026, a fresh Python 3.14 environment built and loaded both datasets with TimeNet pinned to `c39ca32b64ad0c89ea54093dbcb285c1a93eb006`. Both persisted datasets passed the existing raw-array and annotation validators. This is a full-recording round trip for **one recording per part**, not a claim that the complete 445-recording corpus was rebuilt.

- Part I: `03-15-12-53`, source [Zenodo 21927431](https://zenodo.org/records/21927431).
- Part II: `03-22-11-18`, source [Zenodo 21941203](https://zenodo.org/records/21941203).
- Each source contains the original `JK_MsrExtTrq.mat`, `JK_PosMsr.mat`, `JK_moments.mat`, and `JsmoExp.mat` files. No reconstructed arrays were substituted.
- [Receipt](receipt.json): SHA-256 and sizes of every input and built registry file.
- [Part I validation](part1-validation.json) and [Part II validation](part2-validation.json): machine-readable checks, including values after writer/reader round trip.
- [Resolved environment](environment.txt). Profiles retain observed facts; their machine-specific source root is replaced by `SOURCE_ROOT`.

Run from the repository root after obtaining the four original files in each recording directory. Use a new output directory; this example assumes sources under `SOURCE/collision/03-15-12-53` and `SOURCE/contact/03-22-11-18`:

```sh
python3 -m venv /tmp/trace-proof-venv
/tmp/trace-proof-venv/bin/python -m pip install -e 'data_ingestion[dev]'
export TRACE_PROOF_PY=/tmp/trace-proof-venv/bin/python
export TRACE_SOURCE=/path/to/SOURCE
export TRACE_PROOF=/tmp/trace-proof
mkdir -p "$TRACE_PROOF"

"$TRACE_PROOF_PY" -m dataset_profiler.cli profile "$TRACE_SOURCE/collision" \
  --dataset-id kuka/collision-part1 --hints kuka-collision --output "$TRACE_PROOF/part1-profile.json"
"$TRACE_PROOF_PY" -m dataset_profiler.cli profile "$TRACE_SOURCE/contact" \
  --dataset-id kuka/contact-part2 --hints kuka-contact-part2 --output "$TRACE_PROOF/part2-profile.json"
"$TRACE_PROOF_PY" data_ingestion/scripts/build_kuka_timef_dataset.py \
  "$TRACE_SOURCE/collision" "$TRACE_PROOF/registry" --part part1
"$TRACE_PROOF_PY" data_ingestion/scripts/build_kuka_timef_dataset.py \
  "$TRACE_SOURCE/contact" "$TRACE_PROOF/registry" --part part2
"$TRACE_PROOF_PY" data_ingestion/scripts/validate_kuka_timef.py "$TRACE_PROOF/registry" \
  --profile "$TRACE_PROOF/part1-profile.json" --source "$TRACE_SOURCE/collision" \
  --output "$TRACE_PROOF/part1-validation.json"
"$TRACE_PROOF_PY" data_ingestion/scripts/validate_kuka_contact_part2_timef.py "$TRACE_PROOF/registry" \
  "$TRACE_PROOF/part2-profile.json" --source "$TRACE_SOURCE/contact" \
  --output "$TRACE_PROOF/part2-validation.json"
```

The validators instantiate `TimeNet(registry=...).load(dataset_id)` and inspect the resulting records. The separate dataset identities preserve collision/contact annotation meaning. This connector proof and the model's prepared-window evaluation are separate artifacts; the current training run is not claimed to have consumed these newly built registry files.

The clean check also found that the generic profiler could treat sorted event indices as timestamps when the explicit clock was absent. It now requires an identified time variable and leaves timing unknown otherwise. The KUKA connector still requires the original clock file.

# Generic approved-source ingestion acceptance

Verified on 13 September 2026. These checks cover the on-demand path from an approved manifest to
bounded inventory, explicit mapping, TimeF read-back, and a public receipt. They do not execute
downloaded code and do not claim that every dataset has an automatically inferable schema.

| Boundary | Input | Evidence |
| --- | --- | --- |
| GitHub | Mocked pinned blob containing CSV | `test_provider_intake.py`; commit/blob/size verified, then CSV inventoried |
| Zenodo | Mocked immutable record file containing CSV | `test_provider_intake.py`; provider checksum/size verified, then CSV inventoried |
| Hugging Face | Mocked pinned dataset revision containing Parquet | `test_provider_intake.py`; revision/LFS hash verified, then Parquet inventoried |
| Tabular formats | CSV, TSV, Parquet | `test_tabular_formats.py`; bounded schemas, malformed/oversized failures, projected reads and source rows |
| Numeric containers | NPY, NPZ, MAT v5, HDF5 | `test_array_formats.py`; numeric-only traversal/reads, transposed arrays and unsafe object rejection |
| Canonical onboarding | Wide CSV with unresolved units | `test_ingestion_onboarding.py`; verified acquisition becomes `SourceDescriptor`, the persisted orchestrator pauses, HTTP human approval resumes the same job, then TimeF load/receipt/catalog complete |
| Generic TimeF | Wide CSV, long CSV, named NPZ | `test_generic_import.py`; lower-level exact record, channel, unit, value and irregular-time read-back |
| KUKA dispatch | Part I and Part II connector fixtures | `test_specialized_dispatch.py`; exact identities remain separate and retain 14 series, 1 kHz axes and publisher annotations |

`test_mapping.py` records the structural selection boundary: ambiguous selectors cannot advance,
while a confirmed mapping is revision-bound and immutable. Unknown units use the orchestrator's
human-resolution flow and existing `ImplementationOverrideArtifact` validation. `test_generic_import.py`
recomputes the public receipt hash and verifies that it contains no machine-specific cache path.
The job store's unique approved-source constraint and mapping conflict tests prevent replacement or
a second logical ingestion; a ready job is returned rather than run again.

The provider cases above are deterministic mocks, not live downloads. Existing audited KUKA
read-back artifacts in `docs/submission/timenet/` cover one original full recording per part. The
multi-gigabyte full KUKA archives were not downloaded or rebuilt for this acceptance run, so this is
not a fresh full-batch or full-corpus validation.

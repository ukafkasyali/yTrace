# TimeNet interface note for KUKA Batch 01

TimeNet commit studied: `c39ca32b64ad0c89ea54093dbcb285c1a93eb006`.

The in-repository implementation is authoritative. TimeNet is installed externally from this exact
Git commit through `data_ingestion/pyproject.toml`; it is neither vendored nor modified.

The approved connector identity is `kuka/collision-part1`. Batch 01 is the local validation subset,
not part of the dataset ID.

## Connector and build lifecycle

`BaseConnector[TRaw]` separates source discovery from conversion:

- `download(cache_dir) -> list[TRaw]` performs idempotent I/O and returns lightweight raw references.
- `convert(raw_refs) -> TimeFDataset` parses those references without network access.
- inherited `metadata()` validates the adjacent `dataset.yaml` card.
- inherited `store()` derives the schema when needed and persists the dataset with `TimeFWriter`.

The hackathon application invokes its connector directly through
`dataset_profiler.timenet.build_kuka_collision_part1`. That integration performs source discovery,
conversion, and `store_dataset` without depending on TimeNet's separate connector registry or build
CLI. The connector still subclasses `BaseConnector` and exports `CONNECTOR`, keeping native-TimeNet
compatibility testable without making native discovery the product path. The adjacent card provides
the dataset id, semantic version, description, license, domains, tags, and source metadata.

## Data model relevant to KUKA

- A `TimeFDataset` contains records and optional tasks. A record is a logical recording or session.
- A record contains a tuple of `TimeSeries` objects, so synchronized modalities and channels coexist
  in one record. Existing multichannel sensor connectors, such as the PTB-XL connector, use one scalar
  `TimeSeries` per signal/lead under a shared `TimeSeriesSpec` rather than one tensor-valued series.
- `TimeSeriesSpec` identifies a modality and requires a value unit. `signal` identifies a channel.
  `source_id` retains a raw recording identifier, while an explicit `time_series_id` makes identity and
  writer deduplication deterministic.
- `RegularAxis.from_rate_hz(1000)` represents the KUKA cadence exactly as a rational 1,000 us period.
  With 232,001 samples beginning at zero, the last sample lies at 232 seconds.
- A point event is an `Annotation` with a `TimePoint` span. A span uses integer microseconds on the
  source-recording timeline and may scope itself to named time-series IDs. A record-wide fact is an
  annotation without a span; records have no separate arbitrary metadata dictionary.
- `subject_ids` represent actual subjects. They should remain empty when the source exposes no robot,
  operator, or other subject identity. A run identifier belongs in `record_id`, `source_id`, and
  provenance annotations rather than being relabeled as a subject.
- Tasks are supervised targets, not required record content. Preserving collision points as raw
  annotations does not require creating a task. A future event-detection interpretation could add a
  sparse `TemporalLocalizationTask`, but that is outside the raw-run milestone.

`TimeFWriter` persists records, annotations, time-series indices, tasks, and values as manifest-listed
Parquet shards by default. `TimeFReader`, normally reached through `TimeNet(registry=...).load(...)`,
reconstructs the dataset with lazy value loaders; consumer reads do not import or execute connector
code.

## Implementations and tests inspected

- `timenet/hello-world`: raw `BaseConnector`, deterministic offline discovery, multiple modalities,
  regular axes, static/point/interval annotations, provenance IDs, tasks, and writer/reader tests.
- `physionet/ecg-qa-cot`: `BasePhysioNetConnector`, one record per source recording, one scalar series
  per ECG lead, lazy file loaders, record-wide metadata annotations, stable source IDs, and an explicit
  TimeF round-trip test.
- `chengsenwang/tsqa` and its fixture test were also reviewed for the minimal folder/card/export pattern
  and direct offline testing of `convert()`.

Connector tests instantiate the connector, pass checked-in or generated local fixtures directly to
`convert()`, and assert record, series, annotation, task, identity, and value semantics without a
network dependency. Round-trip tests derive the schema, store the dataset, reopen the resulting
version with `TimeFReader` or `TimeNet`, and compare reconstructed content.

## Verified upstream round trip

The offline reference connector completed the full path at the commit above. It produced a Parquet
TimeF version with a manifest, 3 records, 4 distinct stored time series, 4 annotations, and 5 tasks;
the SDK loaded it and materialized the expected leading sine values.

That upstream smoke test was performed during API evaluation. Product reproduction now happens from
the hackathon repository alone; see `data_ingestion/README.md`. The application dependency and
`data_ingestion/timenet.lock` both pin the same immutable TimeNet commit, so no developer-local
checkout participates in installation or execution.

Historical upstream API smoke-test commands were:

```bash
make sync
export TIMENET_HOME=/tmp/timenet-hello-world-home
uv run timenet-build build timenet/hello-world \
  --out /tmp/timenet-hello-world-registry
uv run python -c \
  "from timenet.client import TimeNet; TimeNet(registry='/tmp/timenet-hello-world-registry').load('timenet/hello-world').describe()"
```

The local Codex shell did not initially expose `uv`; verification used a temporary `uv` bootstrap and
TimeNet's committed `uv.lock`. This affects only the shell setup, not the build or TimeF result.

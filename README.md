# y/trace
Replay-only robot contact-event investigation for the EHL Zurich Temporal AI Challenge.

Start with the [submission evidence pack](docs/submission/README.md), [audited comparison](docs/submission/evaluation/report/comparison.md), and [two-minute demo](docs/submission/DEMO.md).


## y/trace replay workbench

Samet’s frontend lives in [frontend/](frontend/README.md). Run `cd frontend && npm ci && npm run dev`. Model and ingestion integration contracts are in [docs/FRONTEND_INTEGRATION.md](docs/FRONTEND_INTEGRATION.md).

The evidence-complete dataset scout lives in [data_sourcing/](data_sourcing/README.md). Its API
contract is documented in [docs/DATA_SOURCING_API.md](docs/DATA_SOURCING_API.md); it produces an
approved source manifest but does not ingest data.

Start the scout, ingestion API and worker, fixture inference bridge, and frontend together in
zero-credit cached mode:

```bash
./scripts/run-local.sh --open
```

For real Tavily and OpenAI testing, first configure `data_sourcing/.env`, then run
`./scripts/run-local.sh --live --open`. Press Ctrl-C to stop all local services. The launcher installs
missing dependencies automatically; run it with `--help` for all options.

Entire checkpoint capture is configured for Codex. Approve the installed commands in Codex `/hooks` and restart/resume to enable automatic capture. Check `entire status` before relying on it.

## Deterministic dataset profiler

The deterministic MATLAB inspection, profiling, auditing, and visualization project lives in [data_ingestion/](data_ingestion/README.md).

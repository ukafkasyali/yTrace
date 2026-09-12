# Trace
Replay-only robot contact-event investigation for the EHL Zurich Temporal AI Challenge.

Start with the [submission evidence pack](docs/submission/README.md), [audited comparison](docs/submission/evaluation/report/comparison.md), and [two-minute demo](docs/submission/DEMO.md).


## Trace replay workbench

Samet’s frontend lives in [frontend/](frontend/README.md). Run `cd frontend && npm ci && npm run dev`. Model and ingestion integration contracts are in [docs/FRONTEND_INTEGRATION.md](docs/FRONTEND_INTEGRATION.md).

Entire checkpoint capture is configured for Codex. Approve the installed commands in Codex `/hooks` and restart/resume to enable automatic capture. Check `entire status` before relying on it.

## Deterministic dataset profiler

The deterministic MATLAB inspection, profiling, auditing, and visualization project lives in [data_ingestion/](data_ingestion/README.md).

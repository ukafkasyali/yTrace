# ysamet
Our working repo for the EHL Zurich Hackathon 


## Trace replay workbench

Samet’s frontend lives in [frontend/](frontend/README.md). Run `cd frontend && npm ci && npm run dev`. Model and ingestion integration contracts are in [docs/FRONTEND_INTEGRATION.md](docs/FRONTEND_INTEGRATION.md).

Entire checkpoint capture is configured for Codex. Approve the installed commands in Codex `/hooks` and restart/resume to enable automatic capture. Check `entire status` before relying on it.

## Deterministic dataset profiler

The deterministic MATLAB inspection, profiling, auditing, and visualization project lives in [data_ingestion/](data_ingestion/README.md).

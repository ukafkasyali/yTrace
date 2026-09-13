# Verification, 13 September 2026

Base: main `22fa8e5`; final hardening is on `codex/final-submission-hardening` in PR #22. Entire is enabled for the worktree; Codex hooks are installed and approval records are present.

## Checks performed

| Check | Result |
|---|---|
| Comparison replay, Python standard library | Checksums, identical IDs/targets, test-session membership and manifests passed; 512 windows / 67 recordings |
| Independent comparison rerun | JSON and Markdown byte-identical with fixed bootstrap seed; 512 windows / 67 recordings |
| Training focused suite | 54 passed; the TimeNet connector and training-observability modules additionally require TimeNet and TensorBoard in this environment |
| Data sourcing, `.venv/bin/python -m pytest -q` | 92 passed; only the existing Starlette/AnyIO deprecation warning was emitted |
| Data ingestion, `.venv/bin/python -m pytest tests -q` | 101 passed in the component virtual environment, including TimeNet coverage |
| Inference, `python3 -m unittest discover -s inference -p 'test_*.py' -v` | 28 passed with temporary localhost server permission |
| Frontend, `npm test` | 92 passed, including strict structured-prediction validation, telemetry-scoped browser-session recovery, readable-export isolation, model/measurement cross-checks, investigation provenance and replay contracts |
| Frontend, `npm run build` | Passed; existing optional 3D chunk remains over Vite's 500 kB advisory threshold |
| Clean environment dependency compatibility | `pip check` passed |
| Real TimeNet round trip | Both dataset parts passed 17 checks each, including all torque/position values and annotation index/time conversion |
| Private deployed model | Ready; expected canary-v4 SHA-256; real `inference.smoke` passed |
| Browser, desktop | Structured prediction, J4/J2 cross-check, generated 3D onset cue, complete seven-channel evidence, reference comparison and Markdown export verified through the live UI. Immediate and active-query cancellation held the control until server acknowledgement, then a new query started successfully. A real free-motion result restored only on its matching case and did not appear after switching to the collision interval |
| Browser, 390 × 844 | Replay, robot context, evaluation summary, horizontally scrollable result table and charts remained usable without clipping; the temporary viewport override was reset |
| Browser, cached sourcing path | Research contract, 47 native evidence records, 8/8 mandatory gates, approval and revision-pinned manifest completed with 0 search credits. The submission UI now ends at an explicit manifest handoff and states that browser acquisition is not connected |
| Actual downloaded investigation JSON | Verified 1,024 raw samples/channel, seven measurement rows, one in-window publisher marker, expected checkpoint and matching model receipt |

The complete optional training suite cannot collect the TimeNet connector test without TimeNet or the training-observability test without TensorBoard in this environment. The 54-test focused suite includes the remaining training tests; it is not a claim that GPU training was rerun. The live smoke confirms actual inference with the existing deployed checkpoint.

The clean environment also exposed a pre-existing macOS test-path comparison (`/var` versus resolved `/private/var`); the assertion now compares resolved paths. PyArrow printed restricted CPU-cache probes under the sandbox but completed build/load/validation successfully.

## Integration boundary

PR #19 joins approved dataset sourcing to one-shot ingestion and remains outside the frozen two-minute demo. In a detached worktree, 86 frontend, 98 sourcing, 116 ingestion and 28 inference tests passed. Review also found that its cached manifest contains placeholder archive IDs, imported TimeF data is not added to the Replay catalog, and its fallback launcher disables model loading. The live full-corpus acquisition was not attempted because the approved source manifest is about 18 GB. PR #22 is this isolated final-hardening branch. Training artifacts are scored from their saved predictions; training processes and model deployment were not modified. CNN results remain evaluation-only because no CNN checkpoint or inference endpoint is connected.

## Remaining evidence gaps

- No timed engineer study, customer testimonial or measured ROI.
- No plain-Llama numerical/text baseline; Qwen is the available zero-shot comparator and has an explicit contact-field failure.
- No newly untouched confirmatory test after the team inspected these results.
- No full-corpus TimeNet rebuild in this verification; two full recordings were validated.
- Checkpoint delivery to the challenge is still an external submission step. The backbone file identity is resolved by hash in `CHECKPOINT_HANDOFF.md`, but the live receipt still reports an unknown backbone revision.
- Raw demo coverage is limited to bundled excerpts from three recordings; no live control, verified root cause or physical contact localization.

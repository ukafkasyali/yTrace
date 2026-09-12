# Verification, 12 September 2026

Base: main `126a8c6`. Entire enabled for the worktree; Codex hooks installed and approval records present. Only intended product, test and submission files are included.

## Checks performed

| Check | Result |
|---|---|
| Comparison replay, Python standard library | Checksums, identical IDs/targets, test-session membership and manifests passed; 512 windows / 67 recordings |
| Independent comparison rerun | JSON and Markdown byte-identical with fixed bootstrap seed |
| New comparison regressions | 15 passed: invalid/missing answers, boolean/null/nonnumeric onset, mismatched targets, IDs and test sessions |
| Training focused suite | 25 passed; `test_checkpoints.py` excluded because optional PyTorch is absent in the clean local environment |
| Data ingestion, `pytest data_ingestion/tests -q` | 50 passed, including TimeNet tests and missing-clock regression |
| Inference, `python3 -m unittest discover -s inference -p 'test_*.py' -v` | 22 passed, with localhost server permission |
| Frontend, `npm test` | 37 passed, including investigation provenance, half-open marker boundaries, reduced-resolution labeling and frozen replay horizon |
| Frontend, `npm run build` | Passed; existing optional 3D chunk remains over Vite's 500 kB advisory threshold |
| Ruff, new comparison module/tests | Passed |
| Clean environment dependency compatibility | `pip check` passed |
| Real TimeNet round trip | Both dataset parts passed 17 checks each, including all torque/position values and annotation index/time conversion |
| Private deployed model | Ready; expected canary-v4 SHA-256; real `inference.smoke` passed |
| Browser, 1280×720 desktop and 390×844 mobile | Real automatic model answer, evidence control and export visible; mobile document width 390 px with no horizontal overflow |
| Actual downloaded investigation JSON | Verified 1,024 raw samples/channel, seven measurement rows, one in-window publisher marker, expected checkpoint and matching model receipt |

The complete optional training suite initially could not collect `test_checkpoints.py` without PyTorch. The focused suite above is the completed local check, not a claim that GPU training or that checkpoint test was rerun. The live smoke confirms actual inference with the existing deployed checkpoint.

The clean environment also exposed a pre-existing macOS test-path comparison (`/var` versus resolved `/private/var`); the assertion now compares resolved paths. PyArrow printed restricted CPU-cache probes under the sandbox but completed build/load/validation successfully.

## Integration boundary

PR #3 was the only open PR and GitHub reported it mergeable against main. Its scout, service contracts and data workspace are preserved as separate work. The current task does not claim its test results as freshly rerun. Training-branch artifacts are scored from their original saved predictions; training processes and model deployment were not modified.

## Remaining evidence gaps

- No timed engineer study, customer testimonial or measured ROI.
- No plain-Llama numerical/text baseline; Qwen is the available zero-shot comparator and has an explicit contact-field failure.
- No newly untouched confirmatory test after the team inspected these results.
- No full-corpus TimeNet rebuild in this verification; two full recordings were validated.
- Checkpoint delivery to the challenge is still an external submission step, and the live backbone revision is unknown.
- No multi-recording raw-window demo, live control, verified root cause or physical contact localization.

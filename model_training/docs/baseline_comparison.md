# Held-out baseline comparison

All results below use the same 512 test windows selected without replacement with seed `20260912`.
Training and prompt-demonstration selection use only the train and validation splits.

## Results recorded on 2026-09-13

- **Signal features:** semantics macro-F1 `0.9880`, contact F1 `0.9908`, median onset error
  `21 ms`, and strongest-joint accuracy `0.8413`.
- **Multi-task 1D CNN:** semantics macro-F1 `0.9751`, contact F1 `0.9777`, median onset error
  `18 ms`, and strongest-joint accuracy `0.7565`.
- **OpenTSLM canary:** semantics macro-F1 `0.8845`, contact F1 `0.9871`, median onset error
  `54 ms`, strongest-joint accuracy `0.7528`, and strict parse validity `0.7793`.
- **Qwen3-VL 4B direct zero-shot:** semantics macro-F1 `0.6951`, contact F1 `0.0291`, median
  onset error `42 ms`, and strongest-joint accuracy `0.4244`.

The transparent feature baseline remains the strongest event classifier and joint-attribution
baseline. The CNN is the strongest learned classifier, has the best median onset error, and avoids
the structured-output failures seen in OpenTSLM. The zero-shot plot model is substantially weaker,
especially for contact detection. Mean and tail onset errors should be considered alongside the
median: the CNN has `86.6 ms` MAE and `274 ms` P90 error.

## Reproducibility

The CNN was selected on validation macro-F1 with onset MAE as the tie-breaker and evaluated on test
only after selection. It trained on a fresh Nebius L40S in 64.3 seconds and selected epoch 23. The
checkpoint SHA-256 is `67e883b997c415bef710275deab30033539a30d3a3534029f1971f36614cdf81`.

The one-shot direct-LLM evaluator is included in this branch. Its single labeled example is selected
deterministically from the train split, recorded in `run_manifest.json`, and reused for every test
query. Its result should be appended here after the queued H100 evaluation completes.

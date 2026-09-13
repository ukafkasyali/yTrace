# Held-out baseline comparison

All results below use the same 512 test windows selected without replacement with seed `20260912`.
Training and prompt-demonstration selection use only the train and validation splits.

## Results recorded on 2026-09-13

- **Signal features:** semantics macro-F1 `0.9880`, contact F1 `0.9908`, median onset error
  `21 ms`, and strongest-joint accuracy `0.8413`.
- **Multi-task 1D CNN:** semantics macro-F1 `0.9751`, contact F1 `0.9777`, median onset error
  `18 ms`, onset MAE `86.6 ms`, onset P90 `274 ms`, strict parse validity `1.0000`, and
  strongest-joint accuracy `0.7565`.
- **OpenTSLM canary:** semantics macro-F1 `0.8845`, contact F1 `0.9871`, median onset error
  `54 ms`, onset MAE `68.5 ms`, onset P90 `140 ms`, strict parse validity `0.7793`, and
  strongest-joint accuracy `0.7528`.
- **Qwen3-VL 4B direct zero-shot:** semantics macro-F1 `0.6951`, contact F1 `0.0291`, median
  onset error `42 ms`, and strongest-joint accuracy `0.4244`.

The transparent feature baseline remains the strongest event classifier and joint-attribution
baseline. Against the OpenTSLM canary, the CNN wins event semantics, median onset error, and output
reliability. OpenTSLM slightly wins contact F1 and clearly wins onset MAE and P90, while their
strongest-joint accuracies are effectively tied. The CNN therefore does not win every downstream
metric; its main weakness is a long tail of onset outliers. The zero-shot plot model is
substantially weaker, especially for contact detection.

## Interpretation and comparison limits

The CNN result is internally consistent. A Conv1D can detect local torque shapes directly, each
target has a dedicated supervised head and loss, all `29,393` training examples were used, and the
model emits typed values without a generative parsing step. Validation semantics macro-F1 reached
`0.9901`; the locked test value was `0.9751`, and early stopping selected epoch 23 using validation
macro-F1 with onset MAE as the tie-breaker. No obvious training failure is visible in the recorded
learning curve.

The OpenTSLM row is not yet a fair full-budget comparison. It is explicitly a canary result from a
three-epoch, approximately `8,192`-example run. It optimizes language-model token loss over both the
answer and generated evidence/formatting text, and its checkpoint is selected by validation token
loss rather than a downstream metric or parse validity. Training uses a mix of summary and atomic
prompts, while the reported evaluation uses summary prompts. On the warm-start path,
`train_opentslm.py` enables LoRA through `OpenTSLM.load_pretrained(...)` without explicitly applying
the rank, alpha, and dropout values from YAML.

The OpenTSLM result is also not fully traceable from this branch: its run manifest, learning curve,
raw predictions, and checkpoint are absent. The quoted metric set came from a scorer revision that
is not committed here. In that revision, malformed contact values were penalized by contact
accuracy but could behave as negative predictions in the binary F1 calculation, so the reported
contact accuracy and F1 are not mathematically contradictory; they are nevertheless not cleanly
comparable to metrics produced by the evaluator currently on this branch.

The supported conclusion is that the CNN is a valid strong baseline, not that it fundamentally
beats OpenTSLM. That claim requires a full-budget OpenTSLM run, downstream-metric checkpoint
selection, the same strict typed scorer and record IDs, and complete evaluation artifacts.

## Reproducibility

The CNN was selected on validation macro-F1 with onset MAE as the tie-breaker and evaluated on test
only after selection. It trained on a fresh Nebius L40S in 64.3 seconds and selected epoch 23. The
checkpoint SHA-256 is `67e883b997c415bef710275deab30033539a30d3a3534029f1971f36614cdf81`.

The one-shot direct-LLM evaluator is included in this branch. Its single labeled example is selected
deterministically from the train split, recorded in `run_manifest.json`, and reused for every test
query. Its result should be appended here after the queued H100 evaluation completes.

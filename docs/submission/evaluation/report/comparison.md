# y/trace: audited held-out comparison

512 identical windows from 67 held-out recordings. Class counts: {'accidental': 118, 'free': 241, 'intentional': 153}. All windows are 1,024 samples at 1 kHz.

| Task / metric | Signal features | OpenTSLM | Qwen plots | Test size / limitation |
|---|---:|---:|---:|---|
| Semantics macro-F1 | 0.9880 | 0.8845 | 0.6951 | 512; three experimental classes; abstentions are errors |
| Semantics accuracy | 0.9883 | 0.7676 | 0.7461 | 512; missing answers incorrect |
| Contact macro-F1 | 0.9902 | 0.8332 | 0.0145 | 512; includes free-motion failures |
| Contact answer coverage | 1.0000 | 0.7793 | 0.0078 | 512; requires a JSON boolean |
| Usable complete summary | 0.9707 | 0.7793 | 0.0078 | 512; types, ranges and cross-field consistency |
| Strongest-joint accuracy | 0.8413 | 0.7528 | 0.4244 | 271 contact windows; derived signal target |
| Affected-joint set F1 | 0.8228 | 0.8156 | 0.4396 | 271 contacts only; invalid lists score zero |
| Onset MAE (ms) | 70.73 | 68.51 | 118.34 | Only finite in-window predictions; read with coverage |
| Onset median error (ms) | 21.00 | 54.00 | 42.00 | Same conditional denominator as MAE |
| Onset P90 error (ms) | 188.60 | 140.00 | 313.80 | Same conditional denominator as MAE |
| Onset coverage | 0.9410 | 0.9889 | 0.7860 | 271 contact windows |
| Onset within 50 ms / all contacts | 0.5535 | 0.4649 | 0.4133 | 271; missing/invalid onsets fail |

## Interpretation

The signal-feature baseline outperforms this OpenTSLM checkpoint on semantics, joint ranking, and successful localization within 50 ms. OpenTSLM has higher onset coverage and slightly lower conditional mean onset error, but a worse median. These results do not establish OpenTSLM superiority.

OpenTSLM returned no usable structured answer for 113 of 512 windows. That includes 112 of 241 free-motion windows (46.5%). On the 399 answered windows, semantics accuracy is 98.50% (393/399). This conditional figure is descriptive, not a paired comparison: the model selects which windows receive an answer, while the baseline answers every window. Any reliability fix must be selected on validation.

OpenTSLM was fine-tuned; Qwen3-VL 4B was zero-shot on plots. This comparison changes training and representation together. Qwen frequently emitted null contact fields: JSON parsing and matching keys did not mean usable answers. No plain-Llama baseline was run in these artifacts.

Legacy positive-contact F1 can stay high while free-motion answers are missing. This report adds contact macro-F1, answer coverage, strict usable-summary rate, and abstention columns in the JSON confusion matrices. It does not repair generations or substitute labels.

## Paired uncertainty

95% percentile intervals for OpenTSLM minus signal features, resampling recording groups (1000 replicates). Negative values favor signal features.

- semantics_accuracy: [-0.2538, -0.1896]
- contact_accuracy: [-0.2555, -0.1914]
- usable_summary_rate: [-0.2312, -0.1558]
- onset_within_50ms_all_contacts: [-0.2008, 0.0160]

## Scope and provenance

The 512 windows are a fixed subset of 4,202 test windows. The source split assigns 311/67/67 recordings to train/validation/test. Every prediction is joined by record ID, checked against the prepared target and test-session assignment, and matched across all four input conditions. Archived input bytes are checksum-verified before scoring. The OpenTSLM evaluation did not originally emit an input manifest; identity is reconstructed from its prediction IDs and targets.

One robot, controlled interactions, unknown subject identities, and implement/class confounding limit generalization. Joint and evidence targets are formulas, not physical contact-location truth. Previously inspected test results are retrospective evidence, not a new untouched confirmation set. Select any further model changes on validation; reserve new recording groups for future confirmation.

Checkpoint SHA-256: `8ff63b84ae5b64758f66b3e0527f4f225a04bb2b6b39193c806f58d94cec9f23`.

Reproduce from repository root (Python standard library only):

```sh
PYTHONPATH=model_training/src python3 -m robot_observability.comparison \
  --source docs/submission/evaluation/source \
  --output /tmp/trace-comparison
```

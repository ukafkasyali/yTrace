# OpenTSLM one-hour follow-up

Run date: 2026-09-13

## Selected checkpoint

`llama-har-sp-timef-rationale-focused-v6` completed at step 3,823. Its minimum recorded
validation loss was 0.0782779 at step 3,600. The selected checkpoint was preserved on the GPU
instance as both `best_model.pt` and `best_validation_step_003600.pt`; both have SHA-256:

`7c69114d54132dd2641d59a54dc97d435ab7c578dfb320831485871012f83f62`

The final checkpoint has a different hash, confirming that model selection did not silently use
the end of training.

## Grounding diagnostic

The step-3,600 checkpoint was evaluated on 12 held-out validation sessions, balanced across six
accidental and six intentional events. Each window was evaluated unchanged, shifted by 64 ms, and
with its strongest channel swapped with the next channel. The trained
`rationale_then_answer` contract was used.

- Base onset coverage: 1.00; base onset MAE: 25.42 ms.
- A 64 ms input shift changed every onset prediction; shift-equivariance MAE: 31.42 ms.
- Base strongest-joint accuracy: 0.75.
- Channel-permutation accuracy: 0.583; prediction change rate: 0.833.

This is a small behavioral diagnostic, not a final benchmark. It shows meaningful temporal and
channel sensitivity, but channel equivariance is not yet reliable.

The nearest routine generation panel before the selected checkpoint was step 3,500. It reported
JSON parse validity 0.988, schema validity 0.917, answer exact match 0.500, semantics macro-F1
0.958, onset MAE 46 ms, and strongest-joint accuracy 0.688. Its paired zero-signal panel reduced
semantics macro-F1 to 0.133, contact F1 to 0, and strongest-joint accuracy to 0, with predictions
changing on 75% of examples. This supports signal use, although the zero-signal panel contains
only 12 examples.

## Matched ablation and unseen prompts

`configs/opentslm_sp_answer_only.yaml` is mechanically matched to
`configs/opentslm_sp_focused_control.yaml` except for experiment metadata and the supervised output
target. A full rationale run took about 3.2 hours, so starting two incomplete runs was rejected as
an invalid one-hour comparison. Use the selected rationale checkpoint and a completed answer-only
run with the same seed for the final ablation.

The evaluation code now supports `--prompt-set heldout`. These paraphrases are kept in a separate
constant from the training prompt pool and are asserted disjoint in tests. Metrics now separate
strict answer/schema validity from rationale presence, onset support and 50 ms consistency, and
joint support and consistency.

For an accuracy-first product, keep the CNN or feature classifier as the typed predictor. A future
OpenTSLM explanation layer should receive those predictions as explicit grounding context and be
evaluated for faithfulness; the current generative model should not be presented as a grounded
explainer merely by combining two independent frontend outputs.

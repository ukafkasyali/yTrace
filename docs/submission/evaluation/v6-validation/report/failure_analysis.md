# OpenTSLM failure-mode audit

Run state: **complete** at step 3823 in 3.21 hours.

This is a validation-methodology audit, not a new test-set or robot-safety claim. Event semantics and manual onset are source annotations; joint attribution and evidence intervals are deterministic pseudo-labels; outputs are model predictions.

## Headline

- Best token-loss step: 3600 (0.0783).
- Best observed decoded step: 3000 (selection score 0.8708).
- Eligible decoded checkpoints: 0/16.
- Final contact F1: 0.867; semantics macro-F1: 0.915.
- Final strongest-joint accuracy: 0.562; affected-joint set F1: 0.722.
- Final onset MAE: 48.7 ms; evidence IoU: 0.436.
- Final schema validity: 0.905; rationale coverage: 0.714.

## Findings

### CRITICAL: checkpoint_gate_deadlock

Evidence: 0/16 decoded evaluations were eligible; never-passed gates: first_pass/schema_exact_match max=0.893 < 0.950.

Implication: No task-selected grounding checkpoint was saved; deployment falls back to token-loss selection.

Action: Tune gates from an explicit pre-run acceptance policy or always retain the best observed decoded checkpoint separately.

### HIGH: late_decoded_regression

Evidence: Best observed decoded step=3000; final-minus-best metrics={'contact_f1': -0.10303030303030303, 'strongest_joint_accuracy': -0.3125, 'affected_joints_set_f1': -0.15401635401635394, 'onset_within_50ms': -0.13333333333333341}.

Implication: Continuing training improved token likelihood while degrading operator-facing answers.

Action: Select checkpoints using predeclared decoded validation metrics and confirm once on unused recording groups.

### HIGH: rationale_contract_not_reliable

Evidence: Only 71.4% of final validation outputs contained a parseable Rationale section.

Implication: The conversational observability contract is not consistently satisfied even when JSON parses.

Action: Report rationale coverage separately and reject or visibly label missing rationales in the demo.

### HIGH: retry_masks_generation_instability

Evidence: First-pass schema=0.798, post-retry schema=0.905, retry rate=0.143.

Implication: A second decode improves reporting metrics but increases latency and hides unreliable first responses.

Action: Treat first-pass validity as the deployment metric; add constrained decoding or stronger schema supervision.

### MEDIUM: joint_slices_not_observable

Evidence: Final panel has no strongest-joint targets for J3, J6, J7.

Implication: Aggregate joint accuracy cannot characterize these joints under natural prevalence.

Action: Keep natural-prevalence headline metrics, but add a separate diagnostic joint-coverage panel without training rebalancing.

### MEDIUM: manifest_hypothesis_mismatch

Evidence: Immutable hypothesis says 'focused_three_intent_curriculum_without_channel_permutation', while curriculum_intents='all' and receipts contain 7 intents.

Implication: A headline metadata field misdescribes the experiment even though detailed receipt fields are correct.

Action: Use the detailed intent counts as authoritative and validate hypothesis labels against materialized views before launch.

### MEDIUM: no_untouched_test_receipt

Evidence: The training run directory contains validation/probe receipts but no test prediction receipt.

Implication: This run supports model selection analysis, not a new held-out performance claim.

Action: Evaluate the frozen checkpoint once on unused recording groups; do not tune after inspecting them.

### MEDIUM: reused_small_validation_panel

Evidence: The same 84-row panel was decoded 16 times; smallest intent slice n=12.

Implication: Checkpoint rankings are noisy and repeated inspection can overfit decisions to this panel.

Action: Use it for monitoring only, then confirm the frozen choice on new recording groups with session-level intervals.

### MEDIUM: training_prompts_not_fully_fitted

Evidence: Final fixed training-probe exact match=0.583.

Implication: The model remains capacity/optimization limited on exact training prompts; validation errors are not purely overfit.

Action: Inspect prompt-family losses and output-token allocation before adding epochs.

## Per-intent final behavior

| Intent | n | Exact | Schema | Retry | Rationale |
|---|---:|---:|---:|---:|---:|
| affected_joints | 12 | 0.250 | 0.667 | 0.333 | 0.667 |
| contact | 12 | 0.500 | 0.833 | 0.167 | 0.500 |
| evidence_interval | 12 | 0.333 | 1.000 | 0.083 | 0.917 |
| onset | 12 | 0.333 | 1.000 | 0.083 | 0.833 |
| semantics | 12 | 0.833 | 1.000 | 0.167 | 0.500 |
| strongest_joint | 12 | 0.667 | 0.833 | 0.167 | 0.583 |
| summary | 12 | 0.333 | 1.000 | 0.000 | 1.000 |

## Methodology limits

- Validation was repeatedly inspected during training and is not an untouched confirmation set.
- The 84-row generation panel is useful for observability but too small for stable rare-slice claims.
- Strongest-joint, affected-joint, and evidence-interval targets measure agreement with deterministic signal rules, not physical impact location.
- Zeroing all channels is a coarse dependence check; it does not prove temporal or channel-grounded reasoning.
- Rationale lexical checks do not prove faithful reasoning.

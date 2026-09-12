# Challenge checkpoint or adapter handoff

Status: not delivered. This checklist prepares an approved challenge-submission
handoff; it does not authorize copying weights, exposing the private service or
promoting a new checkpoint.

## What the challenge needs

The brief asks for a checkpoint **or** adapter together with code, training
configuration, dataset documentation and a short held-out baseline evaluation.
The public repository already contains the code/configuration and evaluation
links in [README.md](README.md). Complete this checklist with the submission
channel's actual requirements before sending any private artifact.

## Identity record to complete

| Required item | Current verified value | Handoff status |
|---|---|---|
| Artifact role | Promoted OpenTSLM SoftPrompt checkpoint (canary-v4) | Private on Nebius; destination not approved here |
| SHA-256 | `8ff63b84ae5b64758f66b3e0527f4f225a04bb2b6b39193c806f58d94cec9f23` | Record after receiver verifies the transferred bytes |
| Artifact format | Expected tensor-only PyTorch `best_model.pt`: encoder/projector state plus declared LoRA state when enabled | Confirm keys and `weights_only=True` release validation against the exact transfer file |
| Architecture | OpenTSLM-SP / soft prompt | Confirm from the released artifact/config |
| Base model | `meta-llama/Llama-3.2-1B` | Confirm resolved model revision |
| Backbone revision | `unknown` in the live service receipt | **Missing: obtain immutable revision/commit or state why unavailable** |
| Warm start | `OpenTSLM/llama-3.2-1b-har-sp` | Record immutable upstream revision from training config |
| Upstream commit | `2968f4b891baab4307f7e9d0043e87677b593a30` | Included in training config |
| Training configuration | [`opentslm_sp.yaml`](../../model_training/configs/opentslm_sp.yaml) | Attach exact run-resolved copy and its SHA-256 |
| Input contract | seven canonical joints, 1,024 contiguous 1 kHz samples, `[start,end)` = 1.024 s | Include channel order, units and rejection behavior |
| Normalization | training-only robust normalization for canary-v4 | Attach the exact normalization artifact and SHA-256; do not use query statistics |
| Prompt/output contract | `rationale_then_answer`, deterministic signal pseudolabel source | Attach exact prompt/template revision and schema |
| Adapter/LoRA | Training configuration declares LoRA rank 16, alpha 32 and dropout 0.0; the live artifact's enabled state and target modules are not established by the public release record | **Missing: state enabled/disabled; if enabled provide target modules and adapter digest** |
| Rollback owner | Not recorded | **Missing: name the release owner responsible for retaining the prior config and performing a failed-release rollback** |
| License/access terms | Base-model access may be gated | Confirm redistribution and recipient access before transfer |

## Evidence bundle

Prepare a read-only bundle or approved private destination containing these items.
Never include an access token, SSH private key, service log with credentials, or
an unreviewed checkpoint.

- The immutable checkpoint or adapter, plus a locally computed SHA-256.
- Exact resolved training config, normalization artifact and prompt/output schema,
  each with SHA-256.
- Dataset card and split identity: [dataset documentation](../../model_training/docs/dataset.md), the split manifest, source inventory and their hashes.
- Reproducible code revision: repository commit SHA, upstream OpenTSLM commit and dependency lock/resolved environment.
- Evaluation package: [comparison report](evaluation/report/comparison.md), machine-readable results and source inventory. State that the 512 windows from 67 groups are already inspected and that feature semantics macro-F1 (0.9880) exceeds OpenTSLM (0.8845).
- A validation/receipt note showing the seven-channel, 1,024-sample, 1 kHz,
  half-open input contract and no silent resampling/padding of incomplete input.
- This limitation statement: the model output is a generated prediction; publisher
  markers and deterministic measurements are separate sources; schema validity
  (0.7793 usable summaries) and fine temporal precision limit use.

## Release and transfer gate

1. The artifact owner completes every missing identity item above and computes
   hashes from the exact bytes intended for handoff.
2. A reviewer checks the checkpoint/adapter format against the inference release
   contract in [`inference/release.py`](../../inference/release.py), the exact
   config and the recorded evaluation status. Do not substitute a newer file.
3. Use the challenge's approved private submission channel and record its
   recipient, date, artifact digest and acknowledgement outside Git if needed.
4. The recipient verifies the SHA-256, config/normalization/prompt identities,
   code revision and evaluation bundle before calling the handoff complete.
5. Preserve the current private canary-v4 service unchanged. A handoff is not a
   runtime promotion. Any future promotion remains evaluation → safe release →
   restart → smoke test → rollback on failure by the named rollback owner.

## Completion record

Do not fill this section until transfer is actually authorized and complete.

| Field | Value |
|---|---|
| Approved channel and recipient |  |
| Artifact filename and SHA-256 verified by recipient |  |
| Resolved backbone revision |  |
| Config / normalization / prompt hashes |  |
| Adapter or LoRA identity |  |
| Rollback owner |  |
| Repository and upstream revisions |  |
| Evaluation bundle digest |  |
| Date and acknowledgement reference |  |

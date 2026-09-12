# Training design and evaluation contract

## User and claim

The target user is a robot operator diagnosing a completed telemetry interval. The model receives
numeric synchronized joint-torque streams and a natural-language question, then generates a short
evidence statement and a machine-readable answer. The current evidence supports recording-held-out
performance on one KUKA LWR4+ platform. Public metadata does not expose reliable subject identity,
and no other robot is present, so neither subject-disjoint nor device-transfer performance is claimed.

## Output schema

The full summary response is:

```json
{
  "contact": true,
  "event_type": "accidental",
  "onset_ms": 438,
  "strongest_joint": "J4",
  "affected_joints": ["J4", "J2"],
  "evidence_start_ms": 438,
  "evidence_end_ms": 611
}
```

Atomic prompts return only their relevant keys. Free motion returns `false`, `free`, and `null` or
empty values. Parsing validity is an explicit metric; unparseable output is never silently repaired.

## Processing

1. Download all Zenodo archives with resume, checksum validation, and an append-only event log.
2. Discover complete sessions through `JK_MsrExtTrq.mat` and `JK_moments.mat` pairs.
3. Convert MATLAB one-based event markers exactly once to zero-based internal indices.
4. Split complete session folders 70/15/15 within accidental/contact source classes.
5. Fit per-joint robust center and scale on subsampled training sessions only.
6. Generate positive windows with randomized event positions. Generate hard free-motion negatives
   from high-derivative-energy areas between events, outside a 750 ms guard region.
7. Preserve signed torque; robust-scale and clip only the model view. Retain raw Nm statistics as
   metadata for visualization and deterministic baselines, but never expose them in model prompts.
8. Calibrate per-joint affected thresholds at the 99th percentile of train-only free-motion scores.
9. Store memory-mapped model arrays, JSONL provenance/targets, split map, normalization, and summary.
10. Build the equivalent TimeF dataset with signals, metadata, classification, answer, and temporal
    localization tasks.

## Pseudo-label definition

For joint `j`, subtract its local pre-event median and divide by its train-only robust scale. Over the
first 250 ms after the manual marker, calculate the mean of the largest 5% absolute deviations. Divide
that score by the joint's 99th-percentile train-free score. The largest calibrated score is the
"strongest joint"; scores at least one form the affected subset. This is queryable signal evidence,
not a physical estimate of impact link or location. Since it defines the target, the direct formula is
an oracle and is not advertised as a learned baseline victory.

## Experiment order and go/no-go checks

1. Transparent handcrafted/logistic signal baseline on the locked test split.
2. OpenTSLM checkpoint load and one-batch forward pass.
3. Overfit 32 training examples; require falling loss and valid structured generation.
4. Verify that shifting a signal shifts predicted onset and permuting channels changes joint attribution.
5. Launch the wall-clock-capped full Llama 1B SoftPrompt run.
6. Evaluate Qwen3-VL 4B zero-shot on the same test windows rendered as plots.
7. If time remains, train the fresh encoder/projector ablation. Flamingo is optional only after its
   upstream multichannel defect is patched and tested.

## Metrics

- Event semantics: macro-F1, accuracy, and per-class confusion.
- Contact: F1 and accuracy.
- Joint evidence: exact top-1 accuracy; future additions should include MRR/rank correlation.
- Onset: MAE, median/P90 absolute error, and accuracy within 10/25/50 ms.
- Generation: strict JSON parse-validity rate.
- Confidence intervals should resample recording sessions, not individual windows.

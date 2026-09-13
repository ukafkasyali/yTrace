# Trace incident investigation

> Retrospective robot telemetry report. Measurements, publisher annotations, and generated predictions are recorded as separate evidence sources.

## Incident

- **Recording:** 05-28-21-25
- **Source dataset:** <https://zenodo.org/records/21927431>
- **Selected window:** [5.787, 6.811) s
- **Question:** Analyze this robot telemetry window.

## Interpretation

**Origin:** generated\_prediction · **Source:** team-opentslm · completed

- **Contact:** yes
- **Event type:** accidental
- **Onset:** 276 ms after window start
- **Strongest joint:** J4
- **Affected joints:** J4, J1
- **Evidence interval:** 295–325 ms after window start

## Deterministic cross-check before handoff

- OpenTSLM predicts J4 as the strongest disturbance. J2 has the largest measured torque range. These are different quantities; inspect both before handoff.

## Measured torque

sampled min, max, range and absolute peak.

| Joint | Range | Signed absolute peak | Peak time |
| --- | ---: | ---: | ---: |
| Joint 1 | 3.088 Nm | 3.070 Nm | 6.129 s |
| Joint 2 | 4.456 Nm | -3.154 Nm | 6.132 s |
| Joint 3 | 0.680 Nm | 1.014 Nm | 6.153 s |
| Joint 4 | 2.451 Nm | 2.006 Nm | 6.131 s |
| Joint 5 | 0.093 Nm | 0.332 Nm | 6.133 s |
| Joint 6 | 0.939 Nm | -1.187 Nm | 6.133 s |
| Joint 7 | 0.054 Nm | 0.183 Nm | 6.057 s |

## Publisher annotations

- 6.187 s — Event 01 (JK\_moments)

## Evidence and provenance

- **Input:** 1024 samples per channel at 1000 Hz (raw)
- **Model:** team-opentslm
- **Revision:** checkpoint-sha256:8ff63b84ae5b64758f66b3e0527f4f225a04bb2b6b39193c806f58d94cec9f23; config:4dbdd82f5ff7; backbone:unknown
- **Input receipt:** 1024 samples/channel; SHA-256 7e78c56206491b03af817e11f1aaec165c5271675164d2e0a68ea579c4c92915

- Inspect 7 input channels — 5.787–6.811 s; joint\_1, joint\_2, joint\_3, joint\_4, joint\_5, joint\_6, joint\_7

## Limitations

- Recorded contact-event triage; no verified root cause, safety decision or repair recommendation.
- Publisher annotations, measured quantities and generated predictions are distinct evidence sources.
- Strong torque response does not identify the physical contact location.
- Raw model output is preserved for audit and may disagree with publisher annotations.

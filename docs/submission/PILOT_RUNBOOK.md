# Pilot runbook: correct investigation reports

This is a formative protocol for the hypothesis in [PILOT.md](PILOT.md). It is
not evidence until observations are collected. Do not recruit through this file,
put participant names in Git, or report the proposed threshold as a result.

## Before the first session

The facilitator completes and freezes this six-row manifest outside the task
interface. Choose only development or validation recordings. Do not use the 512
archived test windows, the three fixed UI examples, or source labels visible to
participants. Assign each case a neutral code (`C01`–`C06`) and preserve the
selection window, recording ID and source revision in a private scoring key.

| Case | Class balance | Condition A participant | Condition B participant | Hidden scoring key prepared? |
|---|---|---|---|---|
| C01 | accidental contact |  |  |  |
| C02 | accidental contact |  |  |  |
| C03 | intentional contact |  |  |  |
| C04 | intentional contact |  |  |  |
| C05 | free motion |  |  |  |
| C06 | free motion |  |  |  |

Use each case once per participant and counterbalance both case-condition pairing
and condition order. Recruit the four participants in this schedule. It gives
every case one A and one B exposure per two-participant block, while no participant
sees the same case twice.

| Participant | Condition order | A cases | B cases |
|---|---|---|---|
| P01 | A then B | C01, C03, C05 | C02, C04, C06 |
| P02 | B then A | C02, C04, C06 | C01, C03, C05 |
| P03 | B then A | C02, C04, C06 | C01, C03, C05 |
| P04 | A then B | C01, C03, C05 | C02, C04, C06 |

Match cases by window duration and visible signal strength as far as the available
validation material permits. Condition B must include one preselected
model-unavailable or unusable-output case; record it as a tool outcome, not as a
participant failure. Freeze the scoring key and expected deterministic measurements
before recruiting.

Prepare the same raw telemetry and short channel guide for both conditions:

| Condition A | Condition B |
|---|---|
| Synchronized charts and ordinary numerical tools; no generated interpretation, evidence links or Trace export | Trace with its actual deterministic measurements, generated prediction, evidence navigation and export |

Give both conditions the same two-minute familiarization. Demonstrate interface
controls only; do not reveal a case answer, say that Trace is expected to be
faster, or imply that a model prediction is ground truth.

## Participant task script

Read this verbatim after familiarization:

> You are preparing a handoff about one recorded robot interval. Inspect the
> available evidence and produce a short report. State whether you see accidental
> contact, intentional contact, free motion, or cannot determine; give the event
> time if you make a contact claim; name the strongest observed joint changes;
> cite the time interval and measured evidence you used. Do not infer physical
> impact location, root cause, safety status or a repair action. You have five
> minutes. Tell me when the report is complete.

The facilitator starts timing at the end of the final sentence and stops when the
participant says the report is complete, reaches five minutes, or abandons the
attempt. Record the outcome even if the report is incomplete. Do not answer task
questions beyond clarifying the controls equally for both conditions.

## Report template supplied to every participant

```text
Case: ______
Classification (accidental / intentional / free / cannot determine): ______
Event interval or “not applicable”: ______
Strongest observed joint change(s): ______
Measured evidence and interval: ______
Uncertainty or limitation: ______
```

For condition B, an exported Trace investigation may accompany this report but
does not replace the participant's written classification and evidence statement.

## Scoring after the session

A reviewer who does not know the condition scores the report using the frozen key.
Publisher class/onset annotations score only the class/onset fields. Deterministic
precomputed measurements score joint quantities. Neither source establishes a
physical cause, impact location or maintenance action.

| Field in `pilot-results.csv` | Rule |
|---|---|
| `completed` | `true` only when the report is submitted before the five-minute cap |
| `class_correct` | `true` when the class matches the hidden source label; `cannot determine` is incorrect unless that was the predeclared key |
| `onset_absolute_error_ms` | Absolute error against the hidden publisher onset for contact cases; leave blank for free motion, abstention or no onset claim |
| `measurement_correct` | `true` only when named joint/time/quantity agrees with frozen deterministic measurements; otherwise `false` |
| `unsupported_claim_count` | Count each distinct claim of cause, impact location, safety decision, repair outcome or numeric fact unsupported by the report's cited evidence |
| `tool_failure` | Brief factual description of unavailable data, model output, export or interface failure; leave blank when none occurred |

Enter one row per task in `pilot-results.csv`. Use anonymous IDs such as `P01`.
Leave `notes` factual; do not include contact details or sensitive information.

## Analysis after collection

Report the number of participants, tasks and cases; completion rate; class and
measurement correctness; onset-error distribution on scored contact reports;
unsupported-claim rate; tool failures; all attempts and the subset of correct
completed attempts. Pair comparisons within participant only where the assigned
case/condition schedule supports it. Show individual paired times and retain
incomplete five-minute attempts rather than dropping them.

The predeclared signal for another pilot is a **25% reduction in median
correct-completed report time with no observed increase in unsupported claims**.
It is a decision rule for this small formative sample, not a statistical proof,
ROI calculation or promised product benefit.

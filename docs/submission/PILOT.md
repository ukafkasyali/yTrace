# Pilot: time to a correct investigation report

Status: protocol prepared; no participants recruited, no results collected. This document must not be presented as user-study evidence. The facilitator materials are in [PILOT_RUNBOOK.md](PILOT_RUNBOOK.md); record observations only in `pilot-results.csv` using its documented columns.

## Decision

Does y/trace's evidence-linked analysis and export help robotics engineers complete a correct contact-event report faster than chart inspection? The buyer hypothesis is a robotics engineering or test lead responsible for incident review; the initial user is the engineer assembling the report.

## Protocol

Recruit four robotics engineers for a formative pilot so each condition and case pairing can be counterbalanced. Ask first how they investigate a recorded incident, which artifacts they hand off, and what information is usually missing. Do not solicit a positive testimonial or prime them with a claimed time saving.

Prepare six matched cases across collision, intentional contact and free motion. Use development/validation recordings; do not tune the model with final test answers. Keep source labels hidden during tasks. Give both conditions the same telemetry and documentation. Match case difficulty by duration and signal strength; each participant sees distinct cases in each condition. Counterbalance case assignment and condition order.

Condition A: synchronized charts and ordinary numerical tools, without generated interpretations. Condition B: y/trace with its actual measurements, model predictions, evidence links and export. Include at least one model failure or unavailable answer in B. Allow the same short tool familiarization in both conditions. Apply a five-minute task cap and retain incomplete attempts.

Each task asks the participant to locate the event, state its class or abstain, identify strongest signal changes, and produce a short evidence-backed report. A reviewer blinded to condition scores against publisher labels for class/onset and separately against deterministic measurements for joint quantities. Do not score a physical root-cause diagnosis: no such ground truth exists.

Record condition, order, case ID, elapsed seconds, completion, class correctness, onset error, measurement correctness, unsupported claims, tool failures and qualitative comments in `pilot-results.csv`. Use anonymous participant IDs and do not put personal information in the repository. The runbook defines the observation start/stop rule, scoring rubric and a six-case manifest before the first participant.

## Analysis and decision rule

Report participant and case counts, completion rate, correctness, unsupported-claim rate, and paired time differences. Report time among correct completions together with all failures; discarding failed or slow attempts can make a weaker system look faster. With such a small sample, treat results as directional and show individual paired outcomes.

Before collecting data, choose whether a 25% reduction in median correct-report time with no observed increase in unsupported claims would justify another pilot. This is a proposed product threshold, not a measured result or statistical guarantee.

An illustrative ROI calculation may later use measured minutes saved × incidents per month × engineer hourly cost. Label each input and keep hypothetical assumptions separate from observed results. Fewer repeated failures and reduced downtime require a longer deployment study.

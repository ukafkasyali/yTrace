# From signal interpretation to a useful investigation

Recommendation, 13 September 2026: keep the KUKA retrospective investigation
use case for this submission. Expand how much investigation it completes, and
measure its benefit before expanding into an unrelated prediction task.

The product promise to test is: an engineer can find a recorded incident, inspect
what changed, compare similar cases and hand off a traceable report with less
manual work. Reduced downtime and successful repairs are not measured outcomes.

## Highest-value sequence

1. Deploy full-recording raw access. The backend adapter and frontend request path
   are merged: a local integration run proved a seven-channel, 1,024-sample request
   outside the bundled excerpts, including a matching input receipt. The private
   GPU service still needs the trusted archive mounted with `--raw-root`, followed
   by the canary smoke test. Keep GPU training independent of this integration.
2. Build a retrospective incident inbox across available runs. Start with publisher
   markers and clearly labeled deterministic candidate windows, group similar
   signal patterns, and let the engineer inspect examples. A useful demo question
   is “Where else did this response occur?” Similarity is not a shared physical cause.
   Any unannotated-event scan needs false-alarm/missed-event evaluation before claims.
3. Run the prepared chart-only versus Trace pilot. Measure time to a correct,
   evidence-backed report and report errors, not just speed. Include model failures
   and reference-selection mistakes. A small exploratory pilot is useful feedback,
   not proof of fleet-wide ROI. See PILOT_RUNBOOK.md.
4. Establish what OpenTSLM adds. The archived signal-feature baseline outperforms
   OpenTSLM on interaction classification (0.9880 versus 0.8845 macro-F1), and
   OpenTSLM's usable-summary rate is 77.93%. Improve output reliability on validation,
   and compare investigation quality/time with and without generated interpretation.
   Do not tune on the inspected test archive or claim a language-model advantage
   without evidence. Finish checkpoint provenance and submission handoff.

## How to use the supplied graph inspiration

Use sparse labels linked to precise evidence. The implemented comparison graph
shows measured selected/reference peaks at original timestamps with leader lines,
and retains the full seven-joint table behind a disclosure. Preserve separate
meanings for measurements, publisher annotations and generated predictions.

Do not copy “95% chance of failure in two days” or “no action needed” into this
dataset's outputs. Canary-v4 interprets 1.024-second contact windows; this does not
establish degradation, remaining useful life, calibrated probabilities or safe action.

## If a second dataset becomes justified

Choose it only for a concrete task that existing data cannot support, after the
current pipeline and pilot are complete. A plausible later manufacturing extension
is tool-wear estimation: the [PHM Society 2010 challenge](https://phmsociety.org/phm_competition/2010-phm-society-conference-data-challenge/)
provides milling force, vibration and acoustic-emission signals with wear measurements
after cuts. It offers a measurable maintenance target, but describes CNC cutters,
not KUKA robot joints. The [NASA repository](https://www.nasa.gov/intelligent-systems-division/discovery-and-systems-health/pcoe/pcoe-data-set-repository/)
also lists IMS bearing experiments and simulated turbofan degradation.

Either is a separate model/data-contract/evaluation project, not another recording
for canary-v4. Split by independent tool, bearing or engine histories; evaluate
forecast error and useful warning lead time. Calibrate any probabilities separately.
Forecasting can be a roadmap item; a weak last-minute forecast would undermine the
otherwise reproducible submission. No dataset switch guarantees a judging outcome.

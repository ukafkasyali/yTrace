import { useState } from 'react';
import { BarChart3, CheckCircle2, Info } from 'lucide-react';
import comparison from '../../../docs/submission/evaluation/report/comparison.json';

type MethodKey = 'features' | 'cnn' | 'opentslm' | 'qwen';

type MethodDescription = {
  label: string;
  role: string;
  input: string;
  learned: string;
  implementation: string[];
  reading: string[];
  limitation: string;
};

const methods: Record<MethodKey, MethodDescription> = {
  features: {
    label: 'Signal features',
    role: 'Strongest overall baseline',
    input: 'Seven normalized torque channels, 1,024 samples each.',
    learned: 'Class-balanced logistic-regression weights and train-only calibrated thresholds over 35 handcrafted signal features.',
    implementation: [
      'Summarize amplitude, impulse, derivative energy, and variability for each joint.',
      'Fit event semantics on training sessions and estimate onset from the first sustained threshold crossing.',
      'Derive strongest and affected joints with train-only calibrated signal rules.',
    ],
    reading: [
      'Best event-semantics and strongest-joint scores in this comparison.',
      'Its 21 ms median onset error is strong, while a 188.6 ms P90 reveals occasional late or early threshold crossings.',
    ],
    limitation: 'The joint targets use related signal formulas, so joint accuracy is not independent physical localization evidence.',
  },
  cnn: {
    label: '1D CNN',
    role: 'Strongest learned baseline',
    input: 'Seven normalized torque channels, 1,024 samples each.',
    learned: '60,082 parameters across a shared temporal encoder and four prediction heads; trained on 29,393 windows.',
    implementation: [
      'Three Conv1d blocks reduce the time axis from 1,024 samples to 128 learned bins.',
      'Separate heads predict event semantics, onset, strongest joint, and affected joints.',
      'Epoch 23 was selected on validation macro-F1 with onset error as the tie-breaker.',
    ],
    reading: [
      'The best typical onset localization at 18 ms median error, with semantics macro-F1 of 0.9751.',
      'Its 274 ms P90 shows a longer tail of onset misses than OpenTSLM.',
    ],
    limitation: 'This is a recorded locked-split run from the comparison branch. Its row-level predictions are not in the current checksum-audited bundle.',
  },
  opentslm: {
    label: 'OpenTSLM',
    role: 'Deployed temporal language model',
    input: 'Seven numeric torque series plus the trained telemetry prompt contract.',
    learned: 'The time-series encoder, projector, and rank-16 LoRA state on the Llama 3.2 1B backbone.',
    implementation: [
      'Patch and encode the raw numeric series, then project them into the language-model token space.',
      'Generate an evidence sentence and structured incident fields from the same temporal input.',
      'Keep malformed or missing outputs as failures instead of silently repairing them.',
    ],
    reading: [
      'Semantics macro-F1 is 0.8845 and the best recorded onset P90 is 140 ms.',
      'Parse validity is 77.93%: 399 of 512 windows produced a structured JSON object.',
    ],
    limitation: 'Most missing answers are free-motion cases. Generated explanations remain predictions, even when the structured fields are correct.',
  },
  qwen: {
    label: 'Qwen3-VL zero-shot',
    role: 'Untrained explainable control',
    input: 'A 1,600 × 1,200 seven-panel telemetry plot and the fixed benchmark prompt.',
    learned: 'No parameters were trained on this dataset.',
    implementation: [
      'Render the same seven synchronized channels as a plot.',
      'Ask Qwen3-VL 4B for the same evidence sentence and JSON fields at temperature zero.',
      'Score its answer on the locked test rows without dataset-specific tuning.',
    ],
    reading: [
      'The model reaches 0.6951 semantics macro-F1 from plots without task-specific training.',
      'JSON syntax is valid, but the required contact value is usually null; contact F1 is 0.0291.',
    ],
    limitation: 'Raster plots are an indirect representation of exact 1 kHz timing and small cross-joint differences.',
  },
};

const audited = comparison.models;
const cnn = {
  semantics_macro_f1: 0.9751,
  contact_positive_f1: 0.9777,
  onset_median_ae_ms: 18,
  onset_p90_ae_ms: 274,
  strongest_joint_accuracy: 0.7565,
  json_object_rate: 1,
};

const methodOrder: MethodKey[] = ['features', 'cnn', 'opentslm', 'qwen'];
const percent = (value: number) => value * 100;
const answeredWindows = Math.round(audited.opentslm.usable_summary_rate * comparison.window_count);

const results = {
  features: {
    semantics: audited.features.semantics_macro_f1,
    contact: audited.features.contact_positive_f1,
    onsetMedian: audited.features.onset_median_ae_ms,
    onsetP90: audited.features.onset_p90_ae_ms,
    strongestJoint: audited.features.strongest_joint_accuracy,
    parseValidity: audited.features.json_object_rate,
  },
  cnn: {
    semantics: cnn.semantics_macro_f1,
    contact: cnn.contact_positive_f1,
    onsetMedian: cnn.onset_median_ae_ms,
    onsetP90: cnn.onset_p90_ae_ms,
    strongestJoint: cnn.strongest_joint_accuracy,
    parseValidity: cnn.json_object_rate,
  },
  opentslm: {
    semantics: audited.opentslm.semantics_macro_f1,
    contact: audited.opentslm.contact_positive_f1,
    onsetMedian: audited.opentslm.onset_median_ae_ms,
    onsetP90: audited.opentslm.onset_p90_ae_ms,
    strongestJoint: audited.opentslm.strongest_joint_accuracy,
    parseValidity: audited.opentslm.json_object_rate,
  },
  qwen: {
    semantics: audited.qwen.semantics_macro_f1,
    contact: audited.qwen.contact_positive_f1,
    onsetMedian: audited.qwen.onset_median_ae_ms,
    onsetP90: audited.qwen.onset_p90_ae_ms,
    strongestJoint: audited.qwen.strongest_joint_accuracy,
    parseValidity: audited.qwen.json_object_rate,
  },
};

type PlotMetric = {
  label: string;
  className: string;
  value: (key: MethodKey) => number;
  render: (value: number) => string;
};

function GroupedBarChart({ title, subtitle, maximum, metrics }: { title: string; subtitle: string; maximum: number; metrics: PlotMetric[] }) {
  return <section className="evaluation-chart" aria-label={title}>
    <div className="chart-heading"><div><h2>{title}</h2><p>{subtitle}</p></div><BarChart3 size={18} aria-hidden="true" /></div>
    <div className="plot-legend">{metrics.map(metric => <span key={metric.label}><i className={metric.className} />{metric.label}</span>)}</div>
    <div className="grouped-plot-scroll"><div className="grouped-plot">
      {methodOrder.map(key => <div className="plot-method" key={key}>
        <div className="plot-bars">{metrics.map(metric => {
          const value = metric.value(key);
          return <div className="plot-bar-slot" key={metric.label}><i className={metric.className} style={{ height: `${Math.max(2, value / maximum * 100)}%` }}><b>{metric.render(value)}</b></i></div>;
        })}</div>
        <span>{key === 'qwen' ? 'Zero-shot' : methods[key].label}</span>
      </div>)}
    </div></div>
  </section>;
}

export default function EvaluationWorkspace() {
  const [active, setActive] = useState<MethodKey>('features');
  const method = methods[active];

  return <section className="workspace-content evaluation-content" aria-labelledby="evaluation-title">
    <header className="workspace-heading evaluation-heading"><div><h1 id="evaluation-title">Held-out baseline comparison</h1><p>{comparison.window_count} test windows · seed {comparison.selection_seed} · seven joints · 1,024 samples at 1 kHz.</p></div><BarChart3 size={22} aria-hidden="true" /></header>

    <div className="evaluation-conclusion"><CheckCircle2 size={17} aria-hidden="true" /><div><strong>The transparent baseline wins classification; each learned model adds a different capability.</strong><p>The CNN gives the best typical onset timing. OpenTSLM has the best recorded onset P90 and produces operator-readable evidence, but its output reliability is the main weakness.</p></div></div>

    <dl className="evaluation-summary evaluation-summary-four">
      <div><dt>Best semantics macro-F1</dt><dd>0.9880</dd><small>Signal features</small></div>
      <div><dt>Best median onset</dt><dd>18 ms</dd><small>1D CNN</small></div>
      <div><dt>Best onset P90</dt><dd>140 ms</dd><small>OpenTSLM</small></div>
      <div><dt>OpenTSLM parse validity</dt><dd>77.93%</dd><small>{answeredWindows} of {comparison.window_count}</small></div>
    </dl>

    <section className="evaluation-table-section" aria-labelledby="results-title">
      <div className="section-heading"><div><h2 id="results-title">Comparable headline results</h2><p>Every row uses 512 held-out windows and seed 20260912. See provenance below for the CNN boundary.</p></div></div>
      <div className="table-scroll"><table className="data-table evaluation-table evaluation-headline-table"><thead><tr><th>Method</th><th>Semantics F1 ↑</th><th>Contact F1 ↑</th><th>Onset median ↓</th><th>Onset P90 ↓</th><th>Strongest joint ↑</th><th>Parse validity ↑</th></tr></thead><tbody>
        {methodOrder.map(key => <tr key={key}><td><i className={`method-dot method-${key}`} />{methods[key].label}{key === 'cnn' && <small>recorded run</small>}</td><td>{results[key].semantics.toFixed(4)}</td><td>{results[key].contact.toFixed(4)}</td><td>{results[key].onsetMedian.toFixed(0)} ms</td><td>{results[key].onsetP90.toFixed(1)} ms</td><td>{results[key].strongestJoint.toFixed(4)}</td><td>{results[key].parseValidity.toFixed(4)}</td></tr>)}
      </tbody></table></div>
      <p className="table-note">Contact F1 is the positive-contact score. Read it with parse validity: abstentions can leave positive F1 high while making the full answer unusable.</p>
    </section>

    <div className="evaluation-chart-grid">
      <GroupedBarChart
        title="Event semantics and joint attribution"
        subtitle="Score · higher is better"
        maximum={100}
        metrics={[
          { label: 'Semantics macro-F1', className: 'plot-primary', value: key => percent(results[key].semantics), render: value => `${value.toFixed(1)}%` },
          { label: 'Strongest-joint accuracy', className: 'plot-secondary', value: key => percent(results[key].strongestJoint), render: value => `${value.toFixed(1)}%` },
        ]}
      />
      <GroupedBarChart
        title="Onset absolute-error distribution"
        subtitle="Milliseconds · lower is better"
        maximum={340}
        metrics={[
          { label: 'Median error', className: 'plot-secondary', value: key => results[key].onsetMedian, render: value => `${value.toFixed(0)} ms` },
          { label: 'P90 error', className: 'plot-warning', value: key => results[key].onsetP90, render: value => `${value.toFixed(value % 1 ? 1 : 0)} ms` },
        ]}
      />
    </div>

    <section className="method-explorer" aria-labelledby="methods-title">
      <div className="section-heading"><div><h2 id="methods-title">Explore each implementation</h2><p>What goes in, what is learned, and why the metric profile differs.</p></div></div>
      <div className="method-tabs" role="tablist" aria-label="Evaluation methods">{methodOrder.map(key => <button key={key} role="tab" aria-selected={active === key} onClick={() => setActive(key)}>{methods[key].label}</button>)}</div>
      <article className="method-detail">
        <header><div><h3>{method.label}</h3><p>{method.role}</p></div><span>{active === 'cnn' ? 'recorded branch run' : active === 'opentslm' ? 'deployed model' : active === 'qwen' ? 'zero-shot control' : 'audited baseline'}</span></header>
        <div className="method-detail-grid"><div><h4>Input</h4><p>{method.input}</p><h4>What is learned</h4><p>{method.learned}</p></div><div><h4>Implementation pipeline</h4><ol>{method.implementation.map(item => <li key={item}>{item}</li>)}</ol></div></div>
        <div className="method-reading"><h4>How to read the result</h4><ul>{method.reading.map(item => <li key={item}>{item}</li>)}</ul></div>
        <p className="method-limit"><Info size={14} aria-hidden="true" /><span><strong>Interpretation limit</strong>{method.limitation}</span></p>
      </article>
    </section>

    <details className="evaluation-methodology"><summary>Evaluation contract and provenance</summary><p>Signal features, OpenTSLM, and Qwen are recomputed from checksum-verified archived predictions joined to the same 512 record IDs across 67 held-out recording groups. The CNN summary reports the same locked test size and seed, but its row-level predictions are not in that audited bundle, so it is visibly marked as a recorded run.</p><p>Recording sessions were split before window generation. Normalization and thresholds use training sessions only. Joint and evidence targets are deterministic signal-derived labels rather than physical contact-location truth.</p><p className="mono">Audited pipeline · records {comparison.record_ids_sha256.slice(0, 12)}… · checkpoint {comparison.checkpoint_sha256.slice(0, 12)}… · CNN checkpoint 67e883b997c4…</p></details>
  </section>;
}

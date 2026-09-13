import { useEffect, useState } from 'react';
import { ArrowLeft, ArrowRight, CheckCircle2, X } from 'lucide-react';
import comparison from '../../../docs/submission/evaluation/report/comparison.json';
import cnnSummary from '../../../docs/submission/evaluation/source/cnn-recorded-summary.json';
import rationaleFollowup from '../../../docs/submission/evaluation/source/opentslm-rationale-followup.json';

type Props = { onExit: () => void };

const slides = [
  { label: 'Problem', time: '0:00–0:35' },
  { label: 'Methods', time: '0:35–1:15' },
  { label: 'Results', time: '1:15–2:25' },
  { label: 'OpenTSLM', time: '2:25–3:50' },
  { label: 'Decision', time: '3:50–5:00' },
];

const audited = comparison.models;
const cnn = cnnSummary.metrics;
const rationale = rationaleFollowup.nearest_generation_panel;
const perturbation = rationaleFollowup.equivariance_panel;

function SpeakerCue({ children }: { children: string }) {
  return <p className="presentation-cue"><strong>Say:</strong> {children}</p>;
}

export default function PresentationWorkspace({ onExit }: Props) {
  const [active, setActive] = useState(0);
  const previous = () => setActive(index => Math.max(0, index - 1));
  const next = () => setActive(index => Math.min(slides.length - 1, index + 1));

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'ArrowLeft') previous();
      if (event.key === 'ArrowRight' || event.key === ' ') next();
      if (event.key === 'Escape') onExit();
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [onExit]);

  return <section className="presentation-workspace" aria-label="Five-minute baseline presentation">
    <header className="presentation-toolbar">
      <div><span>Robot observability</span><strong>{active + 1} / {slides.length} · {slides[active].time}</strong></div>
      <button className="text-button" onClick={onExit}><X size={15}/>Exit presentation</button>
    </header>

    <nav className="presentation-progress" aria-label="Presentation progress">
      {slides.map((slide, index) => <button key={slide.label} aria-current={active === index ? 'step' : undefined} onClick={() => setActive(index)}><i />{slide.label}</button>)}
    </nav>

    <main className="presentation-stage">
      {active === 0 && <article className="presentation-slide presentation-hero">
        <p className="presentation-kicker">KUKA LWR4+ · external-joint torque</p>
        <h1>From seven torque traces to reviewable incident evidence</h1>
        <p className="presentation-lead">We compare direct signal models and language models on the same 1.024-second robot windows.</p>
        <div className="presentation-stat-row"><div><strong>7</strong><span>synchronized joints</span></div><div><strong>1 kHz</strong><span>sampling rate</span></div><div><strong>512</strong><span>locked test windows</span></div><div><strong>4</strong><span>prediction targets</span></div></div>
        <SpeakerCue>Our goal is not merely to detect contact. We need its type, timing, responsible joint, and evidence in a form an engineer can review.</SpeakerCue>
      </article>}

      {active === 1 && <article className="presentation-slide">
        <p className="presentation-kicker">One locked split · four model families</p>
        <h1>We tested increasing levels of model complexity</h1>
        <div className="presentation-method-flow">
          <div><span>01</span><strong>Signal features</strong><p>35 engineered statistics + logistic regression</p></div>
          <div><span>02</span><strong>1D CNN</strong><p>Raw 7 × 1,024 waveform → four supervised heads</p></div>
          <div><span>03</span><strong>OpenTSLM</strong><p>Numeric tokens → Llama + LoRA → rationale and JSON</p></div>
          <div><span>04</span><strong>Direct Qwen</strong><p>Telemetry plots, zero-shot and one-shot</p></div>
        </div>
        <p className="presentation-proof">Recording sessions were split before window generation; normalization and thresholds use training sessions only.</p>
        <SpeakerCue>This order matters: each method adds flexibility, but also adds an optimization or generation burden.</SpeakerCue>
      </article>}

      {active === 2 && <article className="presentation-slide">
        <p className="presentation-kicker">Locked test results · n={comparison.window_count}</p>
        <h1>Simple models win the fixed prediction benchmark</h1>
        <div className="presentation-score-grid">
          <div className="is-primary"><span>Signal features</span><strong>{audited.features.semantics_macro_f1.toFixed(3)}</strong><small>semantics macro-F1</small></div>
          <div><span>1D CNN</span><strong>{cnn.semantics_macro_f1.toFixed(3)}</strong><small>semantics macro-F1 · 18 ms median onset</small></div>
          <div><span>OpenTSLM canary</span><strong>{audited.opentslm.semantics_macro_f1.toFixed(3)}</strong><small>semantics macro-F1 · 77.9% usable</small></div>
          <div><span>Qwen zero-shot</span><strong>{audited.qwen.semantics_macro_f1.toFixed(3)}</strong><small>semantics macro-F1</small></div>
        </div>
        <div className="presentation-warning"><strong>One-shot warning</strong><p>One intentional demonstration caused label anchoring: 467 of 512 predictions copied “intentional,” reducing semantics macro-F1 to 0.120.</p></div>
        <SpeakerCue>The CNN is the strongest learned fixed predictor, but engineered features remain best because they closely match how several targets were constructed.</SpeakerCue>
      </article>}

      {active === 3 && <article className="presentation-slide">
        <p className="presentation-kicker">New rationale run · validation diagnostics</p>
        <h1>OpenTSLM trades some fixed-task accuracy for a broader interface</h1>
        <div className="presentation-split">
          <div><h2>What improved</h2><dl><div><dt>Best checkpoint</dt><dd>step {rationaleFollowup.selected_step}</dd></div><div><dt>JSON validity</dt><dd>{(rationale.json_parse_validity * 100).toFixed(1)}%</dd></div><div><dt>Validation semantics F1</dt><dd>{rationale.semantics_macro_f1.toFixed(3)}</dd></div><div><dt>Base onset MAE</dt><dd>{perturbation.base_onset_mae_ms.toFixed(1)} ms</dd></div></dl></div>
          <div><h2>What remains unproven</h2><ul><li>Rationale appears in only 72.6% of the 84 validation generations.</li><li>Expected channel-swap response is only 58.3% on 12 sessions.</li><li>Unseen prompt paraphrases and the matched answer-only run are not complete.</li><li>These values are not a replacement for the locked test row.</li></ul></div>
        </div>
        <SpeakerCue>Reasoning is valuable only when it is grounded. The model reacts to time shifts and removed signals, but its channel-level equivariance still needs work.</SpeakerCue>
      </article>}

      {active === 4 && <article className="presentation-slide presentation-decision">
        <p className="presentation-kicker">Recommendation</p>
        <h1>Use the best model for each responsibility</h1>
        <div className="presentation-decision-flow"><div><span>Typed prediction</span><strong>Features or 1D CNN</strong><p>Class, contact, onset and affected joints</p></div><b>+</b><div><span>Human-facing explanation</span><strong>Grounded OpenTSLM</strong><p>Explain the typed result and answer follow-up questions</p></div></div>
        <div className="presentation-takeaway"><CheckCircle2 size={22}/><p><strong>Takeaway:</strong> compact signal models currently provide the most reliable decision layer; OpenTSLM is the promising explanation layer, pending a matched ablation and stronger grounding tests.</p></div>
        <SpeakerCue>We are not choosing CNN versus language. We are separating reliable prediction from flexible explanation, then testing whether the explanation remains faithful.</SpeakerCue>
      </article>}
    </main>

    <footer className="presentation-controls">
      <button className="btn" onClick={previous} disabled={active === 0}><ArrowLeft size={15}/>Previous</button>
      <span>Use ← → or Space</span>
      <button className="btn btn-primary" onClick={next} disabled={active === slides.length - 1}>Next<ArrowRight size={15}/></button>
    </footer>
  </section>;
}

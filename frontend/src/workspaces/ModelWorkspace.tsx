import { useEffect, useRef, useState } from 'react';
import { FlaskConical, Play, RefreshCw, Square } from 'lucide-react';
import { type Evidence, type ModelProfile, type QueryRequest, type Services } from '../services';
import type { ModelRegistry } from '../services/useModelRegistry';

type Props = { services: Services; registry: ModelRegistry; request: QueryRequest; onEvidence: (evidence: Evidence) => void };
type Result = {
  model: ModelProfile; request: QueryRequest; status: 'running' | 'complete' | 'failed' | 'cancelled';
  text: string; evidence: Evidence[]; labels?: { label: string; score?: number }[]; revision?: string; elapsedMs?: number; error?: string;
};
const errorText = (error: unknown) => error instanceof Error ? error.message : 'The request failed.';
function PresentedAnswer({ text }: { text: string }) {
  const blocks = text.split(/\n\s*\n/).filter(Boolean);
  if (blocks.length < 2) return <p className="answer-text">{text}</p>;
  return <div className="answer-brief comparison-answer">{blocks.map((block, index) => {
    const [heading, ...body] = block.split('\n');
    return <section key={`${heading}-${index}`}><h3>{heading}</h3>{body.length > 0 && <p>{body.join(' ')}</p>}</section>;
  })}</div>;
}

export default function ModelWorkspace({ services, registry, request, onEvidence }: Props) {
  const { models } = registry;
  const [error, setError] = useState('');
  const [question, setQuestion] = useState(() => request.question);
  const [results, setResults] = useState<Result[]>([]);
  const [running, setRunning] = useState(false);
  const active = useRef(new Map<string, { controller: AbortController; queryId?: string }>());
  const generation = useRef(0);

  useEffect(() => () => {
    generation.current += 1;
    for (const entry of active.current.values()) {
      entry.controller.abort();
      if (entry.queryId) void services.cancelQuery(entry.queryId).catch(() => undefined);
    }
    active.current.clear();
  }, [services]);

  function update(modelId: string, patch: Partial<Result>, run: number) {
    if (generation.current === run) setResults(current => current.map(result => result.model.id === modelId ? { ...result, ...patch } : result));
  }
  async function stop() {
    generation.current += 1;
    const entries = [...active.current.values()];
    entries.forEach(entry => entry.controller.abort());
    active.current.clear(); setRunning(false);
    setResults(current => current.map(result => result.status === 'running' ? { ...result, status: 'cancelled' } : result));
    const outcomes = await Promise.allSettled(entries.filter(entry => entry.queryId).map(entry => services.cancelQuery(entry.queryId!)));
    if (outcomes.some(outcome => outcome.status === 'rejected')) setError('The local streams stopped, but a server cancellation could not be confirmed.');
  }
  async function compare() {
    const available = models.filter(model => model.available && model.id !== 'assistant' && model.capabilities.some(capability => ['language', 'classification'].includes(capability)));
    if (running || !services.connected || !available.length || !question.trim()) return;
    const snapshot = structuredClone({ ...request, question: question.trim() }); const run = ++generation.current;
    setError(''); setRunning(true);
    setResults(available.map(model => ({ model, request: snapshot, status: 'running', text: '', evidence: [] })));
    await Promise.allSettled(available.map(async model => {
      const controller = new AbortController(); const entry: { controller: AbortController; queryId?: string } = { controller };
      active.current.set(model.id, entry); const began = performance.now(); let text = '';
      try {
        const created = await services.startQuery({ ...snapshot, mode: 'direct', modelId: model.id }, controller.signal);
        entry.queryId = created.queryId;
        if (run !== generation.current || controller.signal.aborted) { await services.cancelQuery(created.queryId); return; }
        await services.streamQuery(created.streamUrl, event => {
          if (run !== generation.current) return;
          if (event.queryId !== created.queryId) throw new Error('The response belongs to another query.');
          if (event.type === 'answer.delta' && model.capabilities.includes('language')) { text += event.payload.text ?? ''; update(model.id, { text }, run); }
          if (event.type === 'answer.completed' && !model.capabilities.includes('language') && !event.payload.labels?.length) {
            update(model.id, { status: 'failed', error: 'The classification model returned no class labels.', elapsedMs: performance.now() - began }, run);
          } else if (event.type === 'answer.completed') update(model.id, {
            text: model.capabilities.includes('language') ? event.payload.answer ?? text : '', evidence: event.payload.evidence ?? [], labels: event.payload.labels,
            revision: event.payload.modelRevision, status: 'complete', elapsedMs: performance.now() - began,
          }, run);
          if (event.type === 'query.error') update(model.id, { status: 'failed', error: event.payload.message ?? 'Inference failed.', elapsedMs: performance.now() - began }, run);
          if (event.type === 'query.cancelled') update(model.id, { status: 'cancelled', elapsedMs: performance.now() - began }, run);
        }, controller.signal);
      } catch (reason) {
        update(model.id, { status: controller.signal.aborted ? 'cancelled' : 'failed', error: controller.signal.aborted ? undefined : errorText(reason), elapsedMs: performance.now() - began }, run);
      } finally { if (active.current.get(model.id) === entry) active.current.delete(model.id); }
    }));
    if (generation.current === run) setRunning(false);
  }

  const available = models.filter(model => model.available && model.id !== 'assistant' && model.capabilities.some(capability => ['language', 'classification'].includes(capability)));
  return <section className="workspace-content" aria-labelledby="model-title">
    <header className="workspace-heading"><div><h1 id="model-title">Compare on the same signal</h1><p>Run each available model against a fixed recording window and question.</p></div><FlaskConical size={24} aria-hidden="true" /></header>
    {error && <p className="error-message" role="alert">{error}</p>}
    {registry.error && <p className="error-message" role="alert">{registry.error} Use Refresh status to retry.</p>}
    <section className="workspace-section" aria-busy={registry.loading}><div className="field-row"><h2>Model availability</h2><button className="btn" onClick={registry.refresh} disabled={registry.loading || !services.connected}><RefreshCw size={14} aria-hidden="true"/>{registry.loading ? 'Checking status…' : 'Refresh status'}</button></div><div className="table-scroll"><table className="data-table"><thead><tr><th>Model</th><th>Capability</th><th>Status</th></tr></thead><tbody>{models.map(model => <tr key={model.id}><td>{model.label}</td><td>{model.capabilities.join(', ')}</td><td>{model.available ? 'Available' : model.reason ?? 'Unavailable'}</td></tr>)}</tbody></table></div><p className="status-note">CNN predictions are classification results. Language models return generated answers; claims still need evidence.</p></section>
    <section className="workspace-section"><h2>Comparison context</h2><p><strong>{request.window.recordingId}</strong> · {request.window.startSec.toFixed(3)}–{request.window.endSec.toFixed(3)} s · {request.window.channelIds.length} channels</p><div className="field-row"><label htmlFor="comparison-question">Question for this comparison<textarea id="comparison-question" rows={3} maxLength={4000} value={question} onChange={event => setQuestion(event.target.value)} placeholder="What should the models investigate in this interval?" /></label></div><p className="status-note">Playback cursor: {request.playheadSec.toFixed(3)} s. Each run keeps this context even if the chart selection changes.</p><div className="field-row"><button className="btn btn-primary" onClick={() => void compare()} disabled={running || !services.connected || !available.length || !question.trim()}><Play size={15} aria-hidden="true" />Run available models</button>{running && <button className="btn" onClick={() => void stop()}><Square size={14} aria-hidden="true" />Stop comparison</button>}</div>{!services.connected && <p className="status-note">Model service is not connected. No inference or comparison scores have been generated.</p>}</section>
    {results.length === 0 ? <p className="empty-state">Results will appear here after a connected model runs.</p> : <div className="comparison-results" aria-live="polite">{results.map(result => <article className="model-result workspace-section" key={result.model.id}><header><h2>{result.model.label}</h2><span className="status-note">{result.status}{result.elapsedMs !== undefined ? ` · ${(result.elapsedMs / 1000).toFixed(2)} s` : ''}</span></header><p className="status-note">{result.request.window.recordingId} · {result.request.window.startSec.toFixed(3)}–{result.request.window.endSec.toFixed(3)} s{result.revision ? ` · revision ${result.revision}` : ''}</p><p className="status-note">Question: {result.request.question}</p>{result.error && <p className="error-message">{result.error}</p>}{result.text && <PresentedAnswer text={result.text}/>} {result.labels && <ul>{result.labels.map((label, index) => <li key={index}>{label.label}{typeof label.score === 'number' && Number.isFinite(label.score) ? ` · model score ${label.score.toFixed(3)}` : ''}</li>)}</ul>}{result.evidence.length > 0 && <div className="field-row">{result.evidence.map(evidence => <button className="btn" key={evidence.id} onClick={() => onEvidence(evidence)}>{evidence.label}</button>)}</div>}{result.status === 'running' && !result.text && <p className="status-note">Waiting for model output…</p>}{['failed', 'cancelled'].includes(result.status) && result.text && <p className="status-note">Partial answer. Incomplete.</p>}</article>)}</div>}
  </section>;
}

import { useEffect, useMemo, useRef, useState } from 'react';
import { ArrowUp, ArrowUpRight, CircleStop, Cpu, Download, MessageSquare, RotateCcw, SlidersHorizontal, Waves } from 'lucide-react';
import { predictionBrief, predictionCue, structuredPrediction, type PredictionCue } from './predictionBrief';
import { downloadInvestigationJson, downloadInvestigationMarkdown, type InvestigationAnswer } from './investigationReport';
import { checkpointLabel, fitModelWindow, modelWindowIssue } from '../lib/modelWindow';
import { analyzeWindow } from '../lib/data';
import { intervalLabel } from '../lib/format';
import type { DemoData, EvidenceLink, Interval } from '../types';
import type { Evidence, Services } from '../services';
import type { ModelRegistry } from '../services/useModelRegistry';

type Message = InvestigationAnswer & { telemetry: DemoData; id: string; tools: string[]; status: 'running' | 'complete' | 'error' | 'cancelled' };
type Props = { rawLoading?: boolean; rawError?: string; onRetryRaw: () => void; data: DemoData; datasetId: string; playhead: number; interval: Interval; services: Services; registry: ModelRegistry; onEvidence: (e: EvidenceLink) => void; onModelWindow: (interval: Interval) => void; onCompare: (interval: Interval) => void; onRobotPrediction: (prediction: PredictionCue) => void };
function AnswerText({ text }: { text: string }) {
  const blocks = text.split(/\n\s*\n/).filter(Boolean);
  return <div className="answer-brief">{blocks.map((block, index) => {
    const [heading, ...body] = block.split('\n');
    const structured = body.length > 0 && /^(Measured in this selected window|OpenTSLM interpretation)$/i.test(heading.trim());
    return structured
      ? <section key={`${heading}-${index}`}><h3>{heading}</h3><p>{body.join(' ')}</p></section>
      : <p key={`${heading}-${index}`}>{block.replace(/\n+/g, ' ')}</p>;
  })}</div>;
}
export default function AssistantPanel({ rawLoading, rawError, onRetryRaw, data, datasetId, playhead, interval, services, registry, onEvidence, onModelWindow, onRobotPrediction, onCompare }: Props) {
  const [question, setQuestion] = useState('');
  const [mode, setMode] = useState<'local' | 'assistant'>('assistant');
  const availableThrough = data.recording.durationSeconds;
  const historical = interval.end <= availableThrough;
  const dataIssue = useMemo(() => modelWindowIssue(data, interval, historical ? interval.end : 0), [data, interval, historical]);
  const windowIssue = rawLoading ? 'Loading 1,024 raw samples for this interval…' : rawError ?? dataIssue;
  const fitHorizon = Math.min(Math.floor(availableThrough * 1000) / 1000, data.detail.endSeconds);
  const fittedWindow = useMemo(() => fitModelWindow(data, interval, fitHorizon), [data, interval, fitHorizon]);
  const [exportStatus, setExportStatus] = useState('');
  const [messages, setMessages] = useState<Message[]>([]);
  const [busy, setBusy] = useState(false);
  const assistantAvailable = services.connected && !registry.loading && !registry.error && registry.models.some(model => model.id === 'assistant' && model.available && model.capabilities.includes('language'));
  const modeUnavailable = mode === 'assistant' && (!assistantAvailable || Boolean(windowIssue));
  const active = useRef<{ id: string; controller: AbortController; queryId?: string } | null>(null);
  const body = useRef<HTMLDivElement>(null);
  const completed = useRef(false);
  useEffect(() => { const container = body.current; const latest = container?.lastElementChild as HTMLElement | null; if (!messages.length || !container || !latest) return; container.scrollTo({ top: latest.offsetTop - container.offsetTop, behavior: 'instant' }); }, [messages.length]);
  useEffect(() => () => { const job = active.current; job?.controller.abort(); if (job?.queryId) void services.cancelQuery(job.queryId).catch(() => undefined); active.current = null; }, [services]);
  function update(id: string, changes: Partial<Message> | ((m: Message) => Partial<Message>)) { setMessages(ms => ms.map(m => m.id === id ? { ...m, ...(typeof changes === 'function' ? changes(m) : changes) } : m)); }
  function evidenceFrom(e: Evidence): EvidenceLink[] {
    if (e.window.recordingId !== data.recording.id) return [];
    if (!e.window.channelIds.length) return [];
    return [{ channelId: e.window.channelIds[0], channelIds: e.window.channelIds, label: `Inspect ${e.window.channelIds.length} input channels`, interval: { start: e.window.startSec, end: e.window.endSec } }];
  }
  async function submit(prompt = question, runMode: 'local' | 'assistant' = mode) {
    if (!prompt.trim() || busy || (runMode === 'assistant' && (rawLoading || rawError)) || (runMode === 'assistant' && !assistantAvailable)) return;
    const snapshot = { ...interval }, horizon = Math.max(playhead, snapshot.end);
    if (runMode === 'assistant' && modelWindowIssue(data, snapshot, horizon)) return;
    const id = crypto.randomUUID(); const controller = new AbortController();
    active.current = { id, controller }; completed.current = false;
    setMessages(ms => [...ms, { id, telemetry: data, mode: runMode, question: prompt.trim(), interval: snapshot, playhead: horizon, replayCursor: playhead, text: '', source: runMode === 'local' ? 'Local numerical analysis' : 'Assistant', tools: [], evidence: [], status: 'running' }]);
    setQuestion('');
    setBusy(true);
    try {
      if (runMode === 'local') {
        const result = analyzeWindow(data, snapshot, prompt, horizon);
        update(id, { text: result.text, tools: result.tools, evidence: result.evidence, status: 'complete' });
      } else {
        const request = { mode: 'assistant' as const, question: prompt, playheadSec: horizon, window: { datasetId, recordingId: data.recording.id, startSec: snapshot.start, endSec: snapshot.end, channelIds: data.channels.map(c => c.id) } };
        const job = await services.startQuery(request, controller.signal);
        if (active.current?.id !== id) { await services.cancelQuery(job.queryId); return; }
        active.current.queryId = job.queryId;
        await services.streamQuery(job.streamUrl, event => {
          if (active.current?.id !== id || event.queryId !== job.queryId) return;
          const p = event.payload;
          if (event.type === 'tool.started') update(id, m => ({ tools: [...m.tools, p.label ?? p.tool ?? 'Tool started'] }));
          if (event.type === 'tool.completed') update(id, m => ({ tools: [...m.tools, p.summary ?? 'Tool completed'] }));
          if (event.type === 'answer.delta') update(id, m => ({ text: m.text + (p.text ?? '') }));
          if (event.type === 'answer.completed') { completed.current = true; update(id, m => ({ status: 'complete', text: p.answer ?? m.text, modelId: p.modelId, modelRevision: p.modelRevision, inputTrace: p.inputTrace, modelOutput: typeof p.modelOutput === 'string' ? p.modelOutput : undefined, source: `${p.modelId ?? 'Assistant'} · completed`, evidence: (p.evidence ?? []).flatMap(evidenceFrom) })); }
          if (event.type === 'query.error') { completed.current = true; update(id, m => ({ status: 'error', text: `${m.text}${m.text ? '\n\n' : ''}${p.message ?? 'Inference failed.'}` })); }
          if (event.type === 'query.cancelled') { completed.current = true; update(id, { status: 'cancelled' }); }
        }, controller.signal);
      }
    } catch (error) {
      update(id, m => ({ status: controller.signal.aborted ? 'cancelled' : 'error', text: `${m.text}${m.text ? '\n\n' : ''}${controller.signal.aborted ? 'Stopped. This answer is incomplete.' : error instanceof Error ? error.message : 'The request failed.'}` }));
    } finally { if (active.current?.id === id) { active.current = null; setBusy(false); } }
  }
  function exportReport(message: Message) {
    try { downloadInvestigationMarkdown(message.telemetry, datasetId, message); setExportStatus('Readable investigation report downloaded as Markdown.'); }
    catch (error) { setExportStatus(error instanceof Error ? error.message : 'The report could not be exported.'); }
  }
  function exportEvidence(message: Message) {
    try { downloadInvestigationJson(message.telemetry, datasetId, message); setExportStatus('Machine-readable evidence downloaded as JSON.'); }
    catch (error) { setExportStatus(error instanceof Error ? error.message : 'The evidence could not be exported.'); }
  }
  async function stop() {
    const job = active.current; if (!job) return;
    job.controller.abort();
    if (job.queryId) { try { await services.cancelQuery(job.queryId); } catch { update(job.id, { status: 'error', text: 'The browser stopped listening, but server cancellation could not be confirmed.' }); } }
  }
  return <section className="assistant-panel" aria-label="Telemetry assistant">
    <header className="panel-heading"><div className="assistant-title"><MessageSquare size={16}/><h2>Event investigation</h2></div><span className="assistant-capability">{checkpointLabel(registry.models.find(m => m.id === 'assistant')?.revision).split(' · ')[0]}</span></header>
    <div className="model-input-status"><div><strong className="mono">{intervalLabel(interval)}</strong><span>{windowIssue ? 'Selection needs attention' : '1.024 s · 7 joints · raw telemetry'}</span></div><button className="btn btn-primary" disabled={busy || !assistantAvailable || Boolean(windowIssue)} onClick={() => { setMode('assistant'); void submit('Analyze this robot telemetry window.', 'assistant'); }}>{busy ? 'Analyzing…' : 'Analyze interval'}</button></div>
    {windowIssue && <div className="input-guidance"><p>{windowIssue}</p>{rawError && <button className="text-button" onClick={onRetryRaw}>Retry raw window</button>}{!rawLoading && !rawError && fittedWindow && <button className="text-button" disabled={busy} onClick={() => onModelWindow(fittedWindow)}>Use 1.024 s window</button>}</div>}
    <div className="conversation" ref={body} aria-live="polite">
      {!messages.length && <div className="conversation-intro"><Waves size={26}/><h3>Prepare a reviewable incident handoff.</h3><p>Run one selected window, verify the prediction against its exact signals, then export the evidence for a controls engineer.</p><p className="intro-limit">Recorded torque can guide investigation. It cannot verify a physical cause.</p></div>}
      {messages.map(m => {
        const brief = predictionBrief(m.modelOutput);
        const cue = m.status === 'complete' ? predictionCue(m.modelOutput, m.interval) : undefined;
        const measuredText = m.text.split(/\n\s*\n/).find(block => block.startsWith('Measured in this selected window'))?.split('\n').slice(1).join(' ');
        return <article className="conversation-turn" key={m.id}>
          <div className="user-question"><div><p>{m.question}</p><small className="mono">{intervalLabel(m.interval)}</small></div></div>
          <div className="assistant-answer"><div>
            {m.status !== 'complete' && <div className="answer-source">{m.status === 'running' ? 'Reading the selected telemetry…' : m.status}</div>}
            <div className={m.status === 'error' ? 'error-message' : ''}>
              {brief ? <><section className="prediction-summary"><h3>{brief.title}</h3><div className="prediction-facts">{brief.strongest && <span>Predicted strongest joint <strong>{brief.strongest}</strong></span>}{brief.onset !== undefined && <span>Predicted onset <strong>{brief.onset} ms</strong> into the window</span>}</div><p className="prediction-caution">OpenTSLM prediction · not a verified physical diagnosis.</p></section>{measuredText && <section className="measured-summary"><h3>Measured torque</h3><p>{measuredText}</p></section>}</> : m.text ? <AnswerText text={m.text}/> : null}
            </div>
            {m.status === 'complete' && <section className="investigation-next" aria-label="Verify and hand off"><h3>Verify and hand off</h3><ol className="investigation-actions">
              {cue && <li><button onClick={() => onRobotPrediction(cue)}><span>1</span><strong>Show predicted moment in 3D</strong><ArrowUpRight size={12}/></button></li>}
              {m.evidence.map((e, i) => <li key={i}><button onClick={() => onEvidence(e)}><span>{cue ? 2 : 1}</span><strong>Inspect exact seven-channel input</strong><SlidersHorizontal size={12}/></button></li>)}
              <li><button onClick={() => onCompare(m.interval)}><span>{cue ? 3 : m.evidence.length ? 2 : 1}</span><strong>Compare with a reference window</strong><ArrowUpRight size={12}/></button></li>
              <li><button onClick={() => exportReport(m)}><span>{cue ? 4 : m.evidence.length ? 3 : 2}</span><strong>Export incident handoff (.md)</strong><Download size={13}/></button></li>
            </ol></section>}
            {m.status === 'complete' && m.mode === 'assistant' && <details className="input-receipt"><summary>Model &amp; input details</summary><p>{checkpointLabel(m.modelRevision)} · [{m.interval.start.toFixed(3)}, {m.interval.end.toFixed(3)}) s</p><details><summary>Structured model prediction</summary><p>Only supported prediction fields are shown here. Generated evidence prose is retained only in the audit JSON and is not treated as an annotation or measurement.</p><pre>{JSON.stringify(structuredPrediction(m.modelOutput) ?? { status: 'No valid structured prediction was returned.' }, null, 2)}</pre></details><details><summary>1,024-sample input receipt</summary><pre>{JSON.stringify(m.inputTrace ?? { status: 'The server did not return an input receipt.' }, null, 2)}</pre><p>{m.modelRevision}</p></details>{m.tools.length > 0 && <details className="tool-log"><summary>{m.tools.length} completed tool steps</summary>{m.tools.map((t, i) => <p key={i}>{t}</p>)}</details>}<button className="text-button" onClick={() => exportEvidence(m)}><Download size={12}/>Download audit JSON</button></details>}
            {(m.status === 'error' || m.status === 'cancelled') && <button className="text-button" disabled={busy} onClick={() => { setQuestion(m.question); }}><RotateCcw size={12}/>Use this question again</button>}
          </div></div>
        </article>;
      })}
    </div>
    {exportStatus && <p className="report-status" role="status">{exportStatus}</p>}
    {mode === 'assistant' && !assistantAvailable && <p className="status-note" role="status">Model unavailable. Open custom analysis and choose Measurements only.</p>}
    <details className="custom-analysis" open={busy || undefined}><summary>Ask a custom question</summary><form className="composer" onSubmit={e => { e.preventDefault(); void submit(); }}><label className="sr-only" htmlFor="question">Ask about the selected telemetry</label><textarea id="question" value={question} onChange={e => setQuestion(e.target.value)} placeholder="Ask about this interval…" rows={2} onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); void submit(); } }}/><div className="composer-bottom"><label className="mode-select"><Cpu size={13}/><select aria-label="Analysis mode" value={mode} onChange={e => setMode(e.target.value as 'local' | 'assistant')}><option value="assistant" disabled={!assistantAvailable}>Model + measurements{!assistantAvailable ? ' · unavailable' : ''}</option><option value="local">Measurements only</option></select></label>{busy ? <button className="send-button" type="button" aria-label="Stop analysis" onClick={() => void stop()}><CircleStop size={18}/></button> : <button className="send-button" type="submit" aria-label="Send question" disabled={!question.trim() || modeUnavailable || interval.end <= interval.start || interval.end > availableThrough}><ArrowUp size={18}/></button>}</div></form></details>
  </section>;
}

import { useEffect, useMemo, useRef, useState } from 'react';
import { ArrowUp, ArrowUpRight, CircleStop, Download, MessageSquare, RotateCcw, SlidersHorizontal, Waves } from 'lucide-react';
import { generatedRationale, predictionBrief, predictionCue, structuredPrediction, type PredictionCue } from './predictionBrief';
import { reviewPrediction } from './predictionReview';
import { downloadInvestigationJson, downloadInvestigationMarkdown, type InvestigationAnswer } from './investigationReport';
import { investigationContextFingerprint, investigationStorageKey, persistInvestigation, restoreInvestigation } from './investigationSession';
import { checkpointLabel, fitModelWindow, modelWindowIssue } from '../lib/modelWindow';
import { analyzeWindow } from '../lib/data';
import { intervalLabel } from '../lib/format';
import type { DemoData, EvidenceLink, Interval } from '../types';
import { ApiError, type Evidence, type Services } from '../services';
import type { ModelRegistry } from '../services/useModelRegistry';

type Message = InvestigationAnswer & {
  telemetry: DemoData; id: string; tools: string[];
  status: 'running' | 'complete' | 'error' | 'cancelled'; restored?: boolean;
  phase?: string; startedAtMs?: number; expectedLatencyMs?: number;
  measurementPreview?: string; cacheHit?: boolean;
};
type Props = { rawLoading?: boolean; rawError?: string; onRetryRaw: () => void; data: DemoData; datasetId: string; playhead: number; interval: Interval; services: Services; registry: ModelRegistry; experimental?: { services: Services; registry: ModelRegistry }; onEvidence: (e: EvidenceLink) => void; onModelWindow: (interval: Interval) => void; onCompare: (interval: Interval) => void; onRobotPrediction: (prediction?: PredictionCue) => void };
const automaticPrompt = 'Analyze this robot telemetry window.';

function compactMeasuredRanges(text: string) {
  const ranges = [...text.matchAll(/Joint\s+(\d)\s+\(([-\d.]+)\s+Nm range\)/gi)];
  if (!ranges.length) return text.replace(/^Largest observed torque ranges:\s*/i, '');
  return ranges.map(([, joint, range]) => `J${joint} ${range} Nm`).join(' · ');
}

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
export default function AssistantPanel({ rawLoading, rawError, onRetryRaw, data, datasetId, playhead, interval, services, registry, experimental, onEvidence, onModelWindow, onRobotPrediction, onCompare }: Props) {
  const [question, setQuestion] = useState('');
  const [analysisSource, setAnalysisSource] = useState<'canary' | 'rationale' | 'local'>('canary');
  const mode = analysisSource === 'local' ? 'local' : 'assistant';
  const modelChoice = analysisSource === 'rationale' ? 'rationale' : 'canary';
  const selectedConnection = modelChoice === 'rationale' && experimental ? experimental : { services, registry };
  const selectedServices = selectedConnection.services;
  const canaryLabel = checkpointLabel(registry.models.find(model => model.id === 'assistant')?.revision).split(' · ')[0];
  const rationaleLabel = checkpointLabel(experimental?.registry.models.find(model => model.id === 'assistant')?.revision).split(' · ')[0];
  const availableThrough = data.recording.durationSeconds;
  const historical = interval.end <= availableThrough;
  const dataIssue = useMemo(() => modelWindowIssue(data, interval, historical ? interval.end : 0), [data, interval, historical]);
  const windowIssue = rawLoading ? 'Loading 1,024 raw samples for this interval…' : rawError ?? dataIssue;
  const fitHorizon = Math.min(Math.floor(availableThrough * 1000) / 1000, data.detail.endSeconds);
  const fittedWindow = useMemo(() => fitModelWindow(data, interval, fitHorizon), [data, interval, fitHorizon]);
  const [exportStatus, setExportStatus] = useState('');
  const sessionKey = investigationStorageKey(datasetId, data.recording.id, data.demoCase?.id ?? 'original');
  const initialContextFingerprint = useMemo(() => investigationContextFingerprint(data, datasetId, interval), [data, datasetId, interval]);
  const deferredRestore = useRef(rawLoading === true);
  const [messages, setMessages] = useState<Message[]>(() => {
    if (rawLoading) return [];
    const saved = restoreInvestigation(typeof window === 'undefined' ? undefined : window.sessionStorage, sessionKey, data.recording.id, data.recording.durationSeconds, initialContextFingerprint);
    return saved ? [{ ...saved, telemetry: data, restored: true }] : [];
  });
  const [busy, setBusy] = useState(false);
  const [stopping, setStopping] = useState(false);
  const canaryAvailable = services.connected && !registry.loading && !registry.error && registry.models.some(model => model.id === 'assistant' && model.available && model.capabilities.includes('language'));
  const rationaleAvailable = Boolean(experimental?.services.connected && !experimental.registry.loading && !experimental.registry.error && experimental.registry.models.some(model => model.id === 'assistant' && model.available && model.capabilities.includes('language')));
  const assistantAvailable = analysisSource === 'rationale' ? rationaleAvailable : canaryAvailable;
  const assistantModel = selectedConnection.registry.models.find(model => model.id === 'assistant');
  const expectedLatencyMs = assistantModel?.typicalLatencyMs || 25_000;
  const [clockMs, setClockMs] = useState(() => Date.now());
  const modeUnavailable = mode === 'assistant' && (!assistantAvailable || Boolean(windowIssue));
  const active = useRef<{ id: string; controller: AbortController; services: Services; queryId?: string; stopRequested?: boolean } | null>(null);
  const body = useRef<HTMLDivElement>(null);
  const completed = useRef(false);
  const restoredCueApplied = useRef(false);
  useEffect(() => {
    if (!deferredRestore.current || rawLoading || rawError) return;
    deferredRestore.current = false;
    const saved = restoreInvestigation(typeof window === 'undefined' ? undefined : window.sessionStorage, sessionKey, data.recording.id, data.recording.durationSeconds, initialContextFingerprint);
    if (saved) setMessages(current => current.length ? current : [{ ...saved, telemetry: data, restored: true }]);
  }, [rawLoading, rawError, sessionKey, data, initialContextFingerprint]);
  useEffect(() => { const container = body.current; const latest = container?.lastElementChild as HTMLElement | null; if (!messages.length || !container || !latest) return; container.scrollTo({ top: latest.offsetTop - container.offsetTop, behavior: 'instant' }); }, [messages.length]);
  useEffect(() => {
    if (!busy) return;
    setClockMs(Date.now());
    const timer = window.setInterval(() => setClockMs(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [busy]);
  useEffect(() => {
    if (restoredCueApplied.current) return;
    const restored = [...messages].reverse().find(message => message.restored && message.status === 'complete');
    if (!restored) return;
    restoredCueApplied.current = true;
    onRobotPrediction(predictionCue(restored.modelOutput, restored.interval));
  }, [messages, onRobotPrediction]);
  useEffect(() => {
    const latest = [...messages].reverse().find(message => message.status === 'complete');
    if (!latest) return;
    const { telemetry: _telemetry, restored: _restored, ...persistable } = latest;
    const contextFingerprint = investigationContextFingerprint(latest.telemetry, datasetId, latest.interval);
    persistInvestigation(typeof window === 'undefined' ? undefined : window.sessionStorage, sessionKey, data.recording.id, contextFingerprint, persistable);
  }, [messages, sessionKey, data.recording.id, datasetId]);
  useEffect(() => () => { const job = active.current; job?.controller.abort(); if (job?.queryId) void job.services.cancelQuery(job.queryId).catch(() => undefined); active.current = null; }, []);
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
    const queryServices = selectedServices;
    const startedAtMs = Date.now();
    let measurementPreview = '';
    if (runMode === 'assistant') {
      try { measurementPreview = analyzeWindow(data, snapshot, 'Compare torque ranges.', horizon).text.split(/\n\s*\n/)[0]; }
      catch { /* The model-window validation above remains the authoritative gate. */ }
    }
    active.current = { id, controller, services: queryServices }; completed.current = false;
    onRobotPrediction(undefined);
    setMessages(ms => [...ms.filter(message => !message.restored), { id, telemetry: data, mode: runMode, question: prompt.trim(), interval: snapshot, playhead: horizon, replayCursor: playhead, text: '', source: runMode === 'local' ? 'Local numerical analysis' : 'Assistant', tools: [], evidence: [], status: 'running', phase: 'Preparing exact model input', startedAtMs, expectedLatencyMs, measurementPreview }]);
    setQuestion('');
    setBusy(true);
    try {
      if (runMode === 'local') {
        const result = analyzeWindow(data, snapshot, prompt, horizon);
        update(id, { text: result.text, tools: result.tools, evidence: result.evidence, status: 'complete' });
      } else {
        const request = { mode: 'assistant' as const, question: prompt, playheadSec: horizon, window: { datasetId, recordingId: data.recording.id, startSec: snapshot.start, endSec: snapshot.end, channelIds: data.channels.map(c => c.id) } };
        const job = await queryServices.startQuery(request, controller.signal);
        if (active.current?.id !== id || controller.signal.aborted) { await queryServices.cancelQuery(job.queryId); return; }
        active.current.queryId = job.queryId;
        if (active.current.stopRequested) {
          try { await queryServices.cancelQuery(job.queryId); }
          catch { completed.current = true; throw new Error('Server cancellation could not be confirmed.'); }
        }
        update(id, { cacheHit: job.cacheHit === true, phase: job.cacheHit ? 'Reusing exact checkpoint/input result' : 'Opening model event stream' });
        await queryServices.streamQuery(job.streamUrl, event => {
          if (active.current?.id !== id || event.queryId !== job.queryId) return;
          const p = event.payload;
          if (event.type === 'tool.started') update(id, m => ({
            tools: [...m.tools, p.label ?? p.tool ?? 'Tool started'],
            phase: p.tool === 'measurement_summary' ? 'Calculating deterministic measurements'
              : typeof p.label === 'string' && p.label.startsWith('Reusing') ? 'Reusing exact checkpoint/input result'
              : 'OpenTSLM is generating a prediction',
          }));
          if (event.type === 'tool.completed') update(id, m => ({
            tools: [...m.tools, p.summary ?? 'Tool completed'],
            phase: typeof p.summary === 'string' && p.summary.startsWith('Calculated') ? 'Measurements ready · starting OpenTSLM' : 'Preparing the evidence-linked result',
          }));
          if (event.type === 'answer.delta') update(id, m => ({ text: m.text + (p.text ?? ''), phase: 'OpenTSLM is generating a prediction' }));
          if (event.type === 'answer.completed') {
            completed.current = true;
            const modelOutput = typeof p.modelOutput === 'string' ? p.modelOutput : undefined;
            update(id, m => ({ status: 'complete', phase: undefined, cacheHit: p.cacheHit === true || m.cacheHit, text: p.answer ?? m.text, modelId: p.modelId, modelRevision: p.modelRevision, inputTrace: p.inputTrace, modelOutput, source: `${p.modelId ?? 'Assistant'} · completed`, evidence: (p.evidence ?? []).flatMap(evidenceFrom) }));
            const cue = predictionCue(modelOutput, snapshot);
            onRobotPrediction(cue);
          }
          if (event.type === 'query.error') { completed.current = true; update(id, m => ({ status: 'error', text: `${m.text}${m.text ? '\n\n' : ''}${p.message ?? 'Inference failed.'}` })); }
          if (event.type === 'query.cancelled') { completed.current = true; update(id, { status: 'cancelled' }); }
        }, controller.signal);
      }
    } catch (error) {
      const waitSeconds = error instanceof ApiError
        ? error.estimatedWaitMs !== undefined
          ? Math.max(1, Math.ceil(error.estimatedWaitMs / 1000))
          : error.retryAfterSeconds
        : undefined;
      const wait = error instanceof ApiError && error.code === 'MODEL_BUSY'
        ? waitSeconds !== undefined ? ` Estimated wait: about ${waitSeconds} seconds.` : ' Wait for the current analysis to finish, then retry.'
        : '';
      update(id, m => ({ status: controller.signal.aborted ? 'cancelled' : 'error', phase: undefined, text: `${m.text}${m.text ? '\n\n' : ''}${controller.signal.aborted ? 'Stopped. This answer is incomplete.' : error instanceof Error ? error.message + wait : 'The request failed.'}` }));
    } finally { if (active.current?.id === id) { if (active.current.stopRequested && !completed.current) update(id, { status: 'cancelled', text: 'Stopped. This answer is incomplete.' }); active.current = null; setBusy(false); setStopping(false); } }
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
    job.stopRequested = true; setStopping(true);
    if (job.queryId) { try { await job.services.cancelQuery(job.queryId); } catch { completed.current = true; job.controller.abort(); update(job.id, { status: 'error', text: 'Server cancellation could not be confirmed.' }); } }
  }
  return <section className="assistant-panel" aria-label="Telemetry assistant">
    <header className="panel-heading"><div className="assistant-title"><MessageSquare size={16}/><h2>Event investigation</h2></div><select className="assistant-model-select" aria-label="Analysis source" value={analysisSource} disabled={busy} onChange={event => setAnalysisSource(event.target.value as 'canary' | 'rationale' | 'local')}><option value="canary" disabled={!canaryAvailable}>{canaryLabel} + measurements</option>{experimental && <option value="rationale" disabled={!rationaleAvailable}>{rationaleLabel} + measurements</option>}<option value="local">Measurements only</option></select></header>
    <div className="model-input-status"><div><strong className="mono">{intervalLabel(interval)}</strong><span>{windowIssue ? 'Selection needs attention' : '1.024 s · 7 joints · raw telemetry'}</span></div><button className="btn btn-primary" disabled={stopping || (!busy && modeUnavailable)} onClick={() => { if (busy) { void stop(); return; } void submit(automaticPrompt, mode); }}>{stopping ? 'Stopping…' : busy ? 'Stop analysis' : analysisSource === 'local' ? 'Measure interval' : 'Analyze interval'}</button></div>
    {windowIssue && <div className="input-guidance"><p>{windowIssue}</p>{rawError && <button className="text-button" onClick={onRetryRaw}>Retry raw window</button>}{!rawLoading && !rawError && fittedWindow && <button className="text-button" disabled={busy} onClick={() => onModelWindow(fittedWindow)}>Use 1.024 s window</button>}</div>}
    <div className="conversation" ref={body} aria-live="polite">
      {!messages.length && <div className="conversation-intro"><Waves size={26}/><h3>Prepare a reviewable incident handoff.</h3><p>Analyze the incident, verify the prediction against exact signals, then export the handoff for a controls engineer.</p><p className="intro-limit">Recorded torque can guide investigation. It cannot verify a physical cause.</p></div>}
      {messages.map(m => {
        const brief = predictionBrief(m.modelOutput);
        const rationale = generatedRationale(m.modelOutput);
        const review = m.status === 'complete' ? reviewPrediction(m.telemetry, m.interval, m.modelOutput) : undefined;
        const measuredText = m.text.split(/\n\s*\n/).find(block => block.startsWith('Measured in this selected window'))?.split('\n').slice(1).join(' ');
        const elapsedSeconds = m.startedAtMs ? Math.max(0, Math.floor((clockMs - m.startedAtMs) / 1000)) : 0;
        const typicalSeconds = Math.max(1, Math.round((m.expectedLatencyMs ?? 25_000) / 1000));
        return <article className="conversation-turn" key={m.id}>
          {m.question !== automaticPrompt && <div className="user-question"><div><p>{m.question}</p><small className="mono">{intervalLabel(m.interval)}</small></div></div>}
          <div className="assistant-answer"><div>
            {m.restored && <div className="answer-source">Saved result</div>}
            {m.cacheHit && m.status === 'complete' && <div className="answer-source">Reused exact input and checkpoint result · no new generation.</div>}
            {m.status === 'running' && <div className="analysis-progress" role="status"><strong>{m.phase ?? 'Reading the selected telemetry'}</strong><span>{m.cacheHit ? 'Checkpoint-matched cache hit' : `${elapsedSeconds} s elapsed · typically about ${typicalSeconds} s`}</span></div>}
            {m.status !== 'running' && m.status !== 'complete' && <div className="answer-source">{m.status}</div>}
            <div className={m.status === 'error' ? 'error-message' : ''}>
              {m.status === 'running' && m.measurementPreview && <section className="measured-summary measured-preview"><h3>Measured torque ready</h3><p>{m.measurementPreview}</p><small>Deterministic calculation · OpenTSLM prediction pending.</small></section>}
              {brief ? <><section className="prediction-summary"><h3>{brief.title}</h3><div className="prediction-facts">{brief.strongest && <span>Strongest joint <strong>{brief.strongest}</strong></span>}{brief.onset !== undefined && <span>Onset <strong>{brief.onset} ms</strong></span>}</div><p className="prediction-caution">Model prediction · not a verified diagnosis.</p></section>{rationale && <details className="generated-rationale"><summary>Generated interpretation</summary><p>{rationale}</p></details>}{measuredText && <section className="measured-summary"><h3>Measured range leaders</h3><p>{compactMeasuredRanges(measuredText)}</p></section>}{review && <section className="review-note"><h3>Cross-check</h3><p>Model {review.predictedJoint} · measured range leader {review.largestRangeJoint}. Inspect both.</p></section>}</> : m.status === 'complete' && m.mode === 'assistant' ? null : m.text ? <AnswerText text={m.text}/> : null}
            </div>
            {m.status === 'complete' && <section className="investigation-next" aria-label="Verify and hand off"><h3>Verify and hand off</h3><ol className="investigation-actions">
              {m.evidence.map((e, i) => <li key={i}><button onClick={() => onEvidence(e)}><span>1</span><strong>Inspect exact input</strong><SlidersHorizontal size={12}/></button></li>)}
              <li><button onClick={() => onCompare(m.interval)}><span>{m.evidence.length ? 2 : 1}</span><strong>Find similar incidents</strong><ArrowUpRight size={12}/></button></li>
              <li><button onClick={() => exportReport(m)}><span>{m.evidence.length ? 3 : 2}</span><strong>Export handoff (.md)</strong><Download size={13}/></button></li>
            </ol></section>}
            {m.status === 'complete' && m.mode === 'assistant' && <details className="input-receipt"><summary>Model &amp; input details</summary><p>{checkpointLabel(m.modelRevision)} · [{m.interval.start.toFixed(3)}, {m.interval.end.toFixed(3)}) s</p><details><summary>Structured model prediction</summary><p>Only supported prediction fields are shown here. Generated evidence prose is retained only in the audit JSON and is not treated as an annotation or measurement.</p><pre>{JSON.stringify(structuredPrediction(m.modelOutput) ?? { status: 'No valid structured prediction was returned.' }, null, 2)}</pre></details><details><summary>1,024-sample input receipt</summary><pre>{JSON.stringify(m.inputTrace ?? { status: 'The server did not return an input receipt.' }, null, 2)}</pre><p>{m.modelRevision}</p></details>{m.tools.length > 0 && <details className="tool-log"><summary>{m.tools.length} completed tool steps</summary>{m.tools.map((t, i) => <p key={i}>{t}</p>)}</details>}<button className="text-button" onClick={() => exportEvidence(m)}><Download size={12}/>Download audit JSON</button></details>}
            {(m.status === 'error' || m.status === 'cancelled') && <button className="text-button" disabled={busy} onClick={() => { setQuestion(m.question); }}><RotateCcw size={12}/>Use this question again</button>}
          </div></div>
        </article>;
      })}
    </div>
    {exportStatus && <p className="report-status" role="status">{exportStatus}</p>}
    <details className="custom-analysis" open><summary>Ask a custom question</summary><form className="composer" onSubmit={e => { e.preventDefault(); void submit(); }}><label className="sr-only" htmlFor="question">Ask about the selected telemetry</label><textarea id="question" value={question} onChange={e => setQuestion(e.target.value)} placeholder="Ask about this interval…" rows={2} onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); void submit(); } }}/><div className="composer-bottom">{busy ? <button className="send-button" type="button" aria-label={stopping ? 'Stopping analysis' : 'Stop analysis'} disabled={stopping} onClick={() => void stop()}><CircleStop size={18}/></button> : <button className="send-button" type="submit" aria-label="Send question" disabled={!question.trim() || modeUnavailable || interval.end <= interval.start || interval.end > availableThrough}><ArrowUp size={18}/></button>}</div></form></details>
  </section>;
}

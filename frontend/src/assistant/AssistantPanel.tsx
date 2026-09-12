import { useEffect, useRef, useState } from 'react';
import { ArrowUp, ArrowUpRight, Check, ChevronDown, CircleStop, Cpu, MessageSquare, RotateCcw, SlidersHorizontal, Terminal, Waves } from 'lucide-react';
import { analyzeWindow } from '../lib/data';
import { intervalLabel } from '../lib/format';
import type { DemoData, EvidenceLink, Interval } from '../types';
import type { Evidence, Services } from '../services';
import type { ModelRegistry } from '../services/useModelRegistry';

type Message = { id: string; question: string; interval: Interval; playhead: number; text: string; source: string; tools: string[]; evidence: EvidenceLink[]; status: 'running' | 'complete' | 'error' | 'cancelled' };
type Props = { data: DemoData; datasetId: string; playhead: number; interval: Interval; services: Services; registry: ModelRegistry; onEvidence: (e: EvidenceLink) => void };
function AnswerText({ text }: { text: string }) {
  const blocks = text.split(/\n\s*\n/).filter(Boolean);
  if (blocks.length < 2) return <p>{text}</p>;
  return <div className="answer-brief">{blocks.map((block, index) => {
    const [heading, ...body] = block.split('\n');
    return <section key={`${heading}-${index}`}><h3>{heading}</h3>{body.length > 0 && <p>{body.join(' ')}</p>}</section>;
  })}</div>;
}
export default function AssistantPanel({ data, datasetId, playhead, interval, services, registry, onEvidence }: Props) {
  const [question, setQuestion] = useState('');
  const [mode, setMode] = useState('assistant');
  const [messages, setMessages] = useState<Message[]>([]);
  const [busy, setBusy] = useState(false);
  const assistantAvailable = services.connected && !registry.loading && !registry.error && registry.models.some(model => model.id === 'assistant' && model.available && model.capabilities.includes('language'));
  const directAvailable = registry.models.filter(model => model.id !== 'assistant' && model.available);
  const modeUnavailable = mode === 'assistant' && !assistantAvailable;
  const connectionNote = registry.loading ? 'Checking model status…' : registry.error ? 'Model status unverified · local calculations available' : assistantAvailable ? 'Assistant uses OpenTSLM and measured torque ranges' : directAvailable.length ? `${directAvailable.map(model => model.label).join(', ')} available in Compare models · local calculations here` : 'Local calculations available · assistant unavailable';
  const active = useRef<{ id: string; controller: AbortController; queryId?: string } | null>(null);
  const body = useRef<HTMLDivElement>(null);
  const completed = useRef(false);
  useEffect(() => { const container = body.current; const latest = container?.lastElementChild as HTMLElement | null; if (!messages.length || !container || !latest) return; container.scrollTo({ top: latest.offsetTop - container.offsetTop, behavior: 'smooth' }); }, [messages]);
  useEffect(() => () => { const job = active.current; job?.controller.abort(); if (job?.queryId) void services.cancelQuery(job.queryId).catch(() => undefined); active.current = null; }, [services]);
  function update(id: string, changes: Partial<Message> | ((m: Message) => Partial<Message>)) { setMessages(ms => ms.map(m => m.id === id ? { ...m, ...(typeof changes === 'function' ? changes(m) : changes) } : m)); }
  function evidenceFrom(e: Evidence): EvidenceLink[] {
    if (e.window.recordingId !== data.recording.id) return [];
    if (!e.window.channelIds.length) return [];
    return [{ channelId: e.window.channelIds[0], channelIds: e.window.channelIds, label: `Inspect ${e.window.channelIds.length} input channels`, interval: { start: e.window.startSec, end: e.window.endSec } }];
  }
  async function submit(prompt = question) {
    if (!prompt.trim() || busy || modeUnavailable) return;
    const snapshot = { ...interval }, horizon = playhead;
    const id = crypto.randomUUID(); const controller = new AbortController();
    active.current = { id, controller }; completed.current = false;
    setMessages(ms => [...ms, { id, question: prompt.trim(), interval: snapshot, playhead: horizon, text: '', source: mode === 'local' ? 'Local numerical analysis' : 'Assistant', tools: [], evidence: [], status: 'running' }]);
    setQuestion(''); setBusy(true);
    try {
      if (mode === 'local') {
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
          if (event.type === 'answer.completed') { completed.current = true; update(id, m => ({ status: 'complete', text: p.answer ?? m.text, source: `${p.modelId ?? 'Assistant'} · completed`, evidence: (p.evidence ?? []).flatMap(evidenceFrom) })); }
          if (event.type === 'query.error') { completed.current = true; update(id, m => ({ status: 'error', text: `${m.text}${m.text ? '\n\n' : ''}${p.message ?? 'Inference failed.'}` })); }
          if (event.type === 'query.cancelled') { completed.current = true; update(id, { status: 'cancelled' }); }
        }, controller.signal);
      }
    } catch (error) {
      update(id, m => ({ status: controller.signal.aborted ? 'cancelled' : 'error', text: `${m.text}${m.text ? '\n\n' : ''}${controller.signal.aborted ? 'Stopped. This answer is incomplete.' : error instanceof Error ? error.message : 'The request failed.'}` }));
    } finally { if (active.current?.id === id) { active.current = null; setBusy(false); } }
  }
  async function stop() {
    const job = active.current; if (!job) return;
    job.controller.abort();
    if (job.queryId) { try { await services.cancelQuery(job.queryId); } catch { update(job.id, { status: 'error', text: 'The browser stopped listening, but server cancellation could not be confirmed.' }); } }
  }
  return <section className="assistant-panel" aria-label="Telemetry assistant">
    <header className="panel-heading"><div className="assistant-title"><MessageSquare size={16}/><h2>Ask the signal</h2></div><span className="assistant-capability">OpenTSLM + measurements</span></header>
    <div className="conversation" ref={body} aria-live="polite">
      {!messages.length && <div className="conversation-intro"><div className="assistant-emblem"><Waves size={22}/></div><h3>Investigate this interval</h3><p>Choose an analysis or ask a question about the selected signals.</p><div className="suggestion-list">{['Which joints have the largest torque range?', 'Where is the strongest absolute peak?', 'How does variability change in this interval?'].map(q => <button key={q} disabled={busy || modeUnavailable} onClick={() => void submit(q)}><span>{q === "Which joints have the largest torque range?" ? "Compare joint ranges" : q === "Where is the strongest absolute peak?" ? "Find the strongest peak" : "Compare variability"}</span><ArrowUpRight size={14}/></button>)}</div><div className="connection-note"><Terminal size={13}/><span>{connectionNote}</span></div></div>}
      {messages.map(m => <article className="conversation-turn" key={m.id}><div className="user-question"><span className="avatar">S</span><div><p>{m.question}</p><small className="mono">{intervalLabel(m.interval)} · replay at {m.playhead.toFixed(3)} s</small></div></div><div className="assistant-answer"><Waves size={17}/><div><div className="answer-source">{m.source}{m.status !== 'complete' && <span>{m.status === 'running' ? 'Working' : m.status}</span>}</div>{m.tools.length > 0 && <details className="tool-log"><summary><Check size={12}/>{m.tools.length} tool steps<ChevronDown size={12}/></summary>{m.tools.map((t, i) => <div key={i}><Check size={11}/>{t}</div>)}</details>}<div className={m.status === 'error' ? 'error-message' : ''}>{m.text ? <AnswerText text={m.text}/> : <p>Waiting for the analysis service…</p>}</div>{m.evidence.length > 0 && <div className="evidence-links">{m.evidence.map((e, i) => <button key={i} onClick={() => onEvidence(e)}><SlidersHorizontal size={12}/>{e.label}<ArrowUpRight size={11}/></button>)}</div>}{(m.status === 'error' || m.status === 'cancelled') && <button className="text-button" disabled={busy} onClick={() => { setQuestion(m.question); }}><RotateCcw size={12}/>Use this question again</button>}</div></div></article>)}
    </div>
    {modeUnavailable && <p className="status-note" role="status">The telemetry assistant is unavailable. Select Local analysis or check model status in Compare models.</p>}
    <form className="composer" onSubmit={e => { e.preventDefault(); void submit(); }}><div className="composer-context"><span className="selection-dot"/><span>Selected interval</span><strong className="mono">{intervalLabel(interval)}</strong><span>7 joints</span></div><label className="sr-only" htmlFor="question">Ask about the selected telemetry</label><textarea id="question" value={question} onChange={e => setQuestion(e.target.value)} placeholder="What do you notice in this interval?" rows={2} onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); void submit(); } }}/><div className="composer-bottom"><label className="mode-select"><Cpu size={13}/><select aria-label="Analysis mode" value={mode} onChange={e => setMode(e.target.value)}><option value="assistant" disabled={!assistantAvailable}>Telemetry assistant{!assistantAvailable ? ' · unavailable' : ''}</option><option value="local">Local analysis</option></select></label>{busy ? <button className="send-button" type="button" aria-label="Stop analysis" onClick={() => void stop()}><CircleStop size={18}/></button> : <button className="send-button" type="submit" aria-label="Send question" disabled={!question.trim() || modeUnavailable || interval.end <= interval.start || interval.end > playhead}><ArrowUp size={18}/></button>}</div></form>
  </section>;
}

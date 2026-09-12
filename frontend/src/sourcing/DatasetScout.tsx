import { useEffect, useRef, useState } from 'react';
import { SearchCheck } from 'lucide-react';
import { sourcingIsActive, type Services, type SourcingRun } from '../services';
import ScoutReview from './ScoutReview';

const demoBrief = 'Find 1 kHz robot collision and intentional contact time-series torque data from https://github.com/zhang-zengjie/robot-raw-collision-signals';
const errorText = (error: unknown) => error instanceof Error ? error.message : 'The request failed.';
type Intent = { brief: string; key: string };

export default function DatasetScout({ services, onUseSource }: { services: Services; onUseSource: (url: string) => void }) {
  const [brief, setBrief] = useState(demoBrief);
  const [resumeId, setResumeId] = useState('');
  const [runId, setRunId] = useState('');
  const [pollRevision, setPollRevision] = useState(0);
  const [run, setRun] = useState<SourcingRun | null>(null);
  const [report, setReport] = useState('');
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const [sourceReady, setSourceReady] = useState(false);
  const intent = useRef<Intent | null>(null);

  useEffect(() => {
    if (!runId || !services.connected) return;
    let alive = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const poll = async () => {
      try {
        const next = await services.getSourcingRun(runId);
        if (!alive) return;
        setRun(next); setError('');
        if (sourcingIsActive(next.status)) timer = setTimeout(poll, 1500);
      } catch (reason) { if (alive) setError(errorText(reason)); }
    };
    void poll();
    return () => { alive = false; clearTimeout(timer); };
  }, [services, runId, pollRevision]);

  async function start() {
    const normalized = brief.trim();
    if (normalized.length < 20) { setError('Describe the dataset need in at least 20 characters.'); return; }
    setBusy('start'); setError(''); setRun(null); setReport(''); setSourceReady(false);
    try {
      if (!intent.current || intent.current.brief !== normalized) intent.current = { brief: normalized, key: crypto.randomUUID() };
      const accepted = await services.startSourcingRun({ brief: normalized }, intent.current.key);
      intent.current = null;
      setRunId(accepted.runId); setResumeId(accepted.runId); setPollRevision(value => value + 1);
    } catch (reason) { setError(errorText(reason)); }
    finally { setBusy(''); }
  }

  async function review(decision: 'APPROVE' | 'REJECT') {
    if (!run) return;
    setBusy(decision); setError('');
    try {
      const next = await services.reviewSourcingRun(run.runId, decision, decision === 'APPROVE' ? run.recommendedCandidateId ?? undefined : undefined);
      if (decision === 'APPROVE') next.manifest = await services.getSourcingManifest(run.runId);
      setRun(next);
    } catch (reason) { setError(errorText(reason)); }
    finally { setBusy(''); }
  }

  async function loadReport() {
    if (!run || report || busy) return;
    setBusy('report');
    try { setReport(await services.getSourcingReport(run.runId)); }
    catch (reason) { setError(errorText(reason)); }
    finally { setBusy(''); }
  }

  return <section className="workspace-section dataset-scout" aria-labelledby="scout-title">
    <div className="section-heading"><div><p className="eyebrow">Agentic sourcing</p><h2 id="scout-title">Evidence-complete dataset scout</h2><p>Search bounded hypotheses, verify native sources, and review a deterministic recommendation before ingestion.</p></div><SearchCheck size={20} aria-hidden="true" /></div>
    {!services.connected && <p className="status-note">Configure the team API to run or resume dataset research.</p>}
    <label htmlFor="sourcing-brief">Research brief</label>
    <textarea id="sourcing-brief" rows={3} value={brief} onChange={event => { setBrief(event.target.value); intent.current = null; }} disabled={!services.connected || Boolean(busy)} />
    <div className="scout-actions"><button className="btn btn-primary" type="button" disabled={!services.connected || Boolean(busy)} onClick={() => void start()}><SearchCheck size={15} aria-hidden="true" />{busy === 'start' ? 'Starting…' : 'Start evidence review'}</button><form onSubmit={event => { event.preventDefault(); setError(''); setReport(''); setRun(null); setSourceReady(false); setRunId(resumeId.trim()); setPollRevision(value => value + 1); }}><label htmlFor="sourcing-run-id">Resume run</label><input id="sourcing-run-id" value={resumeId} onChange={event => setResumeId(event.target.value)} placeholder="Run ID" disabled={!services.connected || Boolean(busy)} /><button className="btn" disabled={!services.connected || !resumeId.trim() || Boolean(busy)}>Load</button></form></div>
    {error && <p className="error-message" role="alert">{error}</p>}
    {run ? <ScoutReview run={run} report={report} busy={busy} onReview={decision => void review(decision)} onLoadReport={() => void loadReport()} onUseSource={url => { onUseSource(url); setSourceReady(true); }} /> : runId && !error ? <p className="status-note" aria-live="polite">Loading sourcing run…</p> : null}
    {sourceReady && <p className="status-note" role="status">Source URL added to the ingestion form below. Review it before starting ingestion.</p>}
  </section>;
}

import { useEffect, useRef, useState } from 'react';
import { Plus, SearchCheck, X } from 'lucide-react';
import { sourcingIsActive, type RequirementsPreview, type Services, type SourcingReview, type SourcingRun } from '../services';
import RequirementEditor, { selectedRequirements, type RequirementPriorities } from './RequirementEditor';
import ScoutReview from './ScoutReview';

const demoBrief = 'Find 1 kHz robot collision and intentional contact time-series torque data from https://github.com/zhang-zengjie/robot-raw-collision-signals';
const errorText = (error: unknown) => error instanceof Error ? error.message : 'The request failed.';
type Intent = { signature: string; key: string };

export default function DatasetScout({ services, onUseSource }: { services: Services; onUseSource: () => void }) {
  const [brief, setBrief] = useState(demoBrief);
  const [resumeId, setResumeId] = useState('');
  const [runId, setRunId] = useState('');
  const [pollRevision, setPollRevision] = useState(0);
  const [run, setRun] = useState<SourcingRun | null>(null);
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const [sourceReady, setSourceReady] = useState(false);
  const [customRequirement, setCustomRequirement] = useState('');
  const [customRequirements, setCustomRequirements] = useState<string[]>([]);
  const [preview, setPreview] = useState<RequirementsPreview | null>(null);
  const [priorities, setPriorities] = useState<RequirementPriorities>({});
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
      } catch (reason) { if (alive) setError(`Could not load sourcing run: ${errorText(reason)}`); }
    };
    void poll();
    return () => { alive = false; clearTimeout(timer); };
  }, [services, runId, pollRevision]);

  function invalidatePreview() {
    setPreview(null); setPriorities({}); intent.current = null;
  }

  async function previewRequirements() {
    const normalized = brief.trim();
    if (normalized.length < 20) { setError('Describe the dataset need in at least 20 characters.'); return; }
    setBusy('preview'); setError('');
    try {
      const next = await services.previewSourcingRequirements({
        brief: normalized, customRequirements,
      });
      setPreview(next);
      setPriorities(Object.fromEntries(next.requirements.map(item => [item.id, item.priority])));
    } catch (reason) { setError(`Could not preview requirements: ${errorText(reason)}`); }
    finally { setBusy(''); }
  }

  function addCustomRequirement() {
    const normalized = customRequirement.trim();
    if (normalized.length < 3 || customRequirements.length >= 10
      || customRequirements.some(item => item.toLocaleLowerCase() === normalized.toLocaleLowerCase())) return;
    setCustomRequirements(items => [...items, normalized]);
    setCustomRequirement(''); invalidatePreview();
  }

  async function start() {
    const normalized = brief.trim();
    if (!preview) { setError('Review the research requirements before starting.'); return; }
    const request = {
      brief: normalized,
      requirements: selectedRequirements(preview.requirements, priorities),
    };
    const signature = JSON.stringify(request);
    setBusy('start'); setError(''); setRun(null); setSourceReady(false);
    try {
      if (!intent.current || intent.current.signature !== signature) intent.current = { signature, key: crypto.randomUUID() };
      const accepted = await services.startSourcingRun(request, intent.current.key);
      intent.current = null;
      setRunId(accepted.runId); setResumeId(accepted.runId); setPollRevision(value => value + 1);
    } catch (reason) { setError(`Could not start evidence review: ${errorText(reason)}`); }
    finally { setBusy(''); }
  }

  async function review(reviewRequest: SourcingReview) {
    if (!run) return;
    setBusy(reviewRequest.decision); setError('');
    try {
      const next = await services.reviewSourcingRun(run.runId, reviewRequest);
      if (reviewRequest.decision === 'APPROVE') next.manifest = await services.getSourcingManifest(run.runId);
      setRun(next);
    } catch (reason) { setError(`Could not apply reviewer decision: ${errorText(reason)}`); }
    finally { setBusy(''); }
  }

  return <section className="workspace-section dataset-scout" aria-labelledby="scout-title">
    <div className="section-heading"><div><p className="eyebrow">Agentic sourcing</p><h2 id="scout-title">Evidence-complete dataset scout</h2><p>Search bounded hypotheses, verify native sources, and review a deterministic recommendation before ingestion.</p></div><SearchCheck size={20} aria-hidden="true" /></div>
    {!services.connected && <p className="status-note">Configure the team API to run or resume dataset research.</p>}
    <label htmlFor="sourcing-brief">Research brief</label>
    <textarea id="sourcing-brief" rows={3} value={brief} onChange={event => { setBrief(event.target.value); invalidatePreview(); }} disabled={!services.connected || Boolean(busy)} />
    <form className="custom-requirement" onSubmit={event => { event.preventDefault(); addCustomRequirement(); }}>
      <label htmlFor="custom-sourcing-requirement">Custom mandatory requirement</label>
      <div><input id="custom-sourcing-requirement" value={customRequirement} onChange={event => setCustomRequirement(event.target.value)} maxLength={500} placeholder="Example: At least 200 labelled collision sequences" disabled={!services.connected || Boolean(busy) || customRequirements.length >= 10} /><button className="btn" disabled={customRequirement.trim().length < 3 || customRequirements.length >= 10 || Boolean(busy)}><Plus size={14} aria-hidden="true" />Add</button></div>
      <small>{customRequirements.length}/10 custom requirements</small>
    </form>
    {customRequirements.length > 0 && <ul className="custom-requirements" aria-label="Custom requirements">{customRequirements.map(item => <li key={item}><span>{item}</span><button type="button" aria-label={`Remove custom requirement: ${item}`} onClick={() => { setCustomRequirements(values => values.filter(value => value !== item)); invalidatePreview(); }}><X size={13} aria-hidden="true" /></button></li>)}</ul>}
    {preview && <RequirementEditor requirements={preview.requirements} priorities={priorities} onPriorityChange={(id, priority) => { setPriorities(current => ({ ...current, [id]: priority })); intent.current = null; }} />}
    <div className="scout-actions">
      {preview
        ? <button className="btn btn-primary" type="button" disabled={!services.connected || Boolean(busy)} onClick={() => void start()}><SearchCheck size={15} aria-hidden="true" />{busy === 'start' ? 'Starting…' : 'Start evidence review'}</button>
        : <button className="btn btn-primary" type="button" disabled={!services.connected || Boolean(busy)} onClick={() => void previewRequirements()}><SearchCheck size={15} aria-hidden="true" />{busy === 'preview' ? 'Preparing…' : 'Review requirements'}</button>}
      <form onSubmit={event => { event.preventDefault(); setError(''); setRun(null); setSourceReady(false); setRunId(resumeId.trim()); setPollRevision(value => value + 1); }}><label htmlFor="sourcing-run-id">Resume run</label><input id="sourcing-run-id" value={resumeId} onChange={event => setResumeId(event.target.value)} placeholder="Run ID" disabled={!services.connected || Boolean(busy)} /><button className="btn" disabled={!services.connected || !resumeId.trim() || Boolean(busy)}>Load</button></form>
    </div>
    {error && <p className="error-message" role="alert">{error}</p>}
    {run ? <ScoutReview run={run} busy={busy} onReview={reviewRequest => void review(reviewRequest)} onUseSource={() => { onUseSource(); setSourceReady(true); }} /> : runId && !error ? <p className="status-note" aria-live="polite">Loading sourcing run…</p> : null}
    {sourceReady && <p className="status-note" role="status">Approved source library refreshed. Select its assets there when you are ready to ingest.</p>}
  </section>;
}

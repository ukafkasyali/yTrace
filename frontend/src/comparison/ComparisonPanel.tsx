import { useEffect, useMemo, useRef, useState } from 'react';
import { ArrowUpRight, Download } from 'lucide-react';
import type { DemoCase, DemoData, EvidenceLink, Interval } from '../types';
import type { Services } from '../services';
import { downsample, loadDemoData } from '../lib/data';
import { intervalLabel } from '../lib/format';
import { compareWindows, comparisonReport, rankSimilarIncidents, suggestReference, sampledPeak, type SimilarIncident, type WindowComparison } from './compare';

type Props = { data: DemoData; interval: Interval; cases: DemoCase[]; services: Services; onEvidence: (e: EvidenceLink) => void };
const fixed = (n: number) => n.toFixed(3);
const signed = (n: number) => `${n >= 0 ? '+' : ''}${fixed(n)}`;
function Overlay({ result, jointId, onJoint }: { result: WindowComparison; jointId: string; onJoint: (id: string) => void }) {
  const a = result.plot.selected, b = result.plot.reference;
  const av = a.channels.find(c => c.id === jointId)!.values, bv = b.channels.find(c => c.id === jointId)!.values;
  let low = Infinity, high = -Infinity;
  for (const values of [av, bv]) for (const v of values) { low = Math.min(low, v); high = Math.max(high, v); }
  const pad = Math.max((high - low) * .12, .01);
  const length = result.selected.interval.end - result.selected.interval.start;
  const x = (time: number, start: number) => (time - start) / length * 600;
  const y = (value: number) => 190 - (value - low + pad) / (high - low + 2 * pad) * 150;
  const path = (times: number[], values: number[], start: number) => downsample(times.map((t, i) => ({ x: t - start, y: values[i] })), 400)
    .map((p, i) => `${i ? 'L' : 'M'}${(p.x / length * 600).toFixed(2)},${y(p.y).toFixed(2)}`).join(' ');
  const selectedPeak = sampledPeak(a.times, av), referencePeak = sampledPeak(b.times, bv);
  const sx = x(selectedPeak.time, result.selected.interval.start), sy = y(selectedPeak.value);
  const rx = x(referencePeak.time, result.reference.interval.start), ry = y(referencePeak.value);
  return <figure className="comparison-plot annotated-comparison"><figcaption>
    <label>Inspect joint<select aria-label="Comparison graph joint" value={jointId} onChange={e => onJoint(e.target.value)}>{a.channels.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}</select></label>
    <span>Signed torque · shared Nm scale</span></figcaption>
    <div className="graph-callout selected-callout"><strong>Selected · sampled peak</strong><span>{signed(selectedPeak.value)} Nm at {fixed(selectedPeak.time)} s</span></div>
    <svg viewBox="0 0 600 230" role="img" aria-label={`${jointId.replace('joint_', 'Joint ')}: selected signed peak ${fixed(selectedPeak.value)} Nm at ${fixed(selectedPeak.time)} seconds; reference signed peak ${fixed(referencePeak.value)} Nm at ${fixed(referencePeak.time)} seconds. Largest absolute sampled values, aligned by window start, shared Nm scale.`}>
      {[40, 115, 190].map((lineY, i) => <g key={lineY}><line x1="0" x2="600" y1={lineY} y2={lineY} className="chart-grid"/><text x="0" y={lineY - 5} className="graph-scale">{fixed(high + pad - i * (high - low + 2 * pad) / 2)} Nm</text></g>)}
      <path d={path(b.times, bv, result.reference.interval.start)} className="reference-wave"/>
      <path d={path(a.times, av, result.selected.interval.start)} className="selected-wave"/>
      <path d={`M24,0 L24,16 L${sx},${sy}`} className="graph-leader selected-leader"/>
      <path d={`M576,230 L576,214 L${rx},${ry}`} className="graph-leader reference-leader"/>
      <circle cx={rx} cy={ry} r="5" className="graph-point reference-point"/>
      <circle cx={sx} cy={sy} r="5" className="graph-point selected-point"/>
    </svg>
    <div className="graph-callout reference-callout"><strong>Reference · sampled peak</strong><span>{signed(referencePeak.value)} Nm at {fixed(referencePeak.time)} s</span></div>
    <div className="comparison-axis"><span>0 ms</span><span>Time from each window’s start</span><span>{Math.round(length * 1000)} ms</span></div>
    <p>Circles mark each curve’s largest absolute sampled torque, with its signed value. {result.resolution === 'raw' ? 'Raw samples.' : 'Overview samples; brief peaks may be missed.'} Peaks are measurements, not detected collisions. Motion phases may differ.</p></figure>;
}
export default function ComparisonPanel({ data, interval, cases, services, onEvidence }: Props) {
  const [referenceData, setReferenceData] = useState(data);
  const [referenceId, setReferenceId] = useState(data.recording.id);
  const initial = suggestReference(data, interval);
  const [start, setStart] = useState(initial ? fixed(initial.start) : '');
  const [reference, setReference] = useState<Interval | undefined>();
  const [suggested, setSuggested] = useState(Boolean(initial));
  const [loading, setLoading] = useState(false), [error, setError] = useState(''), [status, setStatus] = useState('');
  const [matching, setMatching] = useState(false), [matchIssue, setMatchIssue] = useState('');
  const [matches, setMatches] = useState<SimilarIncident[]>([]);
  const [joint, setJoint] = useState('');
  const request = useRef(0);
  const duration = interval.end - interval.start;
  useEffect(() => () => { request.current++; }, []);
  const options = [...new Map([{ recordingId: data.recording.id, title: 'This recording' }, { recordingId: '05-28-21-25', title: 'Original recording' }, ...cases].map(c => [c.recordingId, c])).values()];
  async function recordingData(id: string) {
    return id === data.recording.id ? data : id === '05-28-21-25' ? loadDemoData() : services.getReplay(id);
  }
  useEffect(() => {
    let active = true;
    const original: DemoCase = { id: 'original', title: 'Original recording', recordingId: '05-28-21-25', interval: { start: 5.787, end: 6.811 }, note: 'Fixed raw demonstration window.' };
    const pool = [original, ...cases];
    if (!pool.length || !services.connected) { setMatches([]); return () => { active = false; }; }
    setMatching(true); setMatchIssue('');
    const recordings = new Map<string, Promise<DemoData>>();
    const loadCandidate = (item: DemoCase) => {
      if (!recordings.has(item.recordingId)) recordings.set(item.recordingId, recordingData(item.recordingId));
      return recordings.get(item.recordingId)!.then(candidateData => ({ item, data: candidateData }));
    };
    Promise.allSettled(pool.map(loadCandidate)).then(results => {
      if (!active) return;
      const available = results.flatMap(result => result.status === 'fulfilled' ? [result.value] : []);
      try {
        setMatches(rankSimilarIncidents(data, interval, available).slice(0, 3));
        if (available.length < 2) setMatchIssue('Related recordings could not be loaded. The current investigation remains available.');
      } catch (cause) {
        setMatches([]);
        setMatchIssue(cause instanceof Error && cause.message.includes('raw telemetry')
          ? 'This interval only has overview data. Choose a named demo case with a complete 1.024 s raw window.'
          : cause instanceof Error ? cause.message : 'Incident matching is unavailable.');
      } finally { setMatching(false); }
    });
    return () => { active = false; };
  }, [cases, data, interval.start, interval.end, services]);
  async function chooseRecording(id: string) {
    const version = ++request.current; setReferenceId(id); setLoading(true); setError(''); setReference(undefined); setStatus('');
    try {
      const next = await recordingData(id);
      if (version !== request.current) return;
      if (next.recording.id !== id) throw new Error('The service returned a different recording.');
      setReferenceData(next);
      const suggestion = id === data.recording.id ? suggestReference(next, interval) : undefined;
      setReference(suggestion); setStart(suggestion ? fixed(suggestion.start) : ''); setSuggested(Boolean(suggestion));
    } catch (e) { if (version === request.current) setError(e instanceof Error ? e.message : 'Reference could not load. Choose it again to retry.'); }
    finally { if (version === request.current) setLoading(false); }
  }
  async function chooseMatch(match: SimilarIncident) {
    const version = ++request.current; setReferenceId(match.recordingId); setLoading(true); setError(''); setReference(undefined); setStatus('');
    try {
      const next = await recordingData(match.recordingId);
      if (version !== request.current) return;
      if (next.recording.id !== match.recordingId) throw new Error('The service returned a different recording.');
      setReferenceData(next); setReference({ ...match.interval }); setStart(fixed(match.interval.start)); setSuggested(false);
    } catch (cause) { if (version === request.current) setError(cause instanceof Error ? cause.message : 'Related incident could not load.'); }
    finally { if (version === request.current) setLoading(false); }
  }
  const computed = useMemo(() => {
    if (!reference || loading || !start.trim() || Number(start) !== reference.start) return {};
    try { return { result: compareWindows(data, interval, referenceData, reference) }; }
    catch (e) { return { issue: e instanceof Error ? e.message : 'Comparison unavailable.' }; }
  }, [data, interval, referenceData, reference, loading, start]);
  const result = computed.result;
  const selectedJoint = result?.joints.find(j => j.id === joint) ?? result?.joints[0];
  function apply() {
    if (!start.trim() || loading || referenceData.recording.id !== referenceId) { setError('Load a reference recording and enter its start time.'); return; }
    const value = Number(start); setReference({ start: value, end: Math.round((value + duration) * 1e6) / 1e6 }); setSuggested(false); setError(''); setStatus('');
  }
  function exportComparison() {
    if (!result) return;
    const content = { ...comparisonReport(result), referenceSelection: suggested ? 'Suggested earlier annotation-free window with 0.5 s marker margin; not verified normal.' : 'User-selected reference; not verified normal.' };
    const url = URL.createObjectURL(new Blob([JSON.stringify(content, null, 2) + '\n'], { type: 'application/json' }));
    const link = document.createElement('a'); link.href = url; link.download = `trace-comparison-${data.recording.id}.json`; link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000); setStatus('Comparison report downloaded.');
  }
  function exportCohort() {
    if (!matches.length) return;
    const content = {
      schemaVersion: 1,
      kind: 'trace_signal_similarity_cohort',
      origin: 'deterministic_calculation',
      selected: { recordingId: data.recording.id, interval: { ...interval }, sourceUrl: data.recording.sourceUrl, archive: data.recording.archive },
      candidates: matches,
      method: 'Rank equal-length raw 1 kHz windows by symmetric distance over each joint’s torque range, variability and largest sample-to-sample change. Publisher labels and model predictions are not scoring inputs.',
      limitation: 'Similarity is a retrieval lead, not evidence of the same physical cause, safety state or required repair. An engineer must review motion phase, payload and operating conditions.',
    };
    const url = URL.createObjectURL(new Blob([JSON.stringify(content, null, 2) + '\n'], { type: 'application/json' }));
    const link = document.createElement('a'); link.href = url; link.download = `trace-incident-cohort-${data.recording.id}.json`; link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000); setStatus('Incident cohort downloaded for review or labeling.');
  }
  return <section className="comparison-panel" aria-label="Incident comparison">
    <header><h2>Find repeat patterns</h2><p>Trace ranks similar torque events across recordings. Open one to see what changed.</p></header>
    <div className="comparison-selection"><span>Selected · {data.recording.id}</span><strong>{intervalLabel(interval)}</strong></div>
    <section className="incident-cohort" aria-labelledby="incident-cohort-title">
      <div className="incident-cohort-heading"><h3 id="incident-cohort-title">Closest matches</h3>{matches.length > 0 && <button className="text-button" onClick={exportCohort}><Download size={13}/>Export cohort</button>}</div>
      {matching && <p role="status">Searching recorded incidents…</p>}
      {!matching && matches.length > 0 && <div className="incident-matches">{matches.map((match, index) => <button key={match.caseId} className="incident-match" onClick={() => void chooseMatch(match)}><span className="incident-rank">{index + 1}</span><span><strong>{match.title}</strong><small>{match.recordingId} · {intervalLabel(match.interval)} · strongest range {match.strongestJoint?.replace('joint_', 'J') ?? '—'}</small></span><span className="incident-score">{match.score.toFixed(2)}<small>similarity</small></span></button>)}</div>}
      {!matching && matchIssue && <p className="reference-note">{matchIssue}</p>}
      {!matching && !matchIssue && !matches.length && <p className="reference-note">No other fixed raw window is eligible for this interval.</p>}
      {matches.length > 0 && <p className="cohort-limit">A match is a review lead, not proof of the same cause.</p>}
    </section>
    <details className="reference-editor"><summary>Compare a specific window</summary>
    <form className="reference-controls" onSubmit={e => { e.preventDefault(); apply(); }}>
      <label>Reference recording<select aria-label="Reference recording" value={referenceId} onChange={e => void chooseRecording(e.target.value)}>{options.map(c => <option key={c.recordingId} value={c.recordingId}>{c.recordingId === data.recording.id ? 'This recording' : 'Recording'} · {c.recordingId}</option>)}</select></label>
      <div><label>Start (seconds)<input aria-label="Reference start seconds" type="number" min="0" step="0.001" value={start} onChange={e => setStart(e.target.value)} disabled={loading}/></label><span>{fixed(duration)} s window</span><button className="btn" type="submit" disabled={loading}>Compare windows</button></div>
    </form><p className="reference-note">Choose the same motion phase and payload for a useful comparison.</p></details>
    {loading && <p role="status">Loading reference telemetry…</p>}
    {reference && Number(start) !== reference.start && <p className="reference-note">Press Compare windows to apply the new reference start.</p>}
    {error && referenceData.recording.id !== referenceId && <button className="text-button" onClick={() => void chooseRecording(referenceId)}>Retry loading reference</button>}
    {(error || computed.issue) && <p className="comparison-error" role="alert">{error || computed.issue}</p>}
    {result && selectedJoint && <>
      <div className="comparison-finding"><span>Reference · {result.reference.recordingId} · {intervalLabel(result.reference.interval)}</span><h3>{result.joints[0].rangeDelta === 0 ? 'No change in sampled torque ranges' : `${result.joints[0].name} has the largest change in torque range`}</h3><p>{fixed(result.joints[0].reference.range)} → {fixed(result.joints[0].selected.range)} Nm <strong>({signed(result.joints[0].rangeDelta)} Nm)</strong></p><span>{result.resolution === 'raw' ? 'Both windows · raw' : 'Both windows · overview; brief peaks may be missed'} · {result.sampleRateHz} Hz · {result.reference.samplesPerChannel}/{result.selected.samplesPerChannel} samples</span></div>
      <Overlay result={result} jointId={selectedJoint.id} onJoint={setJoint}/>
      <details className="comparison-all-joints"><summary>All 7 joint measurements</summary><div className="comparison-table-wrap"><table className="comparison-table"><caption>Torque range · Nm. Select a joint to inspect both signals.</caption><thead><tr><th>Joint</th><th>Reference</th><th>Selected</th><th>Change</th></tr></thead><tbody>{result.joints.map(j => <tr key={j.id} className={selectedJoint.id === j.id ? 'selected' : ''}><th><button aria-pressed={selectedJoint.id === j.id} onClick={() => setJoint(j.id)}>{j.name}</button></th><td>{fixed(j.reference.range)}</td><td>{fixed(j.selected.range)}</td><td>{signed(j.rangeDelta)}</td></tr>)}</tbody></table></div></details>
      <div className="comparison-next"><p>Check {selectedJoint.name} against the same motion phase and payload.</p><button className="text-button" onClick={() => onEvidence({ channelId: selectedJoint.id, channelIds: [selectedJoint.id], interval: { ...interval }, label: selectedJoint.name })}>Inspect selected signal <ArrowUpRight size={12}/></button></div>
      <details className="comparison-method"><summary>Measurements, method &amp; limits</summary><p>{selectedJoint.name} variability: {fixed(selectedJoint.reference.variability)} → {fixed(selectedJoint.selected.variability)} Nm. Mean shift: {signed(selectedJoint.meanDelta)} Nm.</p><p>Reference: {result.reference.recordingId}, {intervalLabel(result.reference.interval)}. Selected: {result.selected.recordingId}, {intervalLabel(result.selected.interval)}.</p><p>{result.method}</p><p>Publisher markers in reference: {result.reference.publisherAnnotations.length}; selected: {result.selected.publisherAnnotations.length}. These are annotations, not diagnoses.</p><p>{result.limitations.join(' ')}</p></details>
      <button className="btn comparison-export" onClick={exportComparison}><Download size={13}/>Export comparison</button>{status && <p role="status">{status}</p>}
    </>}
  </section>;
}

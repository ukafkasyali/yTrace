import { useEffect, useMemo, useRef, useState } from 'react';
import { ArrowUpRight, Download } from 'lucide-react';
import type { DemoCase, DemoData, EvidenceLink, Interval } from '../types';
import type { Services } from '../services';
import { downsample, loadDemoData } from '../lib/data';
import { intervalLabel } from '../lib/format';
import { compareWindows, comparisonReport, suggestReference, type WindowComparison } from './compare';

type Props = { data: DemoData; interval: Interval; cases: DemoCase[]; services: Services; onEvidence: (e: EvidenceLink) => void };
const fixed = (n: number) => n.toFixed(3);
const signed = (n: number) => `${n >= 0 ? '+' : ''}${fixed(n)}`;
function Overlay({ result, jointId }: { result: WindowComparison; jointId: string }) {
  const a = result.plot.selected, b = result.plot.reference;
  const av = a.channels.find(c => c.id === jointId)!.values, bv = b.channels.find(c => c.id === jointId)!.values;
  let low = Infinity, high = -Infinity;
  for (const values of [av, bv]) for (const v of values) { low = Math.min(low, v); high = Math.max(high, v); }
  const pad = Math.max((high - low) * .12, .01);
  const length = result.selected.interval.end - result.selected.interval.start;
  const path = (times: number[], values: number[], start: number) => downsample(times.map((t, i) => ({ x: t - start, y: values[i] })), 400)
    .map((p, i) => `${i ? 'L' : 'M'}${(p.x / length * 600).toFixed(2)},${(132 - (p.y - low + pad) / (high - low + 2 * pad) * 120).toFixed(2)}`).join(' ');
  return <figure className="comparison-plot"><figcaption><strong>{jointId.replace('joint_', 'Joint ')} · signed torque</strong><span><i/>Selected <i className="reference-key"/>Reference</span></figcaption>
    <svg viewBox="0 0 600 148" role="img" aria-label={`Selected and reference ${jointId.replace('joint_', 'joint ')} torque, aligned by window start, shared Nm scale`}>
      {[12, 72, 132].map(y => <line key={y} x1="0" x2="600" y1={y} y2={y} className="chart-grid"/>)}
      <path d={path(b.times, bv, result.reference.interval.start)} fill="none" stroke="#d9ad70" strokeDasharray="6 4" strokeWidth="2"/>
      <path d={path(a.times, av, result.selected.interval.start)} fill="none" stroke="#99d5bd" strokeWidth="2"/>
    </svg><div className="comparison-axis"><span>0 ms</span><span>{fixed(low)} to {fixed(high)} Nm · shared scale</span><span>{Math.round(length * 1000)} ms</span></div>
    <p>Aligned by window start; motion phases may differ.</p></figure>;
}
export default function ComparisonPanel({ data, interval, cases, services, onEvidence }: Props) {
  const [referenceData, setReferenceData] = useState(data);
  const [referenceId, setReferenceId] = useState(data.recording.id);
  const initial = suggestReference(data, interval);
  const [start, setStart] = useState(initial ? fixed(initial.start) : '');
  const [reference, setReference] = useState<Interval | undefined>(initial);
  const [suggested, setSuggested] = useState(Boolean(initial));
  const [loading, setLoading] = useState(false), [error, setError] = useState(''), [status, setStatus] = useState('');
  const [joint, setJoint] = useState('');
  const request = useRef(0);
  const duration = interval.end - interval.start;
  useEffect(() => () => { request.current++; }, []);
  const options = [...new Map([{ recordingId: data.recording.id, title: 'This recording' }, { recordingId: '05-28-21-25', title: 'Original recording' }, ...cases].map(c => [c.recordingId, c])).values()];
  async function chooseRecording(id: string) {
    const version = ++request.current; setReferenceId(id); setLoading(true); setError(''); setReference(undefined); setStatus('');
    try {
      const next = id === data.recording.id ? data : id === '05-28-21-25' ? await loadDemoData() : await services.getReplay(id);
      if (version !== request.current) return;
      if (next.recording.id !== id) throw new Error('The service returned a different recording.');
      setReferenceData(next);
      const suggestion = id === data.recording.id ? suggestReference(next, interval) : undefined;
      setReference(suggestion); setStart(suggestion ? fixed(suggestion.start) : ''); setSuggested(Boolean(suggestion));
    } catch (e) { if (version === request.current) setError(e instanceof Error ? e.message : 'Reference could not load. Choose it again to retry.'); }
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
  return <section className="comparison-panel" aria-label="Incident comparison">
    <header><h2>What changed?</h2><p>Compare this incident with a reference window or another run.</p></header>
    <div className="comparison-selection"><span>Selected · {data.recording.id}</span><strong>{intervalLabel(interval)}</strong></div>
    <details className="reference-editor" open={!initial}><summary>Change reference or compare another run</summary>
    <form className="reference-controls" onSubmit={e => { e.preventDefault(); apply(); }}>
      <label>Reference recording<select aria-label="Reference recording" value={referenceId} onChange={e => void chooseRecording(e.target.value)}>{options.map(c => <option key={c.recordingId} value={c.recordingId}>{c.recordingId === data.recording.id ? 'This recording' : 'Recording'} · {c.recordingId}</option>)}</select></label>
      <div><label>Start (seconds)<input aria-label="Reference start seconds" type="number" min="0" step="0.001" value={start} onChange={e => setStart(e.target.value)} disabled={loading}/></label><span>{fixed(duration)} s window</span><button className="btn" type="submit" disabled={loading}>Compare windows</button></div>
    </form></details>
    {loading ? <p role="status">Loading reference telemetry…</p> : <p className="reference-note">{suggested ? 'Suggested earlier window with no nearby publisher marker. This does not prove normal motion.' : 'Choose a reference with matching motion phase, payload and operating conditions. Normality is not verified.'}</p>}
    {reference && Number(start) !== reference.start && <p className="reference-note">Press Compare windows to apply the new reference start.</p>}
    {error && referenceData.recording.id !== referenceId && <button className="text-button" onClick={() => void chooseRecording(referenceId)}>Retry loading reference</button>}
    {(error || computed.issue) && <p className="comparison-error" role="alert">{error || computed.issue}</p>}
    {!reference && !loading && !error && <p className="comparison-empty">{referenceId === data.recording.id ? 'No suitable earlier reference was found. Enter another interval or choose a different recording.' : `Enter a reference start within 0–${fixed(referenceData.recording.durationSeconds)} s. This can be a matched window from a before/after run.`}</p>}
    {result && selectedJoint && <>
      <div className="comparison-finding"><span>Reference · {result.reference.recordingId} · {intervalLabel(result.reference.interval)}</span><h3>{result.joints[0].rangeDelta === 0 ? 'No change in sampled torque ranges' : `${result.joints[0].name} has the largest change in torque range`}</h3><p>{fixed(result.joints[0].reference.range)} → {fixed(result.joints[0].selected.range)} Nm <strong>({signed(result.joints[0].rangeDelta)} Nm)</strong></p><span>{result.resolution === 'raw' ? 'Both windows · raw' : 'Both windows · overview; brief peaks may be missed'} · {result.sampleRateHz} Hz · {result.reference.samplesPerChannel}/{result.selected.samplesPerChannel} samples</span></div>
      <div className="comparison-table-wrap"><table className="comparison-table"><caption>Torque range · Nm. Select a joint to inspect both signals.</caption><thead><tr><th>Joint</th><th>Reference</th><th>Selected</th><th>Change</th></tr></thead><tbody>{result.joints.map(j => <tr key={j.id} className={selectedJoint.id === j.id ? 'selected' : ''}><th><button aria-pressed={selectedJoint.id === j.id} onClick={() => setJoint(j.id)}>{j.name}</button></th><td>{fixed(j.reference.range)}</td><td>{fixed(j.selected.range)}</td><td>{signed(j.rangeDelta)}</td></tr>)}</tbody></table></div>
      <Overlay result={result} jointId={selectedJoint.id}/>
      <p className="comparison-secondary">{selectedJoint.name} variability: {fixed(selectedJoint.reference.variability)} → {fixed(selectedJoint.selected.variability)} Nm. Mean shift: {signed(selectedJoint.meanDelta)} Nm.</p>
      <div className="comparison-next"><h3>What to check next</h3><p>Inspect {selectedJoint.name} in both windows. Confirm the motion phase, payload and intended contact were comparable before attributing the change to a fault.</p><button className="text-button" onClick={() => onEvidence({ channelId: selectedJoint.id, channelIds: [selectedJoint.id], interval: { ...interval }, label: selectedJoint.name })}>Inspect selected signal <ArrowUpRight size={12}/></button><h3>How to verify a change</h3><p>After an engineer chooses an adjustment, compare a matched window from the new recording. Lower variation alone does not prove a repair worked or the robot is safe.</p></div>
      <details className="comparison-method"><summary>Method &amp; source windows</summary><p>Reference: {result.reference.recordingId}, {intervalLabel(result.reference.interval)}. Selected: {result.selected.recordingId}, {intervalLabel(result.selected.interval)}.</p><p>{result.method}</p><p>Publisher markers in reference: {result.reference.publisherAnnotations.length}; selected: {result.selected.publisherAnnotations.length}. These are annotations, not diagnoses.</p><p>{result.limitations.join(' ')}</p></details>
      <button className="btn comparison-export" onClick={exportComparison}><Download size={13}/>Export comparison</button>{status && <p role="status">{status}</p>}
    </>}
  </section>;
}

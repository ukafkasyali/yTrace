import { useEffect, useMemo, useState } from 'react';
import { Activity, ArrowLeft, Columns3, Database, FileText, Pause, Play, Radio, RotateCcw, SkipBack, SkipForward, Waves } from 'lucide-react';
import AssistantPanel from './assistant/AssistantPanel';
import { loadDemoData } from './lib/data';
import { intervalLabel, timecode } from './lib/format';
import RecordingExplorer from './recordings/RecordingExplorer';
import { useReplay } from './replay/useReplay';
import { createServices, type Evidence, type QueryRequest, type Recording } from './services';
import SignalViewer from './signals/SignalViewer';
import DataWorkspace from './workspaces/DataWorkspace';
import ModelWorkspace from './workspaces/ModelWorkspace';
import type { DemoData, EvidenceLink, Interval, Marker } from './types';

const services = createServices(import.meta.env.VITE_API_BASE_URL);
const SAMPLE_DATASET = 'zenodo-21927431';

export default function App() {
  const [data, setData] = useState<DemoData | null>(null);
  const [datasetId, setDatasetId] = useState(SAMPLE_DATASET);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  async function load() { setLoading(true); setError(''); try { setData(await loadDemoData()); } catch (e) { setError(e instanceof Error ? e.message : 'Could not load recording.'); } finally { setLoading(false); } }
  useEffect(() => { let alive = true; loadDemoData().then(d => { if (alive) setData(d); }).catch(e => { if (alive) setError(String(e.message)); }).finally(() => { if (alive) setLoading(false); }); return () => { alive = false; }; }, []);
  async function openRecording(recording: Recording) {
    const [signals, events] = await Promise.all([services.getWindow(recording.id, 0, recording.durationSec, recording.channels.map(c => c.id), 200000), services.getEvents(recording.id)]);
    if (recording.channels.length !== 7 || signals.series.length !== 7) throw new Error('This workbench currently supports seven synchronized robot channels.');
    const series = recording.channels.map(c => signals.series.find(s => s.channelId === c.id));
    if (series.some(s => !s)) throw new Error('One or more channels are missing.');
    const times = series[0]!.timeSec;
    if (!times.length || series.some(s => s!.values.some(v => v === null) || s!.timeSec.length !== times.length || s!.timeSec.some((t, i) => Math.abs(t-times[i]) > 1e-6))) throw new Error('Recording has gaps or unsynchronized channels. Resolve the validation findings before replaying it.');
    const channels = recording.channels.map((c, i) => ({ id: c.id, name: c.name, unit: c.unit, values: series[i]!.values as number[] }));
    const rate = times.length > 1 ? 1/(times[1]-times[0]) : recording.channels[0].sampleRateHz;
    const emptyDetail = { startSeconds: 0, endSeconds: 0, times: [] as number[], channels: channels.map(c => ({ ...c, values: [] as number[] })) };
    const next: DemoData = { recording: { id: recording.id, name: recording.name, durationSeconds: recording.durationSec, sampleRateHz: recording.channels[0].sampleRateHz, displaySampleRateHz: rate, sourceUrl: '', archive: 'Backend recording', channelCount: 7, eventCount: events.filter(e => e.origin === 'publisher_annotation').length }, channels, times, events: events.filter(e => e.origin === 'publisher_annotation').map(e => ({ id: e.id, timeSeconds: e.startSec, label: e.label, source: e.source, kind: 'publisher_annotation' as const })), detail: signals.resolution === 'raw' ? { startSeconds: 0, endSeconds: recording.durationSec, times, channels } : emptyDetail };
    setDatasetId(recording.datasetId); setData(next);
  }
  if (loading || !data) return <main className="loading-screen"><Waves size={28}/><h1>Trace</h1>{error ? <><p role="alert">{error}</p><button className="btn" onClick={() => void load()}>Retry loading recording</button></> : <><p>Opening the KUKA recording…</p><div className="loading-lines"><i/><i/><i/></div></>}</main>;
  return <Workbench key={`${datasetId}:${data.recording.id}`} data={data} datasetId={datasetId} onOpenRecording={openRecording}/>;
}

function Workbench({ data, datasetId, onOpenRecording }: { data: DemoData; datasetId: string; onOpenRecording: (recording: Recording) => Promise<void> }) {
  const replay = useReplay(data.recording.durationSeconds, 8);
  const [view, setView] = useState<'inspect' | 'data' | 'models'>('inspect');
  const [mobile, setMobile] = useState<'signals' | 'assistant'>('signals');
  const [interval, setInterval] = useState<Interval>({ start: Math.min(5.787, data.recording.durationSeconds/3), end: Math.min(6.811, data.recording.durationSeconds) });
  const [following, setFollowing] = useState(false);
  const [zoom, setZoom] = useState(10);
  const [highlighted, setHighlighted] = useState<string[]>([]);
  const [draftStart, setDraftStart] = useState(interval.start.toFixed(3));
  const [draftEnd, setDraftEnd] = useState(interval.end.toFixed(3));
  const [selectionError, setSelectionError] = useState('');
  const [availability, setAvailability] = useState('Models not connected');
  useEffect(() => { if (services.connected) { let alive = true; services.listModels().then(models => { if (alive) setAvailability(`${models.filter(m => m.available).length} models available`); }).catch(() => { if (alive) setAvailability('Model service unavailable'); }); return () => { alive = false; }; } }, []);
  useEffect(() => { setDraftStart(interval.start.toFixed(3)); setDraftEnd(interval.end.toFixed(3)); }, [interval]);
  const viewport = useMemo(() => {
    if (following) return { start: zoom ? Math.max(0, replay.playhead-zoom) : 0, end: Math.max(.01, replay.playhead) };
    const padding = (interval.end-interval.start)*.18;
    return { start: Math.max(0, interval.start-padding), end: Math.max(.01, Math.min(data.recording.durationSeconds, interval.end+padding, replay.playhead)) };
  }, [following, zoom, interval, data.recording.durationSeconds, replay.playhead]);
  const validViewport = viewport.end > viewport.start ? viewport : { start: 0, end: Math.max(.01, replay.playhead) };
  const effectiveInterval = following ? { start: Math.max(0, replay.playhead-1.024), end: replay.playhead } : interval;
  function select(next: Interval) { if (next.start < 0 || next.end <= next.start || next.end > replay.playhead) { setSelectionError('Choose a nonempty interval already reached by the replay.'); return; } replay.pause(); setFollowing(false); setInterval(next); setSelectionError(''); }
  function seek(value: number) {
    replay.seek(value); setFollowing(true); setHighlighted([]); setSelectionError('');
    setInterval({ start: Math.max(0, value-1.024), end: value });
  }
  function marker(e: Marker) { const end = Math.min(data.recording.durationSeconds, e.timeSeconds+.624); replay.seek(Math.max(replay.playhead, end)); setInterval({ start: Math.max(0, e.timeSeconds-.4), end }); setFollowing(false); setHighlighted([]); setSelectionError(''); }
  function navigateMarker(direction: number) {
    const target = direction > 0 ? data.events.find(e => e.timeSeconds > replay.playhead) : [...data.events].reverse().find(e => e.timeSeconds < (effectiveInterval.start || replay.playhead));
    if (target) marker(target);
  }
  function evidence(e: EvidenceLink) { if (e.interval.start < 0 || e.interval.end > data.recording.durationSeconds || e.interval.end <= e.interval.start) return; replay.seek(Math.max(replay.playhead, e.interval.end)); setInterval({ ...e.interval }); setFollowing(false); setHighlighted([e.channelId]); setView('inspect'); setMobile('signals'); }
  function serviceEvidence(e: Evidence) { if (e.window.recordingId !== data.recording.id) return; evidence({ channelId: e.window.channelIds[0], label: e.label, interval: { start: e.window.startSec, end: e.window.endSec } }); }
  const query: QueryRequest = { mode: 'assistant', question: 'Describe the main changes in the selected torque signals.', playheadSec: replay.playhead, window: { datasetId, recordingId: data.recording.id, startSec: effectiveInterval.start, endSec: effectiveInterval.end, channelIds: data.channels.map(c => c.id) } };
  const elapsedMarkers = data.events.filter(e => e.timeSeconds <= replay.playhead);
  return <div className="app-shell">
    <header className="app-header"><a className="brand" href="#" onClick={e => { e.preventDefault(); setView('inspect'); }}><Activity size={23} strokeWidth={1.8}/><span>trace</span></a><div className="header-divider"/><span className="project-name">Robot observability</span><span className="replay-label"><RotateCcw size={11}/>Replay</span><div className="header-right"><span className="model-status"><CpuStatus/>{availability}</span><button className="btn btn-subtle" onClick={() => { replay.pause(); setView('data'); }}><Database size={14}/>Data source</button><span className="profile" aria-label="Samet">S</span></div></header>
    <div className="app-body"><nav className="nav-rail" aria-label="Workspace"><button className={view === 'inspect' ? 'active' : ''} onClick={() => setView('inspect')} aria-label="Inspect recording" title="Inspect"><Waves size={20}/></button><button className={view === 'data' ? 'active' : ''} onClick={() => { replay.pause(); setView('data'); }} aria-label="Data sources" title="Data sources"><Database size={19}/></button><button className={view === 'models' ? 'active' : ''} onClick={() => { replay.pause(); setView('models'); }} aria-label="Compare models" title="Compare models"><Columns3 size={19}/></button><div className="nav-spacer"/><a href="https://zenodo.org/records/21927431" target="_blank" rel="noreferrer" aria-label="Open original dataset" title="Original dataset"><FileText size={18}/></a></nav>
      <main className="main-workspace"><div className="workspace-toolbar"><div className="breadcrumb"><span>KUKA experiments</span><ChevronSeparator/><strong>{view === 'inspect' ? 'Recording 05-28-21-25' : view === 'data' ? 'Data sources' : 'Model comparison'}</strong></div><span className="toolbar-source">{datasetId === SAMPLE_DATASET ? 'Real sample data' : 'Backend recording'}<span className="separator-dot">·</span>{data.recording.channelCount} channels</span></div>
        <div className="mobile-switch"><button className={mobile === 'signals' ? 'active' : ''} onClick={() => { setView('inspect'); setMobile('signals'); }}>Signals</button><button className={mobile === 'assistant' ? 'active' : ''} onClick={() => { setView('inspect'); setMobile('assistant'); }}>Assistant</button></div>
        <div className={`inspect-grid mobile-${mobile}`} style={{ display: view === 'inspect' ? undefined : 'none' }}>
          <div className="left-workspace"><RecordingExplorer data={data} playhead={replay.playhead} interval={effectiveInterval} highlighted={highlighted} onMarker={marker} onHighlight={id => setHighlighted(h => h.includes(id) ? h.filter(x => x !== id) : [id])} onData={() => { replay.pause(); setView('data'); }}/><AssistantPanel data={data} datasetId={datasetId} playhead={replay.playhead} interval={effectiveInterval} services={services} onEvidence={evidence} onCompare={() => { replay.pause(); setView('models'); }}/></div>
          <SignalViewer data={data} playhead={replay.playhead} interval={effectiveInterval} viewport={validViewport} highlighted={highlighted} onSelect={select} onFollow={() => { setFollowing(true); setZoom(10); }} onZoom={n => { setFollowing(true); setZoom(n); }}/>
        </div>
        {view !== 'inspect' && <div className="secondary-view"><button className="text-button back-inspect" onClick={() => setView('inspect')}><ArrowLeft size={14}/>Back to replay</button>{view === 'data' ? <DataWorkspace services={services} data={data} onOpenRecording={onOpenRecording}/> : <ModelWorkspace services={services} request={query} onEvidence={serviceEvidence}/>}</div>}
      </main>
    </div>
    <footer className="transport"><div className="transport-controls"><button className="icon-button" aria-label="Replay from beginning" title="Replay from beginning" onClick={() => { setFollowing(true); setHighlighted([]); replay.restart(); }}><RotateCcw size={15}/></button><button className="icon-button" aria-label="Previous publisher marker" disabled={!data.events.some(e => e.timeSeconds < effectiveInterval.start)} onClick={() => navigateMarker(-1)}><SkipBack size={15}/></button><button className="play-button" aria-label={replay.playing ? 'Pause replay' : 'Play replay'} onClick={() => { if (!replay.playing) setFollowing(true); replay.toggle(); }}>{replay.playing ? <Pause size={17} fill="currentColor"/> : <Play size={17} fill="currentColor"/>}</button><button className="icon-button" aria-label="Next publisher marker" disabled={!data.events.some(e => e.timeSeconds > replay.playhead)} onClick={() => navigateMarker(1)}><SkipForward size={15}/></button></div><div className="replay-clock"><strong className="mono">{timecode(replay.playhead, true)}</strong><span className="mono">/ {timecode(data.recording.durationSeconds)}</span></div><div className="timeline-control"><label className="sr-only" htmlFor="playhead">Replay position in seconds</label><input id="playhead" type="range" min="0" max={data.recording.durationSeconds} step="0.001" value={replay.playhead} onChange={e => seek(Number(e.target.value))} style={{ '--progress': `${replay.playhead/data.recording.durationSeconds*100}%` } as React.CSSProperties}/><div className="timeline-marks">{elapsedMarkers.map(e => <button key={e.id} style={{ left: `${e.timeSeconds/data.recording.durationSeconds*100}%` }} aria-label={`Seek to ${e.label} at ${e.timeSeconds.toFixed(3)} seconds`} title={`${e.label} · ${e.timeSeconds.toFixed(3)} s`} onClick={() => marker(e)}/>)}</div></div><label className="speed-control"><span className="sr-only">Playback speed</span><select aria-label="Playback speed" value={replay.speed} onChange={e => replay.setSpeed(Number(e.target.value))}>{[.5,1,2,4].map(n => <option key={n} value={n}>{n}×</option>)}</select></label><span className="play-state">{replay.playing ? 'Playing' : 'Paused'}</span></footer>
    <div className="selection-footer"><span><span className="selection-dot"/>{following ? 'Following playhead' : 'Interval selected'} <strong className="mono">{intervalLabel(effectiveInterval)}</strong></span><form onSubmit={e => { e.preventDefault(); select({ start: Number(draftStart), end: Number(draftEnd) }); }}><label>From <input aria-label="Selection start seconds" type="number" step="0.001" min="0" max={replay.playhead} value={draftStart} onChange={e => setDraftStart(e.target.value)}/></label><label>to <input aria-label="Selection end seconds" type="number" step="0.001" min="0" max={replay.playhead} value={draftEnd} onChange={e => setDraftEnd(e.target.value)}/></label><button type="submit">Apply</button></form><span className="annotation-key"><i/>Publisher marker</span>{selectionError && <span role="alert" className="error-message">{selectionError}</span>}</div>
  </div>;
}
function CpuStatus() { return <Radio size={13}/>; }
function ChevronSeparator() { return <span className="breadcrumb-separator">/</span>; }

import { useEffect, useMemo, useRef, useState } from 'react';
import { Activity, ArrowLeft, BarChart3, Database, Pause, Play, Radio, RotateCcw, SkipBack, SkipForward, Waves } from 'lucide-react';
import AssistantPanel from './assistant/AssistantPanel';
import ComparisonPanel from './comparison/ComparisonPanel';
import type { PredictionCue } from './assistant/predictionBrief';
import { loadDemoData, selectWindow } from './lib/data';
import { intervalLabel, timecode } from './lib/format';
import RecordingContext from './recordings/RecordingContext';
import { markerWindow } from './recordings/markerAnalysis';
import { useModelRegistry } from './services/useModelRegistry';
import { useRawWindow } from './lib/useRawWindow';
import { useReplay } from './replay/useReplay';
import { ApiError, createServices, type ImportedDatasetSelection, type Recording } from './services';
import SignalViewer from './signals/SignalViewer';
import DataWorkspace from './workspaces/DataWorkspace';
import EvaluationWorkspace from './evaluation/EvaluationWorkspace';
import type { DemoCase, DemoData, EvidenceLink, Interval, Marker } from './types';

const services = createServices(import.meta.env.VITE_API_BASE_URL);
const rationaleServices = createServices(import.meta.env.VITE_RATIONALE_API_BASE_URL);
const SAMPLE_DATASET = 'zenodo-21927431';

export function initialReplayInterval(data: DemoData): Interval {
  if (data.demoCase) return data.demoCase.interval;
  if (data.events.length) return markerWindow(data.events[0], data.recording.durationSeconds);
  const start = Math.min(5.787, data.recording.durationSeconds / 3);
  return { start, end: Math.min(start + 1.024, data.recording.durationSeconds) };
}

export default function App() {
  const [data, setData] = useState<DemoData | null>(null);
  const [cases, setCases] = useState<DemoCase[]>([]);
  const [caseLoading, setCaseLoading] = useState(false);
  const [caseError, setCaseError] = useState('');
  useEffect(() => { if (services.connected) services.listDemoCases().then(setCases).catch(() => setCaseError('Example cases unavailable. Check the inference connection, then reload.')); }, []);
  async function openCase(id: string) {
    const item = cases.find(c => c.id === id); if (!item || caseLoading) return;
    setCaseLoading(true); setCaseError('');
    try { const next = await services.getReplay(item.recordingId); setDatasetId(SAMPLE_DATASET); setData({ ...next, demoCase: item }); }
    catch (e) { setCaseError(e instanceof Error ? e.message : 'Could not open example. Choose it again to retry.'); }
    finally { setCaseLoading(false); }
  }
  const [datasetId, setDatasetId] = useState(SAMPLE_DATASET);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  async function load() { setLoading(true); setError(''); try { setData(await loadDemoData()); } catch (e) { setError(e instanceof Error ? e.message : 'Could not load recording.'); } finally { setLoading(false); } }
  useEffect(() => { let alive = true; loadDemoData().then(d => { if (alive) setData(d); }).catch(e => { if (alive) setError(String(e.message)); }).finally(() => { if (alive) setLoading(false); }); return () => { alive = false; }; }, []);
  async function openRecording(recording: Recording) {
    try {
      const replayData = await services.getReplay(recording.id);
      setDatasetId(recording.datasetId); setData(replayData); return;
    } catch (error) {
      // Older adapters may only implement signals/events; malformed replay data must fail.
      if (!(error instanceof ApiError) || error.status !== 404) throw error;
    }
    const [signals, events] = await Promise.all([services.getWindow(recording.id, 0, recording.durationSec, recording.channels.map(c => c.id), 200000), services.getEvents(recording.id)]);
    if (recording.channels.some(c => c.unit !== 'Nm')) throw new Error('This torque workbench requires all channels in Nm. Convert units during ingestion.');
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
  async function openImportedRecord(selection: ImportedDatasetSelection, recordKey: string) {
    const replayData = await services.getImportedReplay(selection.ingestionId, recordKey);
    setDatasetId(selection.datasetId);
    setData(replayData);
  }
  if (loading || !data) return <main className="loading-screen"><Waves size={28}/><h1>y/trace</h1>{error ? <><p role="alert">{error}</p><button className="btn" onClick={() => void load()}>Retry loading recording</button></> : <><p>Opening the KUKA recording…</p><div className="loading-lines"><i/><i/><i/></div></>}</main>;
  return <Workbench key={`${datasetId}:${data.recording.id}:${data.demoCase?.id ?? "default"}`} data={data} datasetId={datasetId} onOpenRecording={openRecording} onOpenImportedRecord={openImportedRecord} cases={cases} onOpenCase={openCase} caseLoading={caseLoading} caseError={caseError}/>;
}

function Workbench({ data: sourceData, datasetId, onOpenRecording, onOpenImportedRecord, cases, onOpenCase, caseLoading, caseError }: { data: DemoData; datasetId: string; onOpenRecording: (recording: Recording) => Promise<void>; onOpenImportedRecord: (selection: ImportedDatasetSelection, recordKey: string) => Promise<void>; cases: DemoCase[]; onOpenCase: (id: string) => Promise<void>; caseLoading: boolean; caseError: string }) {
  const initialInterval = initialReplayInterval(sourceData);
  const importedRecording = sourceData.recording.id.startsWith('imported:');
  const replay = useReplay(sourceData.recording.durationSeconds, initialInterval.start);
  const [visualMode, setVisualMode] = useState<'robot' | 'signals' | 'markers' | 'compare'>(importedRecording ? 'signals' : 'robot');
  const [view, setView] = useState<'inspect' | 'data' | 'evaluation'>('inspect');
  const [mobile, setMobile] = useState<'signals' | 'assistant'>('signals');
  const [interval, setInterval] = useState<Interval>(initialInterval);
  const rawWindow = useRawWindow(sourceData, datasetId, interval, services);
  const data = rawWindow.data;
  const [following, setFollowing] = useState(false);
  const [zoom, setZoom] = useState(10);
  const [highlighted, setHighlighted] = useState<string[]>([]);
  const [robotPrediction, setRobotPrediction] = useState<PredictionCue>();
  const [replayStop, setReplayStop] = useState<number>();
  const timelineMarkers = useRef(new Map<string, HTMLButtonElement>());
  useEffect(() => {
    if (replayStop !== undefined && replay.playhead >= replayStop) { replay.seek(replayStop); setReplayStop(undefined); }
  }, [replay.playhead, replayStop, replay.seek]);
  const visiblePrediction = robotPrediction && robotPrediction.interval.start === interval.start && robotPrediction.interval.end === interval.end ? robotPrediction : undefined;
  function showRobotPrediction(prediction?: PredictionCue) {
    setRobotPrediction(prediction);
    if (!prediction) { setHighlighted([]); return; }
    const postOnsetContext = Math.max(.08, (prediction.interval.end - prediction.interval.start) * .12);
    setReplayStop(undefined); setInterval({ ...prediction.interval });
    replay.seek(Math.min(prediction.interval.end, prediction.onsetSeconds + postOnsetContext)); setFollowing(false); setHighlighted(prediction.channelId ? [prediction.channelId] : []);
    setView('inspect'); setMobile('signals'); setVisualMode('robot');
  }
  function openComparison(window: Interval) {
    replay.pause(); setReplayStop(undefined); setInterval({ ...window }); setVisualMode('compare'); setMobile('signals'); setView('inspect');
  }
  function replayInterval() {
    replay.seek(interval.start); replay.setSpeed(.5); setReplayStop(interval.end); setFollowing(true); replay.toggle();
  }
  const [draftStart, setDraftStart] = useState(interval.start.toFixed(3));
  const [draftEnd, setDraftEnd] = useState(interval.end.toFixed(3));
  const [selectionError, setSelectionError] = useState('');
  const registry = useModelRegistry(services);
  const rationaleRegistry = useModelRegistry(rationaleServices);
  const activeRegistry = useMemo(() => importedRecording ? {
    ...registry,
    models: registry.models.map(model => ({ ...model, available: false, reason: 'Imported records currently support replay and measurements only.' })),
  } : registry, [importedRecording, registry]);
  const activeRationaleRegistry = useMemo(() => importedRecording ? {
    ...rationaleRegistry,
    models: rationaleRegistry.models.map(model => ({ ...model, available: false, reason: 'Imported records currently support replay and measurements only.' })),
  } : rationaleRegistry, [importedRecording, rationaleRegistry]);
  const availability = importedRecording ? 'Imported replay · measurements only' : !services.connected ? 'Models not connected' : registry.loading ? 'Checking models…' : registry.error ? 'Model service unavailable' : registry.models.some(m => m.id === 'opentslm' && m.available) ? 'OpenTSLM connected' : 'Model unavailable';
  useEffect(() => { setDraftStart(interval.start.toFixed(3)); setDraftEnd(interval.end.toFixed(3)); }, [interval]);
  const previewChannels = useMemo(() => {
    if (highlighted.length && highlighted.length <= 2) return highlighted;
    try {
      return selectWindow(data, interval).channels.map(c => ({ id: c.id, range: Math.max(...c.values) - Math.min(...c.values) }))
        .sort((a, b) => b.range - a.range).slice(0, 2).map(c => c.id);
    } catch { return data.channels.slice(0, 2).map(c => c.id); }
  }, [data, interval, highlighted]);
  const viewport = useMemo(() => {
    if (following) return { start: zoom ? Math.max(0, replay.playhead-zoom) : 0, end: Math.max(.01, replay.playhead) };
    const padding = (interval.end-interval.start)*.18;
    return { start: Math.max(0, interval.start-padding), end: Math.max(.01, Math.min(data.recording.durationSeconds, interval.end+padding, replay.playhead)) };
  }, [following, zoom, interval, data.recording.durationSeconds, replay.playhead]);
  const validViewport = viewport.end > viewport.start ? viewport : { start: 0, end: Math.max(.01, replay.playhead) };
  // Playback moves the visual cursor, never the investigation's selected input.
  const effectiveInterval = interval;
  function select(next: Interval) { setReplayStop(undefined); if (next.start < 0 || next.end <= next.start || next.end > data.recording.durationSeconds) { setSelectionError('Choose a nonempty interval within this recording.'); return; } replay.pause(); setFollowing(false); setInterval(next); setSelectionError(''); }
  function seek(value: number) {
    setReplayStop(undefined); replay.seek(value); setFollowing(true); setHighlighted([]); setSelectionError('');
  }
  function marker(e: Marker) {
    setReplayStop(undefined);
    const context = markerWindow(e, data.recording.durationSeconds);
    replay.seek(context.start);
    setInterval(context);
    setVisualMode('robot');
    setMobile('signals');
    setFollowing(false);
    setHighlighted([]);
    setSelectionError('');
  }
  function navigateMarker(direction: number) {
    const target = direction > 0 ? data.events.find(e => e.timeSeconds > Math.max(interval.end, replay.playhead)) : [...data.events].reverse().find(e => e.timeSeconds < (effectiveInterval.start || replay.playhead));
    if (target) marker(target);
  }
  const selectedMarkerId = data.events.find(event => event.timeSeconds >= interval.start && event.timeSeconds < interval.end)?.id
    ?? data.events.find(event => event.timeSeconds >= replay.playhead)?.id
    ?? data.events.at(-1)?.id;
  function moveMarkerFocus(event: React.KeyboardEvent<HTMLButtonElement>, index: number) {
    const keyOffset = event.key === 'ArrowLeft' || event.key === 'ArrowUp' ? -1 : event.key === 'ArrowRight' || event.key === 'ArrowDown' ? 1 : 0;
    const nextIndex = event.key === 'Home' ? 0 : event.key === 'End' ? data.events.length - 1 : Math.max(0, Math.min(data.events.length - 1, index + keyOffset));
    if (!keyOffset && event.key !== 'Home' && event.key !== 'End') return;
    event.preventDefault();
    const target = data.events[nextIndex];
    if (!target) return;
    marker(target);
    requestAnimationFrame(() => timelineMarkers.current.get(target.id)?.focus());
  }
  function evidence(e: EvidenceLink) { setReplayStop(undefined); if (e.interval.start < 0 || e.interval.end > data.recording.durationSeconds || e.interval.end <= e.interval.start) return; replay.seek(e.interval.end); setInterval({ ...e.interval }); setFollowing(false); setHighlighted(e.channelIds?.length ? e.channelIds : [e.channelId]); setView('inspect'); setMobile('signals'); setVisualMode('signals'); }
  return <div className={`app-shell view-${view}`}>
    <header className="app-header"><a className="brand" href="#" onClick={e => { e.preventDefault(); setView('inspect'); }}><Activity size={23} strokeWidth={1.8}/><span>trace</span></a><div className="header-divider"/><span className="project-name">Recorded incident handoff</span><span className="replay-label"><RotateCcw size={11}/>Replay</span><div className="header-right"><span className="model-status"><CpuStatus/>{availability}</span><button className="btn btn-subtle" aria-pressed={view === 'evaluation'} onClick={() => { replay.pause(); setView('evaluation'); }}><BarChart3 size={14}/>Evaluation</button><button className="btn btn-subtle" aria-pressed={view === 'data'} onClick={() => { replay.pause(); setView('data'); }}><Database size={14}/>Data source</button></div></header>
    <div className="app-body">
      <main className="main-workspace">
        {view === 'inspect' && (cases.length > 0 || caseError) && <div className="demo-cases"><label>Recording <select aria-label="Example case" disabled={caseLoading} value={data.demoCase?.id ?? 'current'} onChange={e => { if (e.target.value !== 'current') void onOpenCase(e.target.value); }}><option value="current">{data.recording.name}</option>{cases.map(c => <option key={c.id} value={c.id}>{c.title}</option>)}</select></label><details className="case-source"><summary>Source · {data.recording.id}</summary><p>{data.demoCase?.note ?? (importedRecording ? 'Imported, validated TimeF torque record. Publisher markers remain source annotations. Robot geometry is illustrative because this replay does not load recorded joint positions.' : 'Original KUKA recording. Publisher markers are annotations, not verified contact onset. Raw model input is available from 4 to 9 seconds.')}</p></details>{(caseLoading || caseError) && <span role={caseError ? 'alert' : 'status'}>{caseLoading ? 'Loading raw telemetry…' : caseError}</span>}</div>}
        {view === 'inspect' && <div className="mobile-switch"><button className={mobile === 'signals' ? 'active' : ''} onClick={() => { setView('inspect'); setMobile('signals'); setVisualMode('signals'); }}>Replay</button><button className={mobile === 'assistant' ? 'active' : ''} onClick={() => { setView('inspect'); setMobile('assistant'); }}>Investigation</button></div>}
        <div className={`inspect-grid mobile-${mobile}`} style={{ display: view === 'inspect' ? undefined : 'none' }}>
          <div className="left-workspace"><AssistantPanel rawLoading={rawWindow.loading} rawError={rawWindow.error} onRetryRaw={rawWindow.retry} registry={activeRegistry} experimental={rationaleServices.connected ? { services: rationaleServices, registry: activeRationaleRegistry } : undefined} data={data} datasetId={datasetId} playhead={replay.playhead} interval={effectiveInterval} services={services} onEvidence={evidence} onCompare={openComparison} onRobotPrediction={showRobotPrediction} onModelWindow={select}/></div>
          <div className={`visual-workspace visual-${visualMode}`}>
            <div className="visual-tabs" role="group" aria-label="Replay view">{(['robot', 'signals', 'markers', 'compare'] as const).map(mode => <button key={mode} aria-pressed={visualMode === mode} onClick={() => setVisualMode(mode)}>{mode === 'robot' ? 'Robot' : mode === 'signals' ? 'All 7 signals' : mode === 'markers' ? 'Publisher markers' : 'Compare'}</button>)}<button className="text-button replay-interval" onClick={replayInterval}><Play size={12}/>Replay interval · 0.5×</button></div>
            {visualMode === 'compare' && <ComparisonPanel key={`${interval.start}:${interval.end}`} data={data} interval={interval} cases={cases} services={services} onEvidence={e => { evidence(e); replay.seek(e.interval.end); }}/> }
            {(visualMode === 'robot' || visualMode === 'markers') && <RecordingContext prediction={visiblePrediction} display={visualMode === 'robot' ? 'robot' : 'overview'} datasetId={datasetId} data={data} playhead={replay.playhead} interval={effectiveInterval} highlighted={highlighted} onMarker={marker} onHighlight={id => setHighlighted(h => h.includes(id) ? h.filter(x => x !== id) : [id])} onData={() => { replay.pause(); setView('data'); }}/>}
            {(visualMode === 'robot' || visualMode === 'signals') && <SignalViewer visibleChannelIds={visualMode === 'robot' ? previewChannels : undefined} prediction={visiblePrediction} data={data} playhead={replay.playhead} interval={effectiveInterval} viewport={validViewport} highlighted={highlighted} following={following} zoom={zoom} onSelect={select} onFollow={() => { setFollowing(true); setZoom(10); }} onZoom={n => { setFollowing(true); setZoom(n); }}/>}
          </div>
        </div>
        {view === 'data' && <div className="secondary-view"><button className="text-button back-inspect" onClick={() => setView('inspect')}><ArrowLeft size={14}/>Back to replay</button><DataWorkspace services={services} data={data} onOpenRecording={onOpenRecording} onOpenImportedRecord={onOpenImportedRecord}/></div>}
        {view === 'evaluation' && <div className="secondary-view"><button className="text-button back-inspect" onClick={() => setView('inspect')}><ArrowLeft size={14}/>Back to replay</button><EvaluationWorkspace /></div>}
      </main>
    </div>
    {view === 'inspect' && <><footer className="transport"><div className="transport-controls"><button className="icon-button" aria-label="Replay from beginning" title="Replay from beginning" onClick={() => { setReplayStop(undefined); setFollowing(true); setHighlighted([]); replay.restart(); }}><RotateCcw size={15}/></button><button className="icon-button" aria-label="Previous publisher marker" disabled={!data.events.some(e => e.timeSeconds < effectiveInterval.start)} onClick={() => navigateMarker(-1)}><SkipBack size={15}/></button><button className="play-button" aria-label={replay.playing ? 'Pause replay' : 'Play replay'} onClick={() => { if (!replay.playing) setFollowing(true); replay.toggle(); }}>{replay.playing ? <Pause size={17} fill="currentColor"/> : <Play size={17} fill="currentColor"/>}</button><button className="icon-button" aria-label="Next publisher marker" disabled={!data.events.some(e => e.timeSeconds > Math.max(interval.end, replay.playhead))} onClick={() => navigateMarker(1)}><SkipForward size={15}/></button></div><div className="replay-clock"><strong className="mono">{timecode(replay.playhead, true)}</strong><span className="mono">/ {timecode(data.recording.durationSeconds)}</span></div><div className="timeline-control"><label className="sr-only" htmlFor="playhead">Replay position in seconds</label><input id="playhead" type="range" min="0" max={data.recording.durationSeconds} step="0.001" value={replay.playhead} onChange={e => seek(Number(e.target.value))} style={{ '--progress': `${replay.playhead/data.recording.durationSeconds*100}%` } as React.CSSProperties}/><div className="timeline-marks" role="toolbar" aria-label="Publisher event markers">{data.events.map((e, index) => <button key={e.id} ref={node => { if (node) timelineMarkers.current.set(e.id, node); else timelineMarkers.current.delete(e.id); }} tabIndex={e.id === selectedMarkerId ? 0 : -1} aria-pressed={e.id === selectedMarkerId} className={e.timeSeconds <= replay.playhead ? 'reached' : 'upcoming'} style={{ left: `${e.timeSeconds/data.recording.durationSeconds*100}%` }} aria-label={`${e.label}, publisher annotation at ${e.timeSeconds.toFixed(3)} seconds${e.timeSeconds > replay.playhead ? ', upcoming' : ''}`} title={`${e.label} · ${e.timeSeconds.toFixed(3)} s · publisher annotation`} onKeyDown={event => moveMarkerFocus(event, index)} onClick={() => marker(e)}/>)}</div></div><label className="speed-control"><span className="sr-only">Playback speed</span><select aria-label="Playback speed" value={replay.speed} onChange={e => replay.setSpeed(Number(e.target.value))}>{[.5,1,2,4].map(n => <option key={n} value={n}>{n}×</option>)}</select></label><span className="play-state">{replay.playing ? 'Playing' : 'Paused'}</span></footer>
    <div className="selection-footer"><span><span className="selection-dot"/>Investigation interval <strong className="mono">{intervalLabel(effectiveInterval)}</strong></span><form onSubmit={e => { e.preventDefault(); select({ start: Number(draftStart), end: Number(draftEnd) }); }}><label>From <input aria-label="Selection start seconds" type="number" step="0.001" min="0" max={data.recording.durationSeconds} value={draftStart} onChange={e => setDraftStart(e.target.value)}/></label><label>to <input aria-label="Selection end seconds" type="number" step="0.001" min="0" max={data.recording.durationSeconds} value={draftEnd} onChange={e => setDraftEnd(e.target.value)}/></label><button type="submit">Apply</button></form><button className="text-button" disabled={replay.playhead < 1.024} onClick={() => { const end = Math.floor(replay.playhead * 1000) / 1000; select({ start: Math.round((end - 1.024) * 1000) / 1000, end }); }}>Select at cursor</button>{selectionError && <span role="alert" className="error-message">{selectionError}</span>}</div></>}
  </div>;
}
function CpuStatus() { return <Radio size={13}/>; }

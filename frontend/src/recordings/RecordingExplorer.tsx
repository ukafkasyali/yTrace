import { useMemo } from 'react';
import { ArrowUpRight, ChevronRight, Flag, Info } from 'lucide-react';
import { selectWindow } from '../lib/data';
import { COLORS, intervalLabel } from '../lib/format';
import type { DemoData, Interval, Marker } from '../types';

type Props = { data: DemoData; playhead: number; interval: Interval; highlighted: string[]; onMarker: (marker: Marker) => void; onHighlight: (id: string) => void; onData: () => void };
export default function RecordingExplorer({ data, playhead, interval, highlighted, onMarker, onHighlight, onData }: Props) {
  const elapsed = data.events.filter(e => e.timeSeconds <= playhead);
  const next = data.events.find(e => e.timeSeconds > playhead);
  const selectedMarker = data.events.find(event => event.timeSeconds >= interval.start && event.timeSeconds < interval.end);
  const stats = useMemo(() => {
    if (interval.end > playhead) return null;
    try { const w = selectWindow(data, interval); return { resolution: w.resolution, ranges: w.channels.map(c => ({ id: c.id, name: c.name, range: Math.max(...c.values)-Math.min(...c.values) })) }; } catch { return null; }
  }, [data, interval, playhead]);
  const maximum = Math.max(.001, ...(stats?.ranges.map(r => r.range) ?? [1]));
  const leading = stats?.ranges.reduce((a, b) => b.range > a.range ? b : a);
  return <section className="explorer-panel" aria-label="Recording explorer">
    <header className="panel-heading"><div><h2>Recording explorer</h2><span>{data.recording.id}</span></div><button className="text-button" onClick={onData}>Source details <ArrowUpRight size={14}/></button></header>
    <div className="recording-title"><h1>{data.recording.sourceUrl.includes("21927431") ? "KUKA LWR4+" : data.recording.name}</h1><p>Recorded torque experiment <span>{data.channels.length} joints · {data.recording.durationSeconds.toFixed(0)} s · {data.recording.sampleRateHz} Hz source</span></p></div>
    <div className="explorer-body">
      <aside className="event-browser"><div className="section-label"><Flag size={12}/>Annotations <span>{elapsed.length}/{data.events.length}</span></div><div className="event-list">{elapsed.slice(-6).map(e => <button key={e.id} onClick={() => onMarker(e)} className={`event-item ${e.timeSeconds >= interval.start && e.timeSeconds < interval.end ? 'selected' : ''}`}><span className="event-tick"/><span>{e.label}<small>{e.timeSeconds.toFixed(3)} s</small></span><ChevronRight size={13}/></button>)}{!elapsed.length && <p className="quiet-empty">No markers reached yet.</p>}</div>{next && <button className="text-button next-marker" onClick={() => onMarker(next)}>Next marker <ChevronRight size={13}/></button>}</aside>
      <div className="range-overview"><div className="range-title"><h3>Torque range</h3><span className="mono">{intervalLabel(interval)}</span></div><div className="range-bars" aria-label="Maximum minus minimum torque per joint">{data.channels.map((channel, i) => { const value = stats?.ranges[i]?.range; return <button key={channel.id} className={`range-row ${highlighted.includes(channel.id) ? 'active' : ''}`} onClick={() => onHighlight(channel.id)} aria-label={`Highlight ${channel.name}, range ${value?.toFixed(3) ?? 'unavailable'} Nm`}><span>J{i+1}</span><div className="bar-track"><div style={{ width: `${(value ?? 0)/maximum*100}%`, background: COLORS[i] }}/></div><span className="mono">{value?.toFixed(3) ?? '—'} <small>Nm</small></span></button>; })}</div><div className="range-caption">{leading ? <><span>{leading.name}</span> has the widest excursion in this interval.</> : 'Select a replayed interval to compare joints.'}</div></div>
    </div>
    <footer className="explorer-footer"><details className="marker-explainer"><summary><Info size={13}/><span>{selectedMarker ? `${selectedMarker.label} · marker details` : 'What is a marker?'}</span></summary><div className="marker-detail"><strong>{selectedMarker ? `${selectedMarker.label} at ${selectedMarker.timeSeconds.toFixed(3)} s` : 'Publisher event marker'}</strong><p>This timestamp comes from <span className="mono">{selectedMarker?.source ?? 'JK_moments'}</span>, the dataset publisher's manual event annotation. It helps navigate the recording, but it is not an independently verified exact collision onset.</p>{selectedMarker && <button className="text-button" onClick={() => onMarker(selectedMarker)}>Replay its context <ChevronRight size={13}/></button>}</div></details><span className="data-resolution">{stats?.resolution === 'raw' ? 'Raw samples' : 'Display samples'}</span></footer>
  </section>;
}

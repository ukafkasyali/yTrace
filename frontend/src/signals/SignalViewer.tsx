import { useMemo, useRef } from 'react';
import { Crosshair, Maximize2 } from 'lucide-react';
import { downsample, selectWindow } from '../lib/data';
import { COLORS } from '../lib/format';
import type { DemoData, Interval } from '../types';

type Props = { visibleChannelIds?: string[]; data: DemoData; playhead: number; interval: Interval; viewport: Interval; highlighted: string[]; following: boolean; zoom: number; onSelect: (v: Interval) => void; onFollow: () => void; onZoom: (seconds: number) => void };

export default function SignalViewer({ visibleChannelIds, data, playhead, interval, viewport, highlighted, following, zoom, onSelect, onFollow, onZoom }: Props) {
  const hover = useRef<number | null>(null);
  const panel = useRef<HTMLDivElement>(null);
  const drag = useRef<number | null>(null);
  const end = Math.min(viewport.end, playhead);
  const visible = useMemo(() => {
    if (end <= viewport.start) return null;
    try { return selectWindow(data, { start: viewport.start, end }); } catch { return null; }
  }, [data, viewport.start, end]);
  const plots = useMemo(() => data.channels.map((channel, index) => {
    const values = visible?.channels[index].values ?? [];
    const times = visible?.times ?? [];
    const points = downsample(times.map((x, i) => ({ x, y: values[i] })), 600);
    let low = values.length ? Math.min(...values) : -1;
    let high = values.length ? Math.max(...values) : 1;
    const pad = Math.max((high - low) * .15, .01); low -= pad; high += pad;
    const x = (t: number) => (t - viewport.start) / (viewport.end - viewport.start) * 1000;
    const y = (v: number) => 70 - (v - low) / (high - low) * 64;
    const path = points.map((p, i) => `${i ? 'L' : 'M'}${x(p.x).toFixed(2)},${y(p.y).toFixed(2)}`).join(' ');
    return { channel, colorIndex: index, values, times, path, low, high, zero: y(0), last: values.at(-1) };
  }).filter(p => !visibleChannelIds || visibleChannelIds.includes(p.channel.id)), [data, visible, viewport, visibleChannelIds]);
  function pointerTime(event: React.PointerEvent<SVGSVGElement>) {
    const rect = event.currentTarget.getBoundingClientRect();
    return Math.max(viewport.start, Math.min(playhead, viewport.start + (event.clientX - rect.left) / rect.width * (viewport.end - viewport.start)));
  }
  function beginSelection(event: React.PointerEvent<SVGSVGElement>) {
    if (!event.isPrimary || event.button !== 0) return;
    drag.current = pointerTime(event);
    event.currentTarget.setPointerCapture(event.pointerId);
  }
  function finishSelection(event: React.PointerEvent<SVGSVGElement>) {
    if (drag.current === null) return;
    const time = pointerTime(event);
    const start = Math.min(time, drag.current), finish = Math.max(time, drag.current);
    drag.current = null;
    if (finish - start >= .01) onSelect({ start, end: finish });
  }
  function cancelSelection() { drag.current = null; }
  function showCursor(time: number | null) {
    hover.current = time;
    if (!panel.current) return;
    panel.current.style.setProperty('--cursor', `${time === null ? -100 : (time - viewport.start) / (viewport.end - viewport.start) * 100}%`);
    panel.current.querySelectorAll<HTMLElement>('[data-sample-value]').forEach((el, i) => {
      const p = plots[i];
      let value = p.last;
      if (time !== null && p.times.length) {
        let lo = 0, hi = p.times.length - 1;
        while (lo < hi) { const mid = (lo + hi) >>> 1; if (p.times[mid] < time) lo = mid + 1; else hi = mid; }
        const idx = lo > 0 && Math.abs(p.times[lo - 1] - time) < Math.abs(p.times[lo] - time) ? lo - 1 : lo;
        value = p.values[idx];
      }
      el.textContent = value === undefined ? '—' : value.toFixed(3);
    });
    const label = panel.current.querySelector('[data-cursor-time]');
    if (label) label.textContent = time === null ? 'Hover to inspect · drag to select' : `Cursor ${time.toFixed(3)} s · nearest available samples`;
  }
  const left = Math.max(0, (interval.start - viewport.start) / (viewport.end - viewport.start) * 100);
  const right = Math.min(100, (Math.min(interval.end, playhead) - viewport.start) / (viewport.end - viewport.start) * 100);
  return <section className="signals-panel" ref={panel} aria-label="Synchronized torque signals">
    <header className="panel-heading"><div><h2>{visibleChannelIds ? "Torque evidence" : "All joint signals"}</h2><span>Signed external torque</span></div><div className="plot-actions"><select aria-label="Signal zoom" value={following ? String(zoom) : "selection"} onChange={e => onZoom(Number(e.target.value))}><option value="selection" disabled>Selection</option><option value="1.024">1.024 s</option><option value="5">5 s</option><option value="10">10 s</option><option value="0">Full history</option></select><button className="icon-button" title="Follow playhead" aria-label="Follow playhead" aria-pressed={following} onClick={onFollow}><Crosshair size={16}/></button></div></header>
    <div className="signal-strips">{plots.map(p => <div key={p.channel.id} className={`signal-strip ${highlighted.includes(p.channel.id) ? 'signal-highlighted' : ''}`} data-channel={p.channel.id}>
      <div className="strip-heading"><span className="channel-name"><i style={{ background: COLORS[p.colorIndex] }}/>{p.channel.name}<span className="unit">Nm</span></span><span className="mono sample-value" data-sample-value>{p.last?.toFixed(3) ?? '—'}</span></div>
      <div className="plot-frame"><div className="y-labels"><span>{p.high.toFixed(p.high-p.low < 1 ? 2 : 1)}</span><span>{p.low.toFixed(p.high-p.low < 1 ? 2 : 1)}</span></div><div className="plot-area">
        {right > left && <div className="selection-band" style={{ left: `${left}%`, width: `${right - left}%` }}/>}<div className="hover-guide"/>
        <svg viewBox="0 0 1000 76" preserveAspectRatio="none" role="img" aria-label={`${p.channel.name}, signed external torque in Nm, ${viewport.start.toFixed(3)} to ${end.toFixed(3)} seconds`} onPointerMove={e => showCursor(pointerTime(e))} onPointerLeave={() => showCursor(null)} onPointerDown={beginSelection} onPointerUp={finishSelection} onPointerCancel={cancelSelection} onLostPointerCapture={cancelSelection}>
          {[0, 250, 500, 750, 1000].map(x => <line key={x} x1={x} x2={x} y1="0" y2="76" className="chart-grid"/>)}
          {[18, 38, 58].map(y => <line key={y} x1="0" x2="1000" y1={y} y2={y} className="chart-grid"/>)}
          {p.zero > 0 && p.zero < 76 && <line x1="0" x2="1000" y1={p.zero} y2={p.zero} className="chart-zero"/>}
          {data.events.filter(e => e.timeSeconds >= viewport.start && e.timeSeconds <= end).map(e => <line key={e.id} x1={(e.timeSeconds - viewport.start)/(viewport.end-viewport.start)*1000} x2={(e.timeSeconds - viewport.start)/(viewport.end-viewport.start)*1000} y1="0" y2="76" className="event-line"/>)}
          <path d={p.path} fill="none" stroke={COLORS[p.colorIndex]} strokeWidth="1.3" vectorEffect="non-scaling-stroke"/>
        </svg>
      </div></div>
    </div>)}</div>
    <div className="shared-axis">{[0, .25, .5, .75, 1].map(v => <span key={v}>{(viewport.start+(viewport.end-viewport.start)*v).toFixed(2)} s</span>)}</div>
    <footer className="plot-footer"><span data-cursor-time>Hover to inspect · drag to select</span><span><Maximize2 size={12}/>{visible ? `${visible.sampleRateHz >= 1000 ? `${(visible.sampleRateHz / 1000).toFixed(0)} kHz` : `${visible.sampleRateHz.toFixed(0)} Hz`} ${visible.resolution === 'raw' ? 'raw' : 'overview'} · extrema-preserving plot` : 'No replayed samples'}</span></footer>
  </section>;
}

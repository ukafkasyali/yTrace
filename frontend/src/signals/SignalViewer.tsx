import { useMemo, useRef } from 'react';
import { Crosshair, Maximize2 } from 'lucide-react';
import { downsample, selectWindow } from '../lib/data';
import { COLORS } from '../lib/format';
import type { DemoData, Interval } from '../types';

type Props = { data: DemoData; playhead: number; interval: Interval; viewport: Interval; highlighted: string[]; onSelect: (v: Interval) => void; onFollow: () => void; onZoom: (seconds: number) => void };

export default function SignalViewer({ data, playhead, interval, viewport, highlighted, onSelect, onFollow, onZoom }: Props) {
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
    return { channel, values, times, path, low, high, zero: y(0), last: values.at(-1) };
  }), [data, visible, viewport]);
  function pointerTime(event: React.PointerEvent<SVGSVGElement>) {
    const rect = event.currentTarget.getBoundingClientRect();
    return Math.max(viewport.start, Math.min(playhead, viewport.start + (event.clientX - rect.left) / rect.width * (viewport.end - viewport.start)));
  }
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
    <header className="panel-heading"><div><h2>Joint telemetry</h2><span>Signed external torque</span></div><div className="plot-actions"><select aria-label="Signal zoom" defaultValue="10" onChange={e => onZoom(Number(e.target.value))}><option value="1.024">1.024 s</option><option value="5">5 s</option><option value="10">10 s</option><option value="0">Full history</option></select><button className="icon-button" title="Follow playhead" aria-label="Follow playhead" onClick={onFollow}><Crosshair size={16}/></button></div></header>
    <div className="signal-strips">{plots.map((p, index) => <div key={p.channel.id} className={`signal-strip ${highlighted.includes(p.channel.id) ? 'signal-highlighted' : ''}`} data-channel={p.channel.id}>
      <div className="strip-heading"><span className="channel-name"><i style={{ background: COLORS[index] }}/>{p.channel.name}<span className="unit">Nm</span></span><span className="mono sample-value" data-sample-value>{p.last?.toFixed(3) ?? '—'}</span></div>
      <div className="plot-frame"><div className="y-labels"><span>{p.high.toFixed(1)}</span><span>{p.low.toFixed(1)}</span></div><div className="plot-area">
        {right > left && <div className="selection-band" style={{ left: `${left}%`, width: `${right - left}%` }}/>}<div className="hover-guide"/>
        <svg viewBox="0 0 1000 76" preserveAspectRatio="none" role="img" aria-label={`${p.channel.name}, signed external torque in Nm, ${viewport.start.toFixed(3)} to ${end.toFixed(3)} seconds`} onPointerMove={e => showCursor(pointerTime(e))} onPointerLeave={() => showCursor(null)} onPointerDown={e => { if (e.pointerType === 'mouse') { drag.current = pointerTime(e); e.currentTarget.setPointerCapture(e.pointerId); } }} onPointerUp={e => { if (drag.current !== null) { const t = pointerTime(e); const start = Math.min(t, drag.current), finish = Math.max(t, drag.current); if (finish - start >= .01) onSelect({ start, end: finish }); drag.current = null; } }}>
          {[0, 250, 500, 750, 1000].map(x => <line key={x} x1={x} x2={x} y1="0" y2="76" className="chart-grid"/>)}
          {[18, 38, 58].map(y => <line key={y} x1="0" x2="1000" y1={y} y2={y} className="chart-grid"/>)}
          {p.zero > 0 && p.zero < 76 && <line x1="0" x2="1000" y1={p.zero} y2={p.zero} className="chart-zero"/>}
          {data.events.filter(e => e.timeSeconds >= viewport.start && e.timeSeconds <= end).map(e => <line key={e.id} x1={(e.timeSeconds - viewport.start)/(viewport.end-viewport.start)*1000} x2={(e.timeSeconds - viewport.start)/(viewport.end-viewport.start)*1000} y1="0" y2="76" className="event-line"/>)}
          <path d={p.path} fill="none" stroke={COLORS[index]} strokeWidth="1.3" vectorEffect="non-scaling-stroke"/>
        </svg>
      </div></div>
    </div>)}</div>
    <div className="shared-axis">{[0, .25, .5, .75, 1].map(v => <span key={v}>{(viewport.start+(viewport.end-viewport.start)*v).toFixed(2)} s</span>)}</div>
    <footer className="plot-footer"><span data-cursor-time>Hover to inspect · drag to select</span><span><Maximize2 size={12}/>{visible?.resolution === 'raw' ? '1 kHz raw' : '100 Hz display'}</span></footer>
  </section>;
}

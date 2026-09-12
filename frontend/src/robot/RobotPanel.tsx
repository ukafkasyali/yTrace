import { useEffect, useRef, useState } from 'react';
import { ArrowLeft, ArrowRight, Flag, RotateCcw, ZoomIn, ZoomOut } from 'lucide-react';
import { COLORS } from '../lib/format';
import type { DemoData, Interval } from '../types';
import { createRobotScene, validateGeometry, type RobotGeometry } from './scene';
import { positionAtPlayhead, positionFixture, torqueAtPlayhead, validatePositions, type PositionData } from './telemetry';

type Props = { data: DemoData; playhead: number; interval: Interval; highlighted: string[]; onHighlight: (id: string) => void };
export default function RobotPanel({ data, playhead, interval, highlighted, onHighlight }: Props) {
  const host = useRef<HTMLDivElement>(null);
  const scene = useRef<ReturnType<typeof createRobotScene> | null>(null);
  const callback = useRef(onHighlight); callback.current = onHighlight;
  const highlights = useRef(highlighted); highlights.current = highlighted;
  const cursor = useRef(playhead); cursor.current = playhead;
  const geometry = useRef<RobotGeometry | null>(null);
  const [positions, setPositions] = useState<PositionData | null>(null);
  const [positionError, setPositionError] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    const controller = new AbortController(); let alive = true;
    setLoading(true); setError(''); setPositions(null); setPositionError('');
    const positionUrl = positionFixture(data.recording.id);
    const positionRequest = positionUrl
      ? fetch(positionUrl, { signal: controller.signal }).then(async response => {
        if (!response.ok) throw new Error('Measured joint positions could not be loaded.');
        const value: unknown = await response.json(); validatePositions(value, data.recording.id);
        if ((value.endSeconds ?? value.times[value.times.length - 1]) > data.recording.durationSeconds + 1e-8) throw new Error('Position timing does not match this recording.');
        return { value, message: '' };
      }).catch(() => ({ value: null, message: 'Positions unavailable · fixed illustrative pose' }))
      : Promise.resolve({ value: null, message: 'No validated positions for this recording · fixed illustrative pose' });
    const geometryRequest = fetch('/robot/kuka/kinematics.json', { signal: controller.signal }).then(async response => {
      if (!response.ok) throw new Error('Robot reference could not be loaded.');
      const model: unknown = await response.json(); validateGeometry(model); return model;
    });
    Promise.all([geometryRequest, positionRequest]).then(([model, measured]) => {
      if (!alive || !host.current) return;
      geometry.current = model;
      if (measured.value && measured.value.joints.some((joint, index) => joint.values.some(angle => angle < model.joints[index].limitsRadians[0] - 1e-5 || angle > model.joints[index].limitsRadians[1] + 1e-5))) {
        measured = { value: null, message: 'Position mapping failed validation · fixed illustrative pose' };
      }
      const initial = measured.value ? positionAtPlayhead(measured.value, data.recording.id, cursor.current) : null;
      scene.current = createRobotScene(host.current, model, id => callback.current(id), () => {
        setError('The 3D graphics context was lost. Retry to restore it.');
      }, initial?.radians ?? model.illustrativePoseRadians);
      scene.current.highlight(highlights.current);
      setPositions(measured.value); setPositionError(measured.message);
    }).catch(reason => {
      if (alive && !controller.signal.aborted) setError(reason instanceof Error && reason.message.startsWith('Robot') ? reason.message : '3D is unavailable in this browser. The measured signals remain available.');
    }).finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; controller.abort(); scene.current?.dispose(); scene.current = null; geometry.current = null; };
  }, [attempt, data.recording.id, data.recording.durationSeconds]);
  useEffect(() => { scene.current?.highlight(highlighted); }, [highlighted]);
  useEffect(() => {
    const measured = positions ? positionAtPlayhead(positions, data.recording.id, playhead) : null;
    const angles = measured?.radians ?? geometry.current?.illustrativePoseRadians;
    if (angles) scene.current?.pose(angles);
  }, [positions, playhead, data.recording.id]);
  const sample = torqueAtPlayhead(data, playhead);
  const measured = positions ? positionAtPlayhead(positions, data.recording.id, playhead) : null;
  const poseStatus = measured ? `Recorded articulation · ${measured.time.toFixed(3)} s` : loading ? 'Loading joint positions…' : 'Fixed illustrative pose';
  const selectedMarker = data.events.find(event => event.timeSeconds >= interval.start && event.timeSeconds < interval.end);
  return <div className="robot-panel">
    <div className="robot-caption"><strong>KUKA LWR4+ <span>· schematic</span></strong><span>{poseStatus}</span></div>
    <div className="robot-content">
      <div className="robot-stage">
        <div className="robot-canvas" ref={host} role="img" aria-label={measured ? `Seven-joint schematic with recorded articulation at ${measured.time.toFixed(3)} seconds. Body shape and global base orientation are illustrative. Use joint buttons to highlight telemetry.` : 'Seven-joint robot schematic in a fixed illustrative pose. Measured articulation is unavailable at this time. Use joint buttons to highlight telemetry.'}/>
        {selectedMarker && <div className="robot-marker-note"><Flag size={12}/><div><strong>{selectedMarker.label} · {selectedMarker.timeSeconds.toFixed(3)} s</strong><span>Publisher annotation · pose shown only as time context</span></div></div>}
        {loading && <div className="robot-message" role="status">Loading robot reference…</div>}
        {error && <div className="robot-message" role="alert"><p>{error}</p><button className="btn" onClick={() => setAttempt(n => n + 1)}>Retry 3D</button></div>}
        {!loading && !error && <div className="robot-camera" role="group" aria-label="Robot camera controls">
          <button className="icon-button" aria-label="Rotate view left" onClick={() => scene.current?.rotate(-1)}><ArrowLeft size={13}/></button>
          <button className="icon-button" aria-label="Rotate view right" onClick={() => scene.current?.rotate(1)}><ArrowRight size={13}/></button>
          <button className="icon-button" aria-label="Zoom in on robot" onClick={() => scene.current?.zoom(.85)}><ZoomIn size={13}/></button>
          <button className="icon-button" aria-label="Zoom out from robot" onClick={() => scene.current?.zoom(1.18)}><ZoomOut size={13}/></button>
          <button className="icon-button" aria-label="Reset robot camera" onClick={() => scene.current?.reset()}><RotateCcw size={13}/></button>
        </div>}
      </div>
      <div className="robot-joints" aria-label="Measured joint torque at replay cursor">
        <div className="robot-joint-heading"><span>Joint</span><span>Nm</span></div>
        {data.channels.map((channel, i) => <button key={channel.id} aria-pressed={highlighted.includes(channel.id)}
          aria-label={`Highlight ${channel.name} in robot and signals`} onClick={() => onHighlight(channel.id)}>
          <span><i style={{ background: COLORS[i] }}/>J{i + 1}</span><span className="mono">{sample?.channels.find(c => c.id === channel.id)?.value.toFixed(3) ?? '—'}</span>
        </button>)}
        <p className="mono">{sample ? `${sample.time.toFixed(3)} s` : 'No sample'}</p>
      </div>
    </div>
    <footer className="robot-footer" title={positions?.validation.scope}><span>{measured ? positions?.validation.mappingVerified ? 'Recorded angles · schematic frame' : 'Recorded angles · reference mapping; schematic frame' : positionError || 'No position sample at this cursor · fixed pose'}</span><span>{sample?.resolution === 'raw' ? 'Torque · 1 kHz raw' : 'Torque · 100 Hz overview'}</span></footer>
  </div>;
}

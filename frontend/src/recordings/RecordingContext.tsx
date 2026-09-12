import { Component, lazy, Suspense, useState, type ReactNode } from 'react';
import { Box, ChartNoAxesCombined } from 'lucide-react';
import RecordingExplorer from './RecordingExplorer';
import type { DemoData, Interval, Marker } from '../types';
import type { PredictionCue } from '../assistant/predictionBrief';
const RobotPanel = lazy(() => import('../robot/RobotPanel'));

class RobotBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  render() { return this.state.failed ? <p className="robot-boundary" role="alert">3D could not open. Switch to Overview to continue inspecting the recording.</p> : this.props.children; }
}
type Props = { display?: 'overview' | 'robot'; prediction?: PredictionCue; datasetId: string; data: DemoData; playhead: number; interval: Interval; highlighted: string[]; onMarker: (marker: Marker) => void; onHighlight: (id: string) => void; onData: () => void };
export default function RecordingContext(props: Props) {
  const [localView, setView] = useState<'overview' | 'robot'>('overview');
  const view = props.display ?? localView;
  const isKuka = props.datasetId === 'zenodo-21927431';
  return <section className="recording-context" aria-label="Recording context">
    {!props.display && <div className="context-switch" role="group" aria-label="Recording context view">
      <button aria-pressed={view === 'overview'} onClick={() => setView('overview')}><ChartNoAxesCombined size={13}/>Overview</button>
      <button aria-pressed={view === 'robot'} disabled={!isKuka} title={isKuka ? 'Inspect measured articulation when available; body and base frame are schematic' : 'A robot mapping is not available for this recording'} onClick={() => setView('robot')}><Box size={13}/>3D reference</button>
    </div>}
    {view === 'overview' ? <RecordingExplorer {...props}/> : <RobotBoundary><Suspense fallback={<p className="robot-boundary" role="status">Opening 3D reference…</p>}><RobotPanel data={props.data} prediction={props.prediction} playhead={props.playhead} interval={props.interval} highlighted={props.highlighted} onHighlight={props.onHighlight}/></Suspense></RobotBoundary>}
  </section>;
}

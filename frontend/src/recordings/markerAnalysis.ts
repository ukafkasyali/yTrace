import type { DemoData, Interval, Marker } from '../types';

export type AutomaticAnalysisRequest = {
  id: string;
  displayQuestion: string;
  prompt: string;
  interval: Interval;
  playhead: number;
  mode: 'assistant' | 'local';
  source: string;
};

export function markerWindow(marker: Marker, durationSeconds: number): Interval {
  const start = Math.round(Math.max(0, Math.min(marker.timeSeconds - .4, durationSeconds - 1.024)) * 1000) / 1000;
  return { start, end: Math.min(durationSeconds, Math.round((start + 1.024) * 1000) / 1000) };
}

export function createMarkerAnalysis(data: DemoData, marker: Marker, playhead: number, modelRevision?: string): AutomaticAnalysisRequest {
  const interval = markerWindow(marker, data.recording.durationSeconds);
  const hasRawWindow = Math.abs(interval.end - interval.start - 1.024) < 1e-8 && interval.start >= data.detail.startSeconds && interval.end <= data.detail.endSeconds;
  const mode = hasRawWindow ? 'assistant' : 'local';
  return {
    id: `${data.recording.id}:${marker.id}:${mode}:${mode === 'assistant' ? modelRevision ?? 'pending-model' : 'display'}`,
    displayQuestion: `Automatic analysis · ${marker.label}`,
    prompt: hasRawWindow
      ? 'Analyze this robot torque window. Summarize measured temporal changes, contact semantics if supported, the strongest affected joints, and event timing relative to the window. Treat publisher annotations only as navigation metadata; do not assume an exact physical onset.'
      : 'Summarize the measured torque changes around this publisher marker using only available samples.',
    interval,
    playhead: Math.max(playhead, interval.end),
    mode,
    source: hasRawWindow ? 'Automatic marker analysis' : 'Automatic marker summary · reduced resolution',
  };
}

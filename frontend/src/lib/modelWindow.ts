import type { DemoData, Interval } from '../types';
import { selectWindow } from './data';

export function modelWindowIssue(data: DemoData, interval: Interval, playhead: number): string | undefined {
  if (interval.end > playhead) return 'Move the replay cursor to the end of the selection.';
  if (interval.start < data.detail.startSeconds || interval.end > data.detail.endSeconds) return `Raw model input is available only within ${data.detail.startSeconds.toFixed(3)}–${data.detail.endSeconds.toFixed(3)} s.`;
  if (Math.abs(interval.end - interval.start - 1.024) > 1e-8) return 'OpenTSLM needs exactly 1.024 seconds (1,024 samples per joint).';
  try {
    const selected = selectWindow(data, interval);
    if (selected.resolution !== 'raw' || selected.sampleRateHz !== 1000 || selected.times.length !== 1024 ||
      selected.channels.some((c, i) => c.id !== `joint_${i + 1}`) ||
      selected.times.some((t, i) => Math.abs(t - interval.start - i / 1000) > 1e-8)) return 'Select 1,024 contiguous raw samples at 1 kHz.';
  } catch { return 'No raw model input is available for this selection.'; }
}

export function fitModelWindow(data: DemoData, interval: Interval, playhead: number): Interval | undefined {
  const first = data.detail.times[0];
  const lastEnd = Math.min(data.detail.endSeconds, playhead);
  if (first === undefined || lastEnd - first < 1.024 - 1e-8) return;
  const start = Math.round(Math.max(first, Math.min(interval.start, lastEnd - 1.024)) * 1000) / 1000;
  const next = { start, end: Math.round((start + 1.024) * 1000) / 1000 };
  return modelWindowIssue(data, next, playhead) ? undefined : next;
}

export function checkpointLabel(revision?: string): string {
  const sha = revision?.match(/checkpoint-sha256:([a-f0-9]{64})/)?.[1];
  return sha === '8ff63b84ae5b64758f66b3e0527f4f225a04bb2b6b39193c806f58d94cec9f23' ? 'canary-v4 · 8ff63b84' : sha ? `Checkpoint ${sha.slice(0, 8)}` : 'Checkpoint unverified';
}

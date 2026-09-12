import type { DemoData } from '../types';

export type PositionData = {
  recordingId: string; angularUnit: 'radian'; sampleRateHz: number;
  times: number[]; joints: { channelId: string; values: number[] }[];
  validation: { mappingVerified: true; scope: string };
};

export function validatePositions(value: unknown, recordingId: string): asserts value is PositionData {
  const data = value as PositionData;
  if (!data || data.recordingId !== recordingId || data.angularUnit !== 'radian' || data.sampleRateHz !== 100 ||
    data.validation?.mappingVerified !== true || typeof data.validation.scope !== 'string' || !data.validation.scope.trim() ||
    !Array.isArray(data.times) || !data.times.length ||
    data.times.some((t, i) => !Number.isFinite(t) || t < 0 || (i > 0 && (t <= data.times[i - 1] || Math.abs(t - data.times[i - 1] - 1 / data.sampleRateHz) > 1e-6))) ||
    !Array.isArray(data.joints) || data.joints.length !== 7 ||
    data.joints.some((joint, index) => joint?.channelId !== `joint_${index + 1}` || !Array.isArray(joint.values) || joint.values.length !== data.times.length || joint.values.some(angle => !Number.isFinite(angle)))) {
    throw new Error('Measured joint positions could not be validated.');
  }
}

/** Exact recorded articulation, held from the most recent sample; never a future sample. */
export function positionAtPlayhead(data: PositionData, recordingId: string, playhead: number) {
  if (data.recordingId !== recordingId || !Number.isFinite(playhead) || !data.times.length ||
    playhead < data.times[0] || playhead > data.times[data.times.length - 1]) return null;
  let lo = 0, hi = data.times.length;
  while (lo < hi) {
    const mid = (lo + hi) >>> 1;
    if (data.times[mid] <= playhead) lo = mid + 1;
    else hi = mid;
  }
  if (!lo) return null;
  const index = lo - 1;
  return { time: data.times[index], radians: data.joints.map(joint => joint.values[index]) };
}

/** Sample-and-hold: never interpolate using a measurement after the playhead. */
export function torqueAtPlayhead(data: DemoData, playhead: number) {
  if (!Number.isFinite(playhead) || playhead < 0 || playhead > data.recording.durationSeconds) return null;
  const raw = playhead >= data.detail.startSeconds && playhead < data.detail.endSeconds;
  const source = raw ? data.detail : data;
  let lo = 0, hi = source.times.length;
  while (lo < hi) {
    const mid = (lo + hi) >>> 1;
    if (source.times[mid] <= playhead) lo = mid + 1;
    else hi = mid;
  }
  if (!lo) return null;
  return {
    time: source.times[lo - 1],
    resolution: raw ? 'raw' as const : 'display' as const,
    channels: source.channels.map(c => ({ id: c.id, name: c.name, unit: c.unit, value: c.values[lo - 1] })),
  };
}

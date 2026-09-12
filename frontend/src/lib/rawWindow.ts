import type { DemoData, Interval } from '../types';
import type { SignalWindow } from '../services';

/** Validate identity, order, cadence and coverage before enabling model analysis. */
export function withRawWindow(data: DemoData, datasetId: string, interval: Interval, response: SignalWindow): DemoData {
  const ids = Array.from({ length: 7 }, (_, i) => `joint_${i + 1}`);
  const w = response.window;
  if (response.resolution !== 'raw' || w.datasetId !== datasetId || w.recordingId !== data.recording.id ||
      w.startSec !== interval.start || w.endSec !== interval.end || w.channelIds.join(',') !== ids.join(',') ||
      Math.abs(interval.end - interval.start - 1.024) > 1e-8 || response.series.length !== 7) {
    throw new Error('The raw response does not match this recording and selected model window.');
  }
  const channels = response.series.map((s, i) => {
    if (s.channelId !== ids[i] || s.values.length !== 1024 || s.timeSec.length !== 1024 ||
        s.values.some(v => typeof v !== 'number' || !Number.isFinite(v)) ||
        s.timeSec.some((t, j) => !Number.isFinite(t) || Math.abs(t - interval.start - j / 1000) > 1e-8)) {
      throw new Error('The raw response must contain seven ordered joints and 1,024 contiguous finite samples at 1 kHz.');
    }
    return { id: ids[i], name: data.channels[i].name, unit: data.channels[i].unit, values: s.values as number[] };
  });
  if (data.recording.sampleRateHz !== 1000 || channels.some(c => c.unit !== 'Nm')) throw new Error('Raw torque must use Nm at 1 kHz.');
  return { ...data, detail: { startSeconds: interval.start, endSeconds: interval.end, times: response.series[0].timeSec, channels } };
}

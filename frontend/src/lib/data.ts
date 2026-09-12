import type { Analysis, Channel, DemoData, Interval, WindowData } from '../types';

type Point = { x: number; y: number };
const finite = (n: unknown): n is number => typeof n === 'number' && Number.isFinite(n);

function validateSeries(times: number[], channels: Channel[]) {
  if (!Array.isArray(times) || !times.length || times.some((t, i) => !finite(t) || (i > 0 && t <= times[i - 1]))) throw new Error('Signal timestamps must be finite and strictly increasing.');
  if (!Array.isArray(channels) || channels.length !== 7 || new Set(channels.map(c => c.id)).size !== 7) throw new Error('Expected seven uniquely identified channels.');
  for (const c of channels) {
    if (!c.id || !c.name || !c.unit || !Array.isArray(c.values) || c.values.length !== times.length || c.values.some(v => !finite(v))) throw new Error('Channel values must be finite and match the timestamps.');
  }
}

export async function loadDemoData(): Promise<DemoData> {
  const response = await fetch('/data/kuka-demo.json');
  if (!response.ok) throw new Error(`Recording could not be loaded (${response.status}).`);
  const data = await response.json() as DemoData;
  if (!data?.recording || !data.detail || !Array.isArray(data.events)) throw new Error('Invalid recording format.');
  const r = data.recording;
  if (!r.id || r.channelCount !== 7 || !finite(r.durationSeconds) || r.durationSeconds <= 0 || !finite(r.sampleRateHz) || r.sampleRateHz <= 0 || !finite(r.displaySampleRateHz) || r.displaySampleRateHz <= 0) throw new Error('Invalid recording metadata.');
  validateSeries(data.times, data.channels);
  validateSeries(data.detail.times, data.detail.channels);
  const detail = data.detail;
  if (!finite(detail.startSeconds) || !finite(detail.endSeconds) || detail.startSeconds < 0 || detail.endSeconds <= detail.startSeconds || detail.endSeconds > r.durationSeconds || detail.times[0] < detail.startSeconds || detail.times[detail.times.length - 1] > detail.endSeconds || data.times[0] < 0 || data.times[data.times.length - 1] > r.durationSeconds) throw new Error('Signal bounds are inconsistent.');
  if (data.channels.some((c, i) => detail.channels[i].id !== c.id || detail.channels[i].unit !== c.unit)) throw new Error('Detail and overview channels do not match.');
  if (data.events.some(e => !e.id || e.kind !== 'publisher_annotation' || !finite(e.timeSeconds) || e.timeSeconds < 0 || e.timeSeconds > r.durationSeconds)) throw new Error('Invalid publisher event markers.');
  return data;
}

function lowerBound(values: number[], target: number): number {
  let lo = 0, hi = values.length;
  while (lo < hi) {
    const mid = (lo + hi) >>> 1;
    if (values[mid] < target) lo = mid + 1;
    else hi = mid;
  }
  return lo;
}

export function selectWindow(data: DemoData, interval: Interval): WindowData {
  if (!finite(interval.start) || !finite(interval.end) || interval.start < 0 || interval.end <= interval.start || interval.end > data.recording.durationSeconds) throw new Error('Choose a valid interval within this recording.');
  const raw = interval.start >= data.detail.startSeconds && interval.end <= data.detail.endSeconds;
  const source = raw ? data.detail : data;
  const first = lowerBound(source.times, interval.start), last = lowerBound(source.times, interval.end);
  const times = source.times.slice(first, last);
  const channels = source.channels.map(c => ({ ...c, values: c.values.slice(first, last) }));
  validateSeries(times, channels);
  return { times, channels, resolution: raw ? 'raw' : 'display', sampleRateHz: raw ? data.recording.sampleRateHz : data.recording.displaySampleRateHz };
}

function median(values: number[]) {
  const sorted = [...values].sort((a, b) => a - b), mid = Math.floor(sorted.length / 2);
  return sorted.length % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2;
}
function variability(values: number[]) {
  const center = median(values);
  return Math.sqrt(values.reduce((sum, value) => sum + (value - center) ** 2, 0) / values.length);
}
const number = (value: number) => value.toFixed(3);

export function analyzeWindow(data: DemoData, interval: Interval, question: string, playhead = data.recording.durationSeconds): Analysis {
  if (!finite(playhead) || playhead < 0 || playhead > data.recording.durationSeconds || interval.end > playhead) throw new Error('The selected interval extends beyond the replay cursor.');
  const window = selectWindow(data, interval);
  const caution = window.resolution === 'raw'
    ? `Calculated from ${window.sampleRateHz} Hz raw samples in [${number(interval.start)}, ${number(interval.end)}) s.`
    : `Calculated from the ${window.sampleRateHz} Hz display samples in [${number(interval.start)}, ${number(interval.end)}) s. Short peaks may be missing; these are not full-resolution measurements.`;
  const result = (text: string, channels: Channel[], tools: string[]): Analysis => ({ text: `${text}\n\n${caution} This is deterministic signal analysis, not a model prediction.`, evidence: channels.map(c => ({ channelId: c.id, label: c.name, interval: { ...interval } })), tools, resolution: window.resolution });
  if (/\b(why|cause|caused|repair|fix|broken|fault|collision|contact|safe|safety|action|recommend|predict|forecast)\b/i.test(question)) return result('I cannot determine a physical cause, collision, repair, safety decision or future outcome from these calculations. Publisher markers are annotations, not verified physical onset times. Ask about torque ranges, absolute peaks or variability instead.', [], []);
  if (/variab|rms|volatile|variance/i.test(question)) {
    const duration = interval.end - interval.start;
    const firstEnd = lowerBound(window.times, interval.start + duration * .3);
    const lastStart = lowerBound(window.times, interval.end - duration * .3);
    if (firstEnd < 2 || window.times.length - lastStart < 2) return result('This interval has too few samples to compare variability. Select a longer interval.', [], []);
    const ranked = window.channels.map(c => {
      const before = variability(c.values.slice(0, firstEnd)), after = variability(c.values.slice(lastStart));
      return { c, before, after, change: after - before };
    }).sort((a, b) => b.change - a.change);
    return result(`Comparing the first 30% (${number(interval.start)}–${number(interval.start + duration * .3)} s) with the last 30% (${number(interval.end - duration * .3)}–${number(interval.end)} s), the largest signed changes in RMS deviation about each interval's median are: ${ranked.slice(0, 2).map(r => `${r.c.name}: ${number(r.before)} → ${number(r.after)} ${r.c.unit} (${r.change >= 0 ? '+' : ''}${number(r.change)})`).join('; ')}.`, ranked.slice(0, 2).map(r => r.c), ['Calculate interval medians', 'Compare RMS variability']);
  }
  if (/peak|absolute|max.*torque/i.test(question)) {
    let channel = window.channels[0], index = 0, peak = -Infinity;
    for (const c of window.channels) c.values.forEach((value, i) => { if (Math.abs(value) > peak) { peak = Math.abs(value); channel = c; index = i; } });
    return result(`The largest absolute sampled torque is on ${channel.name}: ${number(peak)} ${channel.unit} at ${number(window.times[index])} s (signed value ${number(channel.values[index])} ${channel.unit}).`, [channel], ['Find absolute sampled peak']);
  }
  const ranked = window.channels.map(c => {
    let min = Infinity, max = -Infinity;
    for (const v of c.values) { min = Math.min(min, v); max = Math.max(max, v); }
    return { c, min, max, range: max - min };
  }).sort((a, b) => b.range - a.range);
  return result(`Using sampled torque range as the measure of change, the largest ranges are ${ranked.slice(0, 2).map(r => `${r.c.name}: ${number(r.range)} ${r.c.unit} (${number(r.min)} to ${number(r.max)})`).join('; ')}. This describes signal changes and does not establish their physical cause.`, ranked.slice(0, 2).map(r => r.c), ['Calculate channel minima and maxima', 'Rank sampled torque ranges']);
}

export function downsample(points: Point[], maxPoints: number): Point[] {
  if (!Number.isInteger(maxPoints) || maxPoints < 2) throw new Error('Point budget must be an integer of at least two.');
  if (points.some(p => !finite(p.x) || !finite(p.y))) throw new Error('Chart points must be finite.');
  if (points.length <= maxPoints) return points.slice();
  if (maxPoints === 2) return [points[0], points[points.length - 1]];
  if (maxPoints === 3) {
    let selected = 1;
    for (let i = 2; i < points.length - 1; i++) if (Math.abs(points[i].y) > Math.abs(points[selected].y)) selected = i;
    return [points[0], points[selected], points[points.length - 1]];
  }
  const buckets = Math.floor((maxPoints - 2) / 2), result: Point[] = [points[0]];
  for (let bucket = 0; bucket < buckets; bucket++) {
    const start = 1 + Math.floor(bucket * (points.length - 2) / buckets);
    const end = 1 + Math.floor((bucket + 1) * (points.length - 2) / buckets);
    let min = start, max = start;
    for (let i = start + 1; i < end; i++) { if (points[i].y < points[min].y) min = i; if (points[i].y > points[max].y) max = i; }
    for (const i of [...new Set([min, max])].sort((a, b) => a - b)) result.push(points[i]);
  }
  result.push(points[points.length - 1]);
  return result;
}

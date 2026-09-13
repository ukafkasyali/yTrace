import type { DemoCase, DemoData, Interval } from '../types';

/** Preserve the signed value and original timestamp of the largest absolute sample. */
export function sampledPeak(times: number[], values: number[]) {
  let index = 0;
  for (let i = 1; i < values.length; i++) if (Math.abs(values[i]) > Math.abs(values[index])) index = i;
  return { time: times[index], value: values[index] };
}

export function suggestReference(data: DemoData, selected: Interval): Interval | undefined {
  const length = selected.end - selected.start;
  if (!Number.isFinite(length) || length < .002) return;
  // Nearest earlier nonoverlapping window, with 0.5 s separation and annotation margin.
  // This is a navigation suggestion, never a normality classifier.
  for (const minimum of [data.detail.startSeconds, 0]) {
    let attempts = 0;
    for (let end = Math.round((selected.start - .5) * 1000) / 1000; end - length >= minimum - 1e-8 && attempts++ < 500; end = Math.round((end - length) * 1000) / 1000) {
      const start = Math.round((end - length) * 1000) / 1000;
      if (start < .01) continue;
      if (!data.events.some(e => e.timeSeconds >= start - .5 && e.timeSeconds <= end + .5)) return { start, end };
    }
  }
}

function rawCovers(data: DemoData, w: Interval) { return w.start >= data.detail.startSeconds && w.end <= data.detail.endSeconds; }
function samples(data: DemoData, w: Interval, raw: boolean) {
  if (!Number.isFinite(w.start) || !Number.isFinite(w.end) || w.start < 0 || w.end <= w.start || w.end > data.recording.durationSeconds) throw new Error('Choose a window within the recording.');
  const source = raw ? data.detail : data;
  const rate = raw ? data.recording.sampleRateHz : data.recording.displaySampleRateHz;
  const indices = source.times.flatMap((t, i) => t >= w.start - 1e-9 && t < w.end - 1e-9 ? [i] : []);
  const times = indices.map(i => source.times[i]);
  if (!Number.isFinite(rate) || rate <= 0 || times.length < 2 || times[0] - w.start > 1 / rate + 1e-7 || w.end - times.at(-1)! > 1 / rate + 1e-7 || times.some((t, i) => !Number.isFinite(t) || (i > 0 && Math.abs(t - times[i - 1] - 1 / rate) > 1e-7))) throw new Error('This window has missing samples or insufficient coverage. Choose another window.');
  const channels = source.channels.map(c => ({ id: c.id, name: c.name, unit: c.unit, values: indices.map(i => c.values[i]) }));
  if (channels.length !== 7 || channels.some((c, i) => c.id !== `joint_${i + 1}` || c.unit !== 'Nm' || c.values.some(v => !Number.isFinite(v)))) throw new Error('Comparison requires seven synchronized joints in Nm.');
  return { times, channels, rate };
}
function metrics(values: number[]) {
  let mean = 0, m2 = 0, min = Infinity, max = -Infinity;
  values.forEach((v, i) => { const delta = v - mean; mean += delta / (i + 1); m2 += delta * (v - mean); min = Math.min(min, v); max = Math.max(max, v); });
  return { range: max - min, variability: Math.sqrt(Math.max(0, m2 / values.length)), mean };
}

function incidentSignature(data: DemoData, interval: Interval) {
  if (!rawCovers(data, interval)) throw new Error('Incident matching requires raw telemetry for the complete window.');
  if (data.recording.sampleRateHz !== 1000) throw new Error('Incident matching requires raw telemetry sampled at 1 kHz.');
  const sample = samples(data, interval, true);
  const features = sample.channels.flatMap(channel => {
    const summary = metrics(channel.values);
    let largestStep = 0;
    for (let index = 1; index < channel.values.length; index++) {
      largestStep = Math.max(largestStep, Math.abs(channel.values[index] - channel.values[index - 1]));
    }
    return [summary.range, summary.variability, largestStep];
  });
  const strongest = sample.channels
    .map(channel => ({ id: channel.id, range: metrics(channel.values).range }))
    .sort((left, right) => right.range - left.range)[0]?.id;
  return { features, sampleRateHz: sample.rate, samplesPerChannel: sample.times.length, strongestJoint: strongest };
}

function symmetricDifference(left: number, right: number) {
  return Math.abs(left - right) / Math.max(Math.abs(left) + Math.abs(right), 1e-9);
}

export type IncidentCandidate = { item: DemoCase; data: DemoData };
export type SimilarIncident = {
  caseId: string;
  title: string;
  recordingId: string;
  interval: Interval;
  score: number;
  strongestJoint?: string;
  samplesPerChannel: number;
  sourceUrl: string;
  archive: string;
};

/**
 * Rank fixed, raw incident windows by a transparent torque-profile distance.
 * This is retrieval for review and cohorting, never a diagnosis or probability.
 */
export function rankSimilarIncidents(selectedData: DemoData, selected: Interval, candidates: IncidentCandidate[]): SimilarIncident[] {
  const selectedSignature = incidentSignature(selectedData, selected);
  const selectedDuration = selected.end - selected.start;
  const ranked: SimilarIncident[] = [];
  for (const candidate of candidates) {
    const interval = candidate.item.interval;
    if (candidate.data.recording.id === selectedData.recording.id) continue;
    if (Math.abs((interval.end - interval.start) - selectedDuration) > 1e-7) continue;
    try {
      const signature = incidentSignature(candidate.data, interval);
      if (signature.sampleRateHz !== selectedSignature.sampleRateHz
        || signature.samplesPerChannel !== selectedSignature.samplesPerChannel) continue;
      const distance = signature.features.reduce(
        (total, value, index) => total + symmetricDifference(value, selectedSignature.features[index]), 0,
      ) / signature.features.length;
      ranked.push({
        caseId: candidate.item.id,
        title: candidate.item.title,
        recordingId: candidate.data.recording.id,
        interval: { ...interval },
        score: Math.max(0, Math.min(1, 1 - distance)),
        strongestJoint: signature.strongestJoint,
        samplesPerChannel: signature.samplesPerChannel,
        sourceUrl: candidate.data.recording.sourceUrl,
        archive: candidate.data.recording.archive,
      });
    } catch {
      // A candidate without complete raw evidence is not eligible for the cohort.
    }
  }
  return ranked.sort((left, right) => right.score - left.score || left.recordingId.localeCompare(right.recordingId));
}
export function compareWindows(selectedData: DemoData, selected: Interval, referenceData: DemoData, reference: Interval) {
  if (Math.abs((selected.end - selected.start) - (reference.end - reference.start)) > 1e-7) throw new Error('Use equal-duration windows for this comparison.');
  if (selectedData.recording.id === referenceData.recording.id && selected.start < reference.end && reference.start < selected.end) throw new Error('Choose a reference that does not overlap the selected incident.');
  const raw = rawCovers(selectedData, selected) && rawCovers(referenceData, reference);
  const a = samples(selectedData, selected, raw), b = samples(referenceData, reference, raw);
  if (a.rate !== b.rate) throw new Error('These recordings have different sample rates. Comparison does not resample signals.');
  const joints = a.channels.map((c, i) => {
    const current = metrics(c.values), baseline = metrics(b.channels[i].values);
    return { id: c.id, name: c.name, selected: current, reference: baseline,
      rangeDelta: current.range - baseline.range, variabilityDelta: current.variability - baseline.variability,
      meanDelta: current.mean - baseline.mean };
  }).sort((x, y) => Math.abs(y.rangeDelta) - Math.abs(x.rangeDelta));
  const descriptor = (data: DemoData, w: Interval, count: number) => ({ recordingId: data.recording.id, sourceUrl: data.recording.sourceUrl, interval: { ...w }, samplesPerChannel: count,
    publisherAnnotations: data.events.filter(e => e.timeSeconds >= w.start && e.timeSeconds < w.end) });
  return { schemaVersion: 1, kind: 'trace_window_comparison', origin: 'deterministic_calculation',
    selected: descriptor(selectedData, selected, a.times.length), reference: descriptor(referenceData, reference, b.times.length),
    resolution: raw ? 'raw' : 'display', sampleRateHz: a.rate, joints,
    method: 'Equal-duration half-open windows; both raw only when both have raw coverage, otherwise both overview. Range=max-min; variability=population standard deviation; deltas=selected-reference. No resampling.',
    limitations: ['Reference normality and matching motion phase, payload and operating conditions are not verified.', 'Lower torque variation alone does not prove a successful repair, safety or fewer collisions.', ...(raw ? [] : ['Both windows use overview samples; short peaks may be missing.'])],
    plot: { selected: a, reference: b } };
}
export type WindowComparison = ReturnType<typeof compareWindows>;
export function comparisonReport(result: WindowComparison) {
  const { plot: _, ...report } = result;
  return { ...report, nextCheck: `Inspect ${result.joints[0].name} in both windows and check matching motion phase, payload and commanded contact.`,
    verification: 'Repeat under matched operating conditions after an engineer-selected change. Compare the same metrics; record the intervention separately. This comparison does not establish causality.' };
}

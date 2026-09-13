import { selectWindow } from '../lib/data';
import type { DemoData, EvidenceLink, Interval } from '../types';
import { structuredPrediction } from './predictionBrief';
import { reviewPrediction } from './predictionReview';

export type InvestigationAnswer = {
  question: string; interval: Interval; playhead: number; replayCursor?: number; text: string;
  mode: 'local' | 'assistant'; source: string; evidence: EvidenceLink[];
  modelId?: string; modelRevision?: string; inputTrace?: unknown; modelOutput?: string;
};

function inputReceiptIssue(receipt: unknown, recordingId: string, interval: Interval): string | undefined {
  if (!receipt || typeof receipt !== 'object') return 'The service did not provide an input receipt.';
  const value = receipt as {
    samplesPerChannel?: unknown;
    inputSha256?: unknown;
    window?: { channelIds?: unknown; recordingId?: unknown; startSec?: unknown; endSec?: unknown };
  };
  const expectedChannels = Array.from({ length: 7 }, (_, index) => `joint_${index + 1}`);
  const channelIds = value.window?.channelIds;
  if (value.samplesPerChannel !== 1024) return 'The input receipt does not confirm 1,024 samples per channel.';
  if (!Array.isArray(channelIds) || channelIds.join(',') !== expectedChannels.join(',')) {
    return 'The input receipt does not confirm canonical joint_1 through joint_7 channel order.';
  }
  if (
    value.window?.recordingId !== recordingId
    || value.window.startSec !== interval.start
    || value.window.endSec !== interval.end
  ) {
    return 'The input receipt does not match this recording and selected interval.';
  }
  if (typeof value.inputSha256 !== 'string' || !/^[a-f0-9]{64}$/i.test(value.inputSha256)) {
    return 'The input receipt does not include a valid input hash.';
  }
  return undefined;
}

function scopedInterpretation(text: string, mode: InvestigationAnswer['mode']): string {
  if (mode !== 'assistant') return text;
  const generatedBlock = text.match(/(?:^|\n)\s*OpenTSLM interpretation\s*\n([\s\S]*)$/i);
  return generatedBlock?.[1].trim() || text;
}

export function buildInvestigationReport(data: DemoData, datasetId: string, answer: InvestigationAnswer) {
  if (answer.interval.end > answer.playhead) throw new Error('The investigation extends beyond its replay cursor.');
  const window = selectWindow(data, answer.interval);
  const measurements = window.channels.map(channel => {
    let minimum = Infinity, maximum = -Infinity, peakIndex = 0;
    channel.values.forEach((value, index) => {
      minimum = Math.min(minimum, value); maximum = Math.max(maximum, value);
      if (Math.abs(value) > Math.abs(channel.values[peakIndex])) peakIndex = index;
    });
    return { channelId: channel.id, name: channel.name, unit: channel.unit, minimum, maximum,
      range: maximum - minimum, absolutePeak: Math.abs(channel.values[peakIndex]),
      signedPeak: channel.values[peakIndex], peakTimeSec: window.times[peakIndex] };
  });
  const receiptIssue = answer.mode === 'assistant'
    ? inputReceiptIssue(answer.inputTrace, data.recording.id, answer.interval)
    : undefined;
  const predictionReview = answer.mode === 'assistant' ? reviewPrediction(data, answer.interval, answer.modelOutput) : undefined;
  return {
    schemaVersion: 1, kind: 'trace_retrospective_investigation',
    recording: { datasetId, recordingId: data.recording.id, sourceUrl: data.recording.sourceUrl },
    question: answer.question,
    window: { startSec: answer.interval.start, endSec: answer.interval.end,
      convention: 'half-open [start, end)', playheadSec: answer.playhead,
      analysisHorizonSec: answer.playhead, replayCursorSec: answer.replayCursor ?? answer.playhead,
      resolution: window.resolution, sampleRateHz: window.sampleRateHz, samplesPerChannel: window.times.length },
    publisherAnnotations: data.events.filter(e => e.timeSeconds >= answer.interval.start && e.timeSeconds < answer.interval.end),
    measurements: { origin: 'deterministic_calculation', method: 'sampled min, max, range and absolute peak', channels: measurements },
    interpretation: { origin: answer.mode === 'local' ? 'deterministic_calculation' : 'generated_prediction',
      answer: scopedInterpretation(answer.text, answer.mode), source: answer.source, modelId: answer.modelId ?? null,
      modelRevision: answer.modelRevision ?? null, rawModelOutput: answer.modelOutput ?? null,
      rawModelOutputTrust: answer.modelOutput ? 'unverified_generated_text_not_annotation_or_measurement' : null,
      structuredPrediction: answer.mode === 'assistant' ? structuredPrediction(answer.modelOutput) ?? null : null,
      inputReceipt: answer.inputTrace ?? null, evidence: answer.evidence },
    reviewNotes: predictionReview ? [predictionReview.note] : [],
    limitations: [
      'Recorded contact-event triage; no verified root cause, safety decision or repair recommendation.',
      'Publisher annotations, measured quantities and generated predictions are distinct evidence sources.',
      'Strong torque response does not identify the physical contact location.',
      ...(window.resolution === 'display' ? ['Measurements use reduced display samples; short peaks may be missing.'] : []),
      ...(answer.mode === 'assistant' && !answer.modelRevision ? ['The service did not provide a model revision.'] : []),
      ...(receiptIssue ? [receiptIssue] : []),
      ...(answer.mode === 'assistant' && answer.modelOutput ? ['Raw model output is preserved for audit and may disagree with publisher annotations.'] : []),
    ],
  };
}

type InvestigationReport = ReturnType<typeof buildInvestigationReport>;

function markdownText(value: unknown): string {
  return String(value ?? '')
    .replace(/\\/g, '\\\\')
    .replace(/([`*_[\]<>|])/g, '\\$1')
    .replace(/\s+/g, ' ')
    .trim();
}

function markdownLink(url: string): string {
  return `<${url.replace(/</g, '%3C').replace(/>/g, '%3E')}>`;
}

function fixed(value: number, digits = 3): string {
  return Number.isFinite(value) ? value.toFixed(digits) : 'unavailable';
}

function generatedPredictionMarkdown(report: InvestigationReport): string | undefined {
  const prediction = report.interpretation.structuredPrediction;
  if (!prediction) return;
  return [
    `- **Contact:** ${prediction.contact ? 'yes' : 'no'}`,
    `- **Event type:** ${markdownText(prediction.event_type)}`,
    `- **Onset:** ${prediction.onset_ms === null ? 'not applicable' : `${prediction.onset_ms} ms after window start`}`,
    `- **Strongest joint:** ${markdownText(prediction.strongest_joint ?? 'not applicable')}`,
    `- **Affected joints:** ${prediction.affected_joints.length ? prediction.affected_joints.map(markdownText).join(', ') : 'none'}`,
    `- **Evidence interval:** ${prediction.evidence_start_ms === null || prediction.evidence_end_ms === null
      ? 'not applicable'
      : `${prediction.evidence_start_ms}–${prediction.evidence_end_ms} ms after window start`}`,
  ].join('\n');
}

export function renderInvestigationMarkdown(report: InvestigationReport): string {
  const annotations = report.publisherAnnotations.length
    ? report.publisherAnnotations.map(annotation =>
      `- ${fixed(annotation.timeSeconds)} s — ${markdownText(annotation.label)} (${markdownText(annotation.source)})`).join('\n')
    : '- No publisher annotation falls inside this interval.';
  const measurements = report.measurements.channels.map(channel =>
    `| ${markdownText(channel.name)} | ${fixed(channel.range)} ${markdownText(channel.unit)} | ${fixed(channel.signedPeak)} ${markdownText(channel.unit)} | ${fixed(channel.peakTimeSec)} s |`).join('\n');
  const evidence = report.interpretation.evidence.length
    ? report.interpretation.evidence.map(item =>
      `- ${markdownText(item.label)} — ${fixed(item.interval.start)}–${fixed(item.interval.end)} s; ${item.channelIds?.map(markdownText).join(', ') ?? markdownText(item.channelId)}`).join('\n')
    : '- No separate evidence links were returned.';
  const receipt = report.interpretation.inputReceipt && typeof report.interpretation.inputReceipt === 'object'
    ? report.interpretation.inputReceipt as { samplesPerChannel?: unknown; inputSha256?: unknown }
    : undefined;
  const answer = report.interpretation.origin === 'generated_prediction'
    ? generatedPredictionMarkdown(report) ?? 'No valid structured prediction was returned.'
    : report.interpretation.answer.split(/\n\s*\n/).map(markdownText).filter(Boolean).join('\n\n');
  const reviewNotes = report.reviewNotes.length ? report.reviewNotes.map(item => `- ${markdownText(item)}`) : ['- No model/measurement ranking difference was flagged by the current review checks.'];
  return [
    '# Trace incident investigation',
    '',
    '> Retrospective robot telemetry report. Measurements, publisher annotations, and generated predictions are recorded as separate evidence sources.',
    '',
    '## Incident',
    '',
    `- **Recording:** ${markdownText(report.recording.recordingId)}`,
    `- **Source dataset:** ${markdownLink(report.recording.sourceUrl)}`,
    `- **Selected window:** [${fixed(report.window.startSec)}, ${fixed(report.window.endSec)}) s`,
    `- **Question:** ${markdownText(report.question)}`,
    '',
    '## Interpretation',
    '',
    `**Origin:** ${markdownText(report.interpretation.origin)} · **Source:** ${markdownText(report.interpretation.source)}`,
    '',
    answer || 'No interpretation was returned.',
    '',
    '## Deterministic cross-check before handoff',
    '',
    ...reviewNotes,
    '',
    '## Measured torque',
    '',
    `${markdownText(report.measurements.method)}.`,
    '',
    '| Joint | Range | Signed absolute peak | Peak time |',
    '| --- | ---: | ---: | ---: |',
    measurements,
    '',
    '## Publisher annotations',
    '',
    annotations,
    '',
    '## Evidence and provenance',
    '',
    `- **Input:** ${report.window.samplesPerChannel} samples per channel at ${report.window.sampleRateHz} Hz (${markdownText(report.window.resolution)})`,
    `- **Model:** ${markdownText(report.interpretation.modelId ?? 'not applicable')}`,
    `- **Revision:** ${markdownText(report.interpretation.modelRevision ?? 'not provided')}`,
    `- **Input receipt:** ${markdownText(receipt?.samplesPerChannel ?? 'not provided')} samples/channel; SHA-256 ${markdownText(receipt?.inputSha256 ?? 'not provided')}`,
    '',
    evidence,
    '',
    '## Limitations',
    '',
    ...report.limitations.map(item => `- ${markdownText(item)}`),
    '',
  ].join('\n');
}

function download(content: string, type: string, filename: string): void {
  const url = URL.createObjectURL(new Blob([content], { type }));
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  document.body.appendChild(link); link.click(); link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function reportFilename(data: DemoData, answer: InvestigationAnswer, extension: string): string {
  return `trace-${data.recording.id.replace(/[^a-zA-Z0-9_-]/g, '_')}-${answer.interval.start.toFixed(3)}s.${extension}`;
}

export function downloadInvestigationMarkdown(data: DemoData, datasetId: string, answer: InvestigationAnswer) {
  const report = buildInvestigationReport(data, datasetId, answer);
  download(renderInvestigationMarkdown(report), 'text/markdown;charset=utf-8', reportFilename(data, answer, 'md'));
}

export function downloadInvestigationJson(data: DemoData, datasetId: string, answer: InvestigationAnswer) {
  const report = buildInvestigationReport(data, datasetId, answer);
  download(JSON.stringify(report, null, 2) + '\n', 'application/json', reportFilename(data, answer, 'json'));
}

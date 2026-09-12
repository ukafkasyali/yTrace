import { selectWindow } from '../lib/data';
import type { DemoData, EvidenceLink, Interval } from '../types';

export type InvestigationAnswer = {
  question: string; interval: Interval; playhead: number; text: string;
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
  return {
    schemaVersion: 1, kind: 'trace_retrospective_investigation',
    recording: { datasetId, recordingId: data.recording.id, sourceUrl: data.recording.sourceUrl },
    question: answer.question,
    window: { startSec: answer.interval.start, endSec: answer.interval.end,
      convention: 'half-open [start, end)', playheadSec: answer.playhead,
      resolution: window.resolution, sampleRateHz: window.sampleRateHz, samplesPerChannel: window.times.length },
    publisherAnnotations: data.events.filter(e => e.timeSeconds >= answer.interval.start && e.timeSeconds < answer.interval.end),
    measurements: { origin: 'deterministic_calculation', method: 'sampled min, max, range and absolute peak', channels: measurements },
    interpretation: { origin: answer.mode === 'local' ? 'deterministic_calculation' : 'generated_prediction',
      answer: answer.text, source: answer.source, modelId: answer.modelId ?? null,
      modelRevision: answer.modelRevision ?? null, rawModelOutput: answer.modelOutput ?? null,
      inputReceipt: answer.inputTrace ?? null, evidence: answer.evidence },
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

export function downloadInvestigationReport(data: DemoData, datasetId: string, answer: InvestigationAnswer) {
  const report = buildInvestigationReport(data, datasetId, answer);
  const url = URL.createObjectURL(new Blob([JSON.stringify(report, null, 2) + '\n'], { type: 'application/json' }));
  const link = document.createElement('a');
  link.href = url;
  link.download = `trace-${data.recording.id.replace(/[^a-zA-Z0-9_-]/g, '_')}-${answer.interval.start.toFixed(3)}s.json`;
  document.body.appendChild(link); link.click(); link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

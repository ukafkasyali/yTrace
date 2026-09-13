import { describe, expect, it } from 'vitest';
import { buildInvestigationReport, renderInvestigationMarkdown, type InvestigationAnswer } from './investigationReport';
import type { DemoData } from '../types';

const channels = Array.from({ length: 7 }, (_, i) => ({ id: `joint_${i + 1}`, name: `Joint ${i + 1}`, unit: 'Nm', values: [1, -3, 2] }));
const data: DemoData = {
  recording: { id: 'recording', name: 'Recording', durationSeconds: 3, sampleRateHz: 1000,
    displaySampleRateHz: 1, sourceUrl: 'https://example.com/data', archive: 'fixture', channelCount: 7, eventCount: 2 },
  times: [0, 1, 2], channels,
  detail: { startSeconds: 1, endSeconds: 1.003, times: [1, 1.001, 1.002], channels },
  events: [{ id: 'inside', timeSeconds: 1.001, kind: 'publisher_annotation', label: 'Marker', source: 'publisher' },
    { id: 'end', timeSeconds: 1.003, kind: 'publisher_annotation', label: 'Next', source: 'publisher' }],
};
const answer: InvestigationAnswer = { question: 'What changed?', interval: { start: 1, end: 1.003 },
  playhead: 2, text: 'A generated interpretation', mode: 'assistant', source: 'OpenTSLM', evidence: [],
  modelId: 'checkpoint', modelRevision: 'sha256:verified', inputTrace: { samplesPerChannel: 3 } };

describe('investigation export', () => {
  it('separates annotations, computed values and frozen model provenance', () => {
    const report = buildInvestigationReport(data, 'dataset', answer);
    expect(report.publisherAnnotations.map(e => e.id)).toEqual(['inside']);
    expect(report.measurements.channels[0]).toMatchObject({ range: 5, absolutePeak: 3, signedPeak: -3, peakTimeSec: 1.001 });
    expect(report.interpretation.origin).toBe('generated_prediction');
    expect(report.interpretation.modelRevision).toBe('sha256:verified');
    expect(report.window.samplesPerChannel).toBe(3);
    expect(report.window.startSec).toBe(answer.interval.start);
  });
  it('labels reduced display measurements and local calculations honestly', () => {
    const report = buildInvestigationReport(data, 'dataset', { ...answer, mode: 'local', interval: { start: 0, end: 2 } });
    expect(report.window.resolution).toBe('display');
    expect(report.interpretation.origin).toBe('deterministic_calculation');
    expect(report.limitations.join(' ')).toContain('short peaks may be missing');
  });
  it('keeps the incident-start cursor separate from the completed analysis window', () => {
    const report = buildInvestigationReport(data, 'dataset', { ...answer, replayCursor: 1 });
    expect(report.window.replayCursorSec).toBe(1);
    expect(report.window.analysisHorizonSec).toBe(2);
    expect(report.window.samplesPerChannel).toBe(3);
  });
  it('rejects measurements beyond the snapshotted replay cursor', () => {
    expect(() => buildInvestigationReport(data, 'dataset', { ...answer, playhead: 1 })).toThrow('replay cursor');
  });
  it('marks an assistant export incomplete when model input provenance is missing', () => {
    const report = buildInvestigationReport(data, 'dataset', { ...answer, inputTrace: undefined });
    expect(report.limitations.join(' ')).toContain('did not provide an input receipt');
  });
  it('requires canonical evidence before treating an assistant receipt as complete', () => {
    const receipt = {
      samplesPerChannel: 1024,
      inputSha256: 'a'.repeat(64),
      window: {
        recordingId: data.recording.id,
        startSec: answer.interval.start,
        endSec: answer.interval.end,
        channelIds: Array.from({ length: 7 }, (_, index) => `joint_${index + 1}`),
      },
    };
    const report = buildInvestigationReport(data, 'dataset', { ...answer, inputTrace: receipt, modelOutput: 'Answer: {}' });
    expect(report.limitations.join(' ')).not.toContain('input receipt does not');
    expect(report.limitations.join(' ')).toContain('Raw model output is preserved');
    expect(report.interpretation.rawModelOutputTrust).toBe('unverified_generated_text_not_annotation_or_measurement');
  });
  it('flags a receipt from a different investigation interval', () => {
    const report = buildInvestigationReport(data, 'dataset', {
      ...answer,
      inputTrace: {
        samplesPerChannel: 1024, inputSha256: 'a'.repeat(64),
        window: { recordingId: data.recording.id, startSec: 0, endSec: 1.024, channelIds: Array.from({ length: 7 }, (_, index) => `joint_${index + 1}`) },
      },
    });
    expect(report.limitations.join(' ')).toContain('does not match this recording and selected interval');
  });
  it('renders a readable Markdown handoff without collapsing evidence sources', () => {
    const report = buildInvestigationReport(data, 'dataset', {
      ...answer,
      modelOutput: 'Answer: {"contact":true,"event_type":"accidental","onset_ms":42,"strongest_joint":"J2","affected_joints":["J2"],"evidence_start_ms":42,"evidence_end_ms":200}',
    });
    const markdown = renderInvestigationMarkdown(report);
    expect(markdown).toContain('# Trace incident investigation');
    expect(markdown).toContain('## Interpretation');
    expect(markdown).toContain('## Deterministic cross-check before handoff');
    expect(markdown).toContain('- **Event type:** accidental');
    expect(markdown).toContain('- **Strongest joint:** J2');
    expect(markdown).toContain('## Measured torque');
    expect(markdown).toContain('| Joint 1 | 5.000 Nm | -3.000 Nm | 1.001 s |');
    expect(markdown).toContain('## Publisher annotations');
    expect(markdown).toContain('Marker');
    expect(markdown).toContain('## Evidence and provenance');
    expect(markdown).toContain('## Limitations');
  });
  it('keeps unverified raw model prose out of the readable handoff', () => {
    const report = buildInvestigationReport(data, 'dataset', {
      ...answer,
      text: 'Safe structured interpretation.',
      modelOutput: 'Unverified explanation about a manual event marker.\nAnswer: {"contact":true}',
    });
    const markdown = renderInvestigationMarkdown(report);
    expect(markdown).toContain('No valid structured prediction was returned.');
    expect(markdown).not.toContain('manual event marker');
  });
  it('keeps the server measurement summary out of the generated interpretation section', () => {
    const report = buildInvestigationReport(data, 'dataset', {
      ...answer,
      text: 'Measured in this selected window\nLargest observed torque ranges: Joint 2.\n\nOpenTSLM interpretation\nOpenTSLM predicts external contact.',
      modelOutput: 'Answer: {"contact":true,"event_type":"intentional","onset_ms":42,"strongest_joint":"J2","affected_joints":["J2"],"evidence_start_ms":42,"evidence_end_ms":200}',
    });
    const markdown = renderInvestigationMarkdown(report);
    expect(report.interpretation.answer).toBe('OpenTSLM predicts external contact.');
    expect(markdown).toContain('- **Event type:** intentional');
    expect(markdown).not.toContain('OpenTSLM predicts external contact.');
    expect(markdown).not.toContain('Largest observed torque ranges: Joint 2.');
    expect(markdown).toContain('| Joint 2 | 5.000 Nm | -3.000 Nm | 1.001 s |');
  });
});

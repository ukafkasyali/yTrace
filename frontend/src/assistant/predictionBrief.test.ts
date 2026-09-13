import { describe, expect, it } from 'vitest';
import { predictionBrief, predictionCue, structuredPrediction } from './predictionBrief';

const contact = (overrides = '') => `{"contact":true,"event_type":"accidental","onset_ms":298,"strongest_joint":"J4","affected_joints":["J4"],"evidence_start_ms":298,"evidence_end_ms":548${overrides}}`;
describe('compact generated interpretation', () => {
  it('places generated onset in recording time without inventing a joint or a free-motion impact', () => {
    const interval = { start: 5.641, end: 6.665 };
    const raw = contact();
    const cue = predictionCue(raw, interval)!;
    expect(cue.onsetSeconds).toBeCloseTo(5.939);
    expect(cue.channelId).toBe('joint_4');
    interval.start = 10;
    expect(cue.interval.start).toBe(5.641);
    expect(predictionCue(raw, interval)).toBeUndefined();
    expect(predictionCue('{"contact":false,"event_type":"free","onset_ms":100,"strongest_joint":null,"affected_joints":[],"evidence_start_ms":null,"evidence_end_ms":null}', {start: 0, end: 1.024})).toBeUndefined();
    expect(predictionCue(contact(',"onset_ms":1024'), {start: 0, end: 1.024})).toBeUndefined();
    expect(predictionCue(contact(',"strongest_joint":"J8"'), {start: 0, end: 1.024})).toBeUndefined();
  });
  it('shows only supported predictions and preserves the distinction from facts', () => {
    expect(predictionBrief('Answer: {"contact":true,"event_type":"intentional","strongest_joint":"J4","onset_ms":312,"affected_joints":["J4"],"evidence_start_ms":312,"evidence_end_ms":400}\nEvidence: signal.')).toEqual({ title: 'Intentional contact predicted', strongest: 'J4', onset: 312 });
    expect(predictionBrief('Answer: {"contact":false,"event_type":"free","strongest_joint":null,"onset_ms":null,"affected_joints":[],"evidence_start_ms":null,"evidence_end_ms":null}')?.title).toBe('Free motion predicted');
  });
  it('falls back rather than disguising missing or inconsistent model fields', () => {
    for (const raw of [undefined, '', 'malformed', '{"contact":null,"event_type":"free"}', '{"contact":false,"event_type":"accidental"}', '{"contact":true,"event_type":"unknown"}']) expect(predictionBrief(raw)).toBeUndefined();
    expect(predictionBrief('{"contact":true,"event_type":"accidental","onset_ms":2048,"strongest_joint":"J8"}')).toBeUndefined();
  });
  it('uses the same complete cross-field contract as the held-out scorer', () => {
    expect(predictionBrief(contact(',"evidence_end_ms":1024'))).toEqual({ title: 'Accidental contact predicted', strongest: 'J4', onset: 298 });
    for (const raw of [
      contact(',"affected_joints":[]'),
      contact(',"affected_joints":["J1"]'),
      contact(',"affected_joints":["J4","J4"]'),
      contact(',"evidence_start_ms":null'),
      contact(',"evidence_end_ms":298'),
      contact(',"extra":"unsupported"'),
    ]) expect(predictionBrief(raw)).toBeUndefined();
  });
  it('keeps typed prediction fields separate from generated evidence prose', () => {
    const raw = `Answer: ${contact()}\nEvidence: The manual event marker is at 298 ms.`;
    expect(structuredPrediction(raw)).toEqual({ contact: true, event_type: 'accidental', onset_ms: 298,
      strongest_joint: 'J4', affected_joints: ['J4'], evidence_start_ms: 298, evidence_end_ms: 548 });
    expect(JSON.stringify(structuredPrediction(raw))).not.toContain('manual event marker');
  });
});

import { describe, expect, it } from 'vitest';
import { predictionBrief, predictionCue } from './predictionBrief';
describe('compact generated interpretation', () => {
  it('places generated onset in recording time without inventing a joint or a free-motion impact', () => {
    const interval = { start: 5.641, end: 6.665 };
    const raw = '{"contact":true,"event_type":"accidental","onset_ms":298,"strongest_joint":"J4"}';
    const cue = predictionCue(raw, interval)!;
    expect(cue.onsetSeconds).toBeCloseTo(5.939);
    expect(cue.channelId).toBe('joint_4');
    interval.start = 10;
    expect(cue.interval.start).toBe(5.641);
    expect(predictionCue(raw, interval)).toBeUndefined();
    expect(predictionCue('{"contact":false,"event_type":"free","onset_ms":100}', {start: 0, end: 1.024})).toBeUndefined();
    expect(predictionCue('{"contact":true,"event_type":"accidental","onset_ms":1024}', {start: 0, end: 1.024})).toBeUndefined();
    expect(predictionCue('{"contact":true,"event_type":"accidental","onset_ms":100,"strongest_joint":"J8"}', {start: 0, end: 1.024})?.channelId).toBeUndefined();
  });
  it('shows only supported predictions and preserves the distinction from facts', () => {
    expect(predictionBrief('Answer: {"contact":true,"event_type":"intentional","strongest_joint":"J4","onset_ms":312}\nEvidence: signal.')).toEqual({ title: 'Intentional contact predicted', strongest: 'J4', onset: 312 });
    expect(predictionBrief('Answer: {"contact":false,"event_type":"free"}')?.title).toBe('Free motion predicted');
  });
  it('falls back rather than disguising missing or inconsistent model fields', () => {
    for (const raw of [undefined, '', 'malformed', '{"contact":null,"event_type":"free"}', '{"contact":false,"event_type":"accidental"}', '{"contact":true,"event_type":"unknown"}']) expect(predictionBrief(raw)).toBeUndefined();
    expect(predictionBrief('{"contact":true,"event_type":"accidental","onset_ms":2048,"strongest_joint":"J8"}')).toEqual({title:'Accidental contact predicted',strongest:undefined,onset:undefined});
  });
});

import { describe, expect, it } from 'vitest';
import { predictionBrief } from './predictionBrief';
describe('compact generated interpretation', () => {
  it('shows only supported predictions and preserves the distinction from facts', () => {
    expect(predictionBrief('Answer: {"contact":true,"event_type":"intentional","strongest_joint":"J4","onset_ms":312}\nEvidence: signal.')).toEqual({ title: 'Intentional contact predicted', strongest: 'J4', onset: 312 });
    expect(predictionBrief('Answer: {"contact":false,"event_type":"free"}')?.title).toBe('Free motion predicted');
  });
  it('falls back rather than disguising missing or inconsistent model fields', () => {
    for (const raw of [undefined, '', 'malformed', '{"contact":null,"event_type":"free"}', '{"contact":false,"event_type":"accidental"}', '{"contact":true,"event_type":"unknown"}']) expect(predictionBrief(raw)).toBeUndefined();
    expect(predictionBrief('{"contact":true,"event_type":"accidental","onset_ms":2048,"strongest_joint":"J8"}')).toEqual({title:'Accidental contact predicted',strongest:undefined,onset:undefined});
  });
});

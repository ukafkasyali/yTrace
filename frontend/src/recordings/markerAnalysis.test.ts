import { describe, expect, it } from 'vitest';
import { markerWindow } from './markerAnalysis';
import type { Marker } from '../types';

const marker = (timeSeconds: number): Marker => ({ id: `event-${timeSeconds}`, timeSeconds, label: 'Event', kind: 'publisher_annotation', source: 'JK_moments' });
describe('marker analysis context', () => {
  it('creates a shifted 1.024 second context window', () => {
    const beginning = markerWindow(marker(.2), 10);
    expect(beginning.start).toBe(0);
    expect(beginning.end).toBeCloseTo(1.024);
    const ending = markerWindow(marker(9.8), 10);
    expect(ending.start).toBeCloseTo(8.976);
    expect(ending.end).toBe(10);
  });
});

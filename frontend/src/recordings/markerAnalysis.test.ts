import { describe, expect, it } from 'vitest';
import { createMarkerAnalysis, markerWindow } from './markerAnalysis';
import type { DemoData, Marker } from '../types';

const marker = (timeSeconds: number): Marker => ({ id: `event-${timeSeconds}`, timeSeconds, label: 'Event', kind: 'publisher_annotation', source: 'JK_moments' });
const data = { recording: { id: 'recording', durationSeconds: 10 }, detail: { startSeconds: 4, endSeconds: 9 } } as DemoData;

describe('marker analysis context', () => {
  it('creates a shifted 1.024 second context window', () => {
    const beginning = markerWindow(marker(.2), 10);
    expect(beginning.start).toBe(0);
    expect(beginning.end).toBeCloseTo(1.024);
    const ending = markerWindow(marker(9.8), 10);
    expect(ending.start).toBeCloseTo(8.976);
    expect(ending.end).toBe(10);
  });

  it('uses OpenTSLM only when the full raw window is available', () => {
    const request = createMarkerAnalysis(data, marker(6.187), 8, 'checkpoint-a');
    expect(request.mode).toBe('assistant');
    expect(request.interval.start).toBeCloseTo(5.787);
    expect(request.interval.end).toBeCloseTo(6.811);
    expect(request.playhead).toBe(8);
    expect(request.id).toContain('checkpoint-a');
    expect(createMarkerAnalysis(data, marker(9.2), 8).mode).toBe('local');
  });
});

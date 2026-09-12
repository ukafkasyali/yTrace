import { describe, expect, it } from 'vitest';
import { checkpointLabel, fitModelWindow, modelWindowIssue } from './modelWindow';
import type { DemoData } from '../types';
const times = Array.from({ length: 5000 }, (_, i) => (4000 + i) / 1000);
const channels = Array.from({ length: 7 }, (_, i) => ({ id: `joint_${i+1}`, name: `Joint ${i+1}`, unit: 'Nm', values: times.map(t => t*(i+1)) }));
const data = { recording: { durationSeconds: 10, sampleRateHz: 1000 }, times, channels, detail: { startSeconds: 4, endSeconds: 9, times, channels } } as DemoData;
describe('model input window', () => {
  it('accepts exact raw input and rejects display, subsets, gaps, and other lengths', () => {
    const interval = { start: 5.787, end: 6.811 };
    expect(modelWindowIssue(data, interval, 8)).toBeUndefined();
    expect(modelWindowIssue(data, { start: 3, end: 4.024 }, 8)).toContain('Raw model input');
    expect(modelWindowIssue(data, { start: 5, end: 7 }, 8)).toContain('exactly 1.024');
    expect(modelWindowIssue(data, interval, 6)).toContain('replay cursor');
    const broken = structuredClone(data); broken.detail.times[2000] += .0001;
    expect(modelWindowIssue(broken, interval, 8)).toContain('contiguous');
  });
  it('fits to raw bounds without using future samples or padding a short history', () => {
    expect(fitModelWindow(data, { start: 3, end: 7 }, 8)).toEqual({ start: 4, end: 5.024 });
    expect(fitModelWindow(data, { start: 8, end: 9 }, 8)).toEqual({ start: 6.976, end: 8 });
    expect(fitModelWindow(data, { start: 4, end: 4.5 }, 4.5)).toBeUndefined();
  });
  it('does not call an unrecognized checkpoint canary-v4', () => {
    expect(checkpointLabel()).toBe('Checkpoint unverified');
    expect(checkpointLabel(`checkpoint-sha256:${'a'.repeat(64)}`)).toBe('Checkpoint aaaaaaaa');
  });
});

describe('replay sampling boundaries', () => {
  it('fits consistently behind fractional playback cursors without rounding into the future', () => {
    for (const cursor of [7.0001, 7.0004, 7.0006, 7.0009, 7.0011]) {
      const fitted = fitModelWindow(data, { start: 8, end: 9 }, cursor)!;
      expect(fitted).toBeDefined();
      expect(fitted.end).toBeLessThanOrEqual(cursor);
      expect(modelWindowIssue(data, fitted, cursor)).toBeUndefined();
    }
  });
});

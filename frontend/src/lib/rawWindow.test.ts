import { describe, expect, it } from 'vitest';
import type { DemoData } from '../types';
import type { SignalWindow } from '../services';
import { withRawWindow } from './rawWindow';

const interval = { start: 6, end: 7.024 };
const ids = Array.from({ length: 7 }, (_, i) => `joint_${i + 1}`);
const times = Array.from({ length: 1024 }, (_, i) => interval.start + i / 1000);
const data = {
  recording: { id: 'full', name: 'Full recording', durationSeconds: 10, sampleRateHz: 1000,
    displaySampleRateHz: 100, sourceUrl: 'source', archive: 'collision', channelCount: 7, eventCount: 0 },
  times: [0, .01], channels: ids.map((id, i) => ({ id, name: `Joint ${i + 1}`, unit: 'Nm', values: [0, 0] })),
  detail: { startSeconds: 0, endSeconds: 1.024, times: [0, .001],
    channels: ids.map((id, i) => ({ id, name: `Joint ${i + 1}`, unit: 'Nm', values: [0, 0] })) }, events: [],
} satisfies DemoData;
const response = {
  window: { datasetId: 'zenodo-21927431', recordingId: 'full', startSec: 6, endSec: 7.024, channelIds: ids },
  resolution: 'raw', series: ids.map((channelId, i) => ({ channelId, timeSec: times, values: times.map(t => t * (i + 1)) })),
} satisfies SignalWindow;

describe('on-demand raw model window', () => {
  it('installs a matching seven-joint 1 kHz source window', () => {
    const next = withRawWindow(data, 'zenodo-21927431', interval, response);
    expect(next.detail.startSeconds).toBe(6);
    expect(next.detail.endSeconds).toBe(7.024);
    expect(next.detail.times).toHaveLength(1024);
    expect(next.detail.channels[6].values[100]).toBeCloseTo(42.7);
    expect(next.times).toBe(data.times);
  });

  it('rejects mismatched identity, display data and invalid cadence', () => {
    expect(() => withRawWindow(data, 'other', interval, response)).toThrow('does not match');
    expect(() => withRawWindow(data, 'zenodo-21927431', interval, { ...response, resolution: 'display' })).toThrow('does not match');
    const bad = structuredClone(response);
    bad.series[0].timeSec[10] += .0001;
    expect(() => withRawWindow(data, 'zenodo-21927431', interval, bad)).toThrow('contiguous');
  });
});

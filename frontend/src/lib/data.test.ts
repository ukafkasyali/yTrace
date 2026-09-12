import { afterEach, describe, expect, it, vi } from 'vitest';
import { analyzeWindow, downsample, loadDemoData, selectWindow } from './data';
import type { DemoData } from '../types';

function fixture(): DemoData {
  const times = Array.from({ length: 11 }, (_, i) => i);
  const channels = Array.from({ length: 7 }, (_, i) => ({ id: `j${i + 1}`, name: `Joint ${i + 1}`, unit: 'Nm', values: times.map(t => i === 0 ? -t : i === 1 ? t / 2 : 0) }));
  return { recording: { id: 'test', name: 'test', durationSeconds: 11, sampleRateHz: 2, displaySampleRateHz: 1, sourceUrl: '', archive: '', channelCount: 7, eventCount: 0 }, times, channels, events: [], detail: { startSeconds: 4, endSeconds: 9, times: [4, 4.5, 5, 5.5, 6, 6.5, 7, 7.5, 8, 8.5], channels: channels.map(c => ({ ...c, values: [4, 4.5, 5, 5.5, 6, 6.5, 7, 7.5, 8, 8.5].map(t => c.id === 'j1' ? -t : c.id === 'j2' ? t / 2 : 0) })) } };
}
afterEach(() => vi.unstubAllGlobals());
describe('window selection', () => {
  it('uses raw detail for a fully contained half-open interval', () => { const w = selectWindow(fixture(), { start: 4, end: 5 }); expect(w.times).toEqual([4, 4.5]); expect(w.resolution).toBe('raw'); });
  it('uses overview for a partially overlapping interval', () => { const w = selectWindow(fixture(), { start: 3, end: 5 }); expect(w.times).toEqual([3, 4]); expect(w.resolution).toBe('display'); });
  it('rejects invalid bounds and nonfinite selected values', () => { expect(() => selectWindow(fixture(), { start: 2, end: 2 })).toThrow(); expect(() => selectWindow(fixture(), { start: NaN, end: 5 })).toThrow(); const d = fixture(); d.channels[0].values[2] = Infinity; expect(() => selectWindow(d, { start: 1, end: 4 })).toThrow(); });
});
describe('measured analysis', () => {
  it('calculates ranges and evidence from actual samples', () => { const a = analyzeWindow(fixture(), { start: 0, end: 4 }, 'Which changes most?'); expect(a.text).toContain('Joint 1: 3.000 Nm'); expect(a.evidence[0].channelId).toBe('j1'); expect(a.text).toContain('display samples'); });
  it('finds an absolute negative peak and its timestamp', () => { const a = analyzeWindow(fixture(), { start: 0, end: 4 }, 'peak'); expect(a.text).toContain('3.000 Nm at 3.000 s'); expect(a.text).toContain('signed value -3.000'); });
  it('rejects future context and avoids causal invention', () => { expect(() => analyzeWindow(fixture(), { start: 4, end: 6 }, 'range', 5)).toThrow(/replay cursor/); const a = analyzeWindow(fixture(), { start: 0, end: 4 }, 'Why did it collide and how do I repair it?'); expect(a.evidence).toEqual([]); expect(a.text).toContain('cannot determine'); });
  it('compares medians and RMS on the first and last thirty percent', () => { const d = fixture(); d.channels[0].values = [0, 0, 0, 0, 0, 0, 0, -3, 0, 3, 0]; const a = analyzeWindow(d, { start: 0, end: 10 }, 'variability'); expect(a.text).toContain('Joint 1: 0.000 → 2.449 Nm'); });
});
describe('loading', () => {
  it('validates full recording rather than accepting malformed source data', async () => { const d = fixture(); d.detail.times[2] = d.detail.times[1]; vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => d })); await expect(loadDemoData()).rejects.toThrow(/strictly increasing/); });
  it('rejects missing channels', async () => { const d = fixture(); d.channels.pop(); vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => d })); await expect(loadDemoData()).rejects.toThrow(/seven/); });
});
describe('display downsampling', () => {
  it('preserves extrema and endpoints within the point budget', () => { const points = Array.from({ length: 1000 }, (_, x) => ({ x, y: x === 501 ? 100 : x === 701 ? -90 : 0 })); const result = downsample(points, 40); expect(result.length).toBeLessThanOrEqual(40); expect(result[0]).toEqual(points[0]); expect(result.at(-1)).toEqual(points.at(-1)); expect(result).toContainEqual(points[501]); expect(result).toContainEqual(points[701]); expect(result.every((p, i) => i === 0 || p.x > result[i - 1].x)).toBe(true); });
  it('handles tiny budgets and rejects nonfinite data', () => { expect(downsample([{ x: 0, y: 0 }, { x: 1, y: 9 }, { x: 2, y: 0 }, { x: 3, y: 0 }], 3)).toEqual([{ x: 0, y: 0 }, { x: 1, y: 9 }, { x: 3, y: 0 }]); expect(() => downsample([{ x: 0, y: NaN }], 3)).toThrow(); });
});

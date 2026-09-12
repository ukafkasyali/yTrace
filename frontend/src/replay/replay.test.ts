import { describe, expect, it } from 'vitest';
import { advancePlayhead, clampTime } from './useReplay';

describe('replay clock', () => {
  it('advances by elapsed wall-clock time and speed', () => {
    expect(advancePlayhead(2, 500, 2, 10)).toBe(3);
    expect(advancePlayhead(2, 500, .5, 10)).toBe(2.25);
  });
  it('stops exactly at the recording end', () => {
    expect(advancePlayhead(9.9, 500, 4, 10)).toBe(10);
    expect(advancePlayhead(10, 5000, 4, 10)).toBe(10);
  });
  it('clamps seeks and initial time to the recording bounds', () => {
    expect(clampTime(-2, 10)).toBe(0);
    expect(clampTime(12, 10)).toBe(10);
    expect(clampTime(8, 2)).toBe(2);
    expect(clampTime(1, 0)).toBe(0);
  });
  it('handles invalid duration, time, elapsed and speed safely', () => {
    expect(clampTime(NaN, 10)).toBe(0);
    expect(clampTime(2, Infinity)).toBe(0);
    expect(clampTime(2, -10)).toBe(0);
    expect(advancePlayhead(2, -100, 2, 10)).toBe(2);
    expect(advancePlayhead(2, Infinity, 2, 10)).toBe(2);
    expect(advancePlayhead(2, 1000, NaN, 10)).toBe(2);
    expect(advancePlayhead(2, 1000, -2, 10)).toBe(2);
  });
  it('speed changes apply only to subsequently elapsed time', () => {
    const before = advancePlayhead(0, 1000, 1, 10);
    expect(advancePlayhead(before, 1000, 2, 10)).toBe(3);
  });
});

import collisionPositions from '../../public/robot/kuka/positions-03-15-12-53.json';
import intentionalPositions from '../../public/robot/kuka/positions-03-22-11-18.json';
import { describe, expect, it } from 'vitest';
import { positionAtPlayhead, torqueAtPlayhead, validatePositions, type PositionData } from './telemetry';
import { validateGeometry, validPose } from './scene';
import data from '../../public/data/kuka-demo.json';
import geometry from '../../public/robot/kuka/kinematics.json';
import measuredPositions from '../../public/robot/kuka/positions.json';
import type { DemoData } from '../types';

const fixture = data as DemoData;
describe('3D reference telemetry', () => {
  it('uses raw samples inside the verified detail excerpt', () => {
    const sample = torqueAtPlayhead(fixture, 6.1875)!;
    expect(sample.resolution).toBe('raw');
    expect(sample.time).toBe(6.187);
    expect(sample.channels.map(c => c.id)).toEqual(fixture.channels.map(c => c.id));
    expect(sample.channels.every(c => c.unit === 'Nm')).toBe(true);
  });
  it('never reads ahead of the replay boundary or interpolates future measurements', () => {
    for (const playhead of [0, .015, 3.9999, 4, 5.7875, 8.9999, 9, 169.999, 170]) {
      expect(torqueAtPlayhead(fixture, playhead)!.time).toBeLessThanOrEqual(playhead);
    }
    expect(torqueAtPlayhead(fixture, 9)!.resolution).toBe('display');
    expect(torqueAtPlayhead(fixture, -1)).toBe(null);
    expect(torqueAtPlayhead(fixture, Infinity)).toBe(null);
  });
  it('checks the geometry contract before building a scene', () => {
    expect(() => validateGeometry(geometry)).not.toThrow();
    expect(() => validateGeometry({ ...geometry, sourceUpAxis: 'Y' })).toThrow();
    const invalid = structuredClone(geometry);
    invalid.joints[0].channelId = 'unmapped';
    expect(() => validateGeometry(invalid)).toThrow();
    const scaledAxis = structuredClone(geometry);
    scaledAxis.joints[0].axis = [0, 0, 2];
    expect(() => validateGeometry(scaledAxis)).toThrow();
  });
});

describe('measured robot articulation', () => {
  const positions = measuredPositions as PositionData;
  it('validates the real position fixture and every joint against URDF limits', () => {
    expect(() => validatePositions(positions, fixture.recording.id)).not.toThrow();
    validateGeometry(geometry);
    expect(positions.times).toHaveLength(17000);
    expect(positions.times[0]).toBe(.01);
    expect(positions.times.at(-1)).toBe(170);
    for (const time of [.01, 4, 8, 30, 100, 170]) {
      expect(validPose(geometry, positionAtPlayhead(positions, fixture.recording.id, time)!.radians)).toBe(true);
    }
  });
  it('holds the last measured pose with no future interpolation', () => {
    const at = positionAtPlayhead(positions, fixture.recording.id, 8.005)!;
    expect(at.time).toBe(8);
    const index = positions.times.indexOf(8);
    expect(at.radians).toEqual(positions.joints.map(joint => joint.values[index]));
    expect(positionAtPlayhead(positions, fixture.recording.id, 8.01)!.time).toBe(8.01);
    for (const time of [.01, .01999, 1.4321, 8.005, 169.999, 170]) {
      expect(positionAtPlayhead(positions, fixture.recording.id, time)!.time).toBeLessThanOrEqual(time);
    }
  });
  it('does not manufacture startup, out-of-range or other-recording poses', () => {
    for (const time of [-1, 0, .00999, 170.001, Infinity, NaN]) {
      expect(positionAtPlayhead(positions, fixture.recording.id, time)).toBeNull();
    }
    expect(positionAtPlayhead(positions, 'another-recording', 8)).toBeNull();
  });
  it('rejects wrong units, unverified mapping, gaps, malformed channels and nonfinite angles', () => {
    const small = { ...positions, times: [.01, .02], joints: positions.joints.map(j => ({ ...j, values: j.values.slice(0, 2) })) };
    expect(() => validatePositions({ ...small, angularUnit: 'degree' }, fixture.recording.id)).toThrow();
    expect(() => validatePositions({ ...small, validation: { mappingVerified: false, scope: 'unverified' } }, fixture.recording.id)).toThrow();
    expect(() => validatePositions({ ...small, times: [.01, .03] }, fixture.recording.id)).toThrow();
    expect(() => validatePositions({ ...small, times: [.02, .02] }, fixture.recording.id)).toThrow();
    expect(() => validatePositions({ ...small, joints: small.joints.slice(0, 6) }, fixture.recording.id)).toThrow();
    const broken = structuredClone(small);
    broken.joints[0].values[0] = NaN;
    expect(() => validatePositions(broken, fixture.recording.id)).toThrow();
    expect(() => validatePositions(small, 'another-recording')).toThrow();
    validateGeometry(geometry);
    expect(validPose(geometry, [0, 0, 0, 0, 0, 0, NaN])).toBe(false);
    expect(validPose(geometry, [0, 5, 0, 0, 0, 0, 0])).toBe(false);
  });
});

describe('per-recording example positions', () => {
  it('routes distinct recordings to their own measured angles with disclosed mapping scope', async () => {
    const { positionFixture } = await import('./telemetry');
    const fixtures: Record<string, PositionData> = { '03-15-12-53': collisionPositions as PositionData, '03-22-11-18': intentionalPositions as PositionData };
    validateGeometry(geometry);
    for (const id of ['03-15-12-53', '03-22-11-18']) {
      const path = positionFixture(id)!;
      expect(path).toContain(id);
      const positions = fixtures[id];
      expect(() => validatePositions(positions, id)).not.toThrow();
      expect(positions.validation.mappingVerified).toBe(false);
      expect(positionAtPlayhead(positions, id, positions.endSeconds!)?.time).toBe(positions.times.at(-1));
      expect(positionAtPlayhead(positions, id, positions.endSeconds! + .001)).toBeNull();
      expect(positions.validation.conventionReference).toBe('05-28-21-25');
      const first = positionAtPlayhead(positions, id, 6)!;
      const later = positionAtPlayhead(positions, id, 60)!;
      expect(first.radians).not.toEqual(later.radians);
      expect(validPose(geometry, first.radians)).toBe(true);
      expect(positionAtPlayhead(positions, '05-28-21-25', 6)).toBeNull();
      const unverified = structuredClone(positions);
      unverified.validation.timestampsExactlyMatchTorque = false;
      expect(() => validatePositions(unverified, id)).toThrow();
    }
    expect(positionFixture('unknown')).toBeUndefined();
  });
});

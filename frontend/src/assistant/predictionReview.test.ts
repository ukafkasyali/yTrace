import { describe, expect, it } from 'vitest';
import type { DemoData } from '../types';
import { reviewPrediction } from './predictionReview';

const channels = Array.from({ length: 7 }, (_, index) => ({
  id: `joint_${index + 1}`, name: `Joint ${index + 1}`, unit: 'Nm', values: index === 1 ? [-3, 2] : [0, index === 3 ? 2 : 1],
}));
const data: DemoData = {
  recording: { id: 'r', name: 'R', durationSeconds: 2, sampleRateHz: 1000, displaySampleRateHz: 1000, sourceUrl: '', archive: '', channelCount: 7, eventCount: 0 },
  times: [0, .001], channels, detail: { startSeconds: 0, endSeconds: 1, times: [0, .001], channels }, events: [],
};
const prediction = (joint: string) => `Answer: {"contact":true,"event_type":"accidental","onset_ms":1,"strongest_joint":"${joint}","affected_joints":["${joint}"],"evidence_start_ms":1,"evidence_end_ms":2}`;

describe('prediction review', () => {
  it('flags a visible model and measurement disagreement', () => {
    expect(reviewPrediction(data, { start: 0, end: .002 }, prediction('J4'))).toEqual({
      predictedJoint: 'J4', largestRangeJoint: 'J2',
      note: 'OpenTSLM highlights J4, while J2 has the largest measured torque range. Review both signals before sharing this handoff.',
    });
  });

  it('does not manufacture a review issue when the strongest joints agree', () => {
    expect(reviewPrediction(data, { start: 0, end: .002 }, prediction('J2'))).toBeUndefined();
  });
});

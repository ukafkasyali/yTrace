import { selectWindow } from '../lib/data';
import type { DemoData, Interval } from '../types';
import { structuredPrediction } from './predictionBrief';

export type PredictionReview = {
  predictedJoint: string;
  largestRangeJoint: string;
  note: string;
};

/** Surface a cross-check between different model and measurement quantities. */
export function reviewPrediction(data: DemoData, interval: Interval, output?: string): PredictionReview | undefined {
  const prediction = structuredPrediction(output);
  if (!prediction?.strongest_joint) return;
  const window = selectWindow(data, interval);
  const strongest = window.channels
    .map(channel => ({ channel, range: Math.max(...channel.values) - Math.min(...channel.values) }))
    .sort((a, b) => b.range - a.range)[0];
  if (!strongest) return;
  const largestRangeJoint = `J${strongest.channel.id.replace('joint_', '')}`;
  if (largestRangeJoint === prediction.strongest_joint) return;
  return {
    predictedJoint: prediction.strongest_joint,
    largestRangeJoint,
    note: `OpenTSLM predicts ${prediction.strongest_joint} as the strongest disturbance. ${largestRangeJoint} has the largest measured torque range. These are different quantities; inspect both before handoff.`,
  };
}

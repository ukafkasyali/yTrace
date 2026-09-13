import { selectWindow } from '../lib/data';
import type { DemoData, Interval } from '../types';
import { structuredPrediction } from './predictionBrief';

export type PredictionReview = {
  predictedJoint: string;
  largestRangeJoint: string;
  note: string;
};

/** Surface a model/measurement disagreement without treating either quantity as physical truth. */
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
    note: `OpenTSLM highlights ${prediction.strongest_joint}, while ${largestRangeJoint} has the largest measured torque range. Review both signals before sharing this handoff.`,
  };
}

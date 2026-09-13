import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import type { PredictionCue } from '../assistant/predictionBrief';
import type { DemoData } from '../types';
import SignalViewer from './SignalViewer';

const data: DemoData = {
  recording: { id: 'run-1', name: 'Run 1', durationSeconds: 2, sampleRateHz: 1_000, displaySampleRateHz: 1_000, sourceUrl: '', archive: '', channelCount: 1, eventCount: 0 },
  times: [0, .5, 1],
  channels: [{ id: 'joint_1', name: 'Joint 1', unit: 'Nm', values: [0, 1, 0] }],
  events: [],
  detail: { startSeconds: 0, endSeconds: 2, times: [0, .5, 1], channels: [{ id: 'joint_1', name: 'Joint 1', unit: 'Nm', values: [0, 1, 0] }] },
};

const prediction: PredictionCue = { title: 'Accidental contact predicted', onsetSeconds: .5, channelId: 'joint_1', interval: { start: 0, end: 1.024 } };

function render(cue?: PredictionCue, playhead = 1) {
  return renderToStaticMarkup(<SignalViewer prediction={cue} data={data} playhead={playhead} interval={{ start: 0, end: 1.024 }} viewport={{ start: 0, end: 1 }} highlighted={[]} following={false} zoom={1.024} onSelect={() => undefined} onFollow={() => undefined} onZoom={() => undefined} />);
}

describe('generated onset evidence cue', () => {
  it('labels the prediction on the measured signal at its recording time', () => {
    const markup = render(prediction);
    expect(markup).toContain('model-onset-cue');
    expect(markup).toContain('left:50%');
    expect(markup).toContain('Predicted onset');
  });

  it('does not draw a prediction ahead of the replay playhead', () => {
    expect(render(prediction, .25)).not.toContain('model-onset-cue');
  });
});

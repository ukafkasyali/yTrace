import { afterEach, describe, expect, it, vi } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import AssistantPanel from './AssistantPanel';
import { investigationContextFingerprint, investigationStorageKey } from './investigationSession';
import { createServices, DECLARED_MODELS } from '../services';
import fixture from '../../public/data/kuka-demo.json';
import type { DemoData } from '../types';

afterEach(() => vi.unstubAllGlobals());

describe('assistant result visibility', () => {
  it('keeps an unusable completed model response silent in the visible answer', () => {
    const data = fixture as DemoData;
    const datasetId = 'zenodo-21927431';
    const interval = { start: 5.787, end: 6.811 };
    const key = investigationStorageKey(datasetId, data.recording.id);
    const saved = JSON.stringify({
      schemaVersion: 2,
      recordingId: data.recording.id,
      contextFingerprint: investigationContextFingerprint(data, datasetId, interval),
      message: {
        id: 'invalid-result', question: 'Analyze this robot telemetry window.',
        interval, playhead: interval.end, text: 'INVALID MODEL TEXT MUST STAY HIDDEN',
        mode: 'assistant', source: 'OpenTSLM · completed', evidence: [], tools: [],
        status: 'complete', modelOutput: 'Answer: {"contact": null}',
      },
    });
    const sessionStorage = {
      getItem: (requested: string) => requested === key ? saved : null,
      setItem: () => undefined,
      removeItem: () => undefined,
    };
    vi.stubGlobal('window', { sessionStorage });

    const html = renderToStaticMarkup(<AssistantPanel
      data={data} datasetId={datasetId} playhead={interval.end} interval={interval}
      services={createServices()} registry={{ models: [...DECLARED_MODELS], loading: false, error: '', refresh: () => undefined }}
      onRetryRaw={() => undefined} onEvidence={() => undefined} onModelWindow={() => undefined}
      onCompare={() => undefined} onRobotPrediction={() => undefined}
    />);

    expect(html).not.toContain('No usable structured prediction');
    expect(html).not.toContain('INVALID MODEL TEXT MUST STAY HIDDEN');
    expect(html).toContain('Model &amp; input details');
  });
});

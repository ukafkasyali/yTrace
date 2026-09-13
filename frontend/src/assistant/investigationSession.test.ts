import { describe, expect, it } from 'vitest';
import { investigationStorageKey, persistInvestigation, restoreInvestigation, type PersistableInvestigation } from './investigationSession';

function memoryStorage() {
  const values = new Map<string, string>();
  return {
    getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => { values.set(key, value); },
    removeItem: (key: string) => { values.delete(key); },
  };
}

const message: PersistableInvestigation = {
  id: 'answer-1', status: 'complete', tools: ['Measured raw torque'],
  question: 'Analyze this robot telemetry window.', interval: { start: 5.787, end: 6.811 },
  playhead: 6.811, replayCursor: 5.787, text: 'OpenTSLM predicts external contact.',
  mode: 'assistant', source: 'team-opentslm · completed', evidence: [],
  modelId: 'team-opentslm', modelRevision: 'checkpoint-sha256:abc',
  inputTrace: { samplesPerChannel: 1024 }, modelOutput: 'Answer: {}',
};

describe('browser-session investigation snapshot', () => {
  it('restores only a completed result for the matching recording', () => {
    const storage = memoryStorage();
    const key = investigationStorageKey('dataset', 'recording');
    persistInvestigation(storage, key, 'recording', message);
    expect(restoreInvestigation(storage, key, 'recording', 170)).toEqual(message);
    expect(restoreInvestigation(storage, key, 'other-recording', 170)).toBeUndefined();
  });

  it('drops corrupt or out-of-range snapshots instead of showing stale evidence', () => {
    const storage = memoryStorage();
    const key = investigationStorageKey('dataset', 'recording');
    storage.setItem(key, JSON.stringify({ schemaVersion: 1, recordingId: 'recording', message: { ...message, interval: { start: 169.5, end: 170.524 } } }));
    expect(restoreInvestigation(storage, key, 'recording', 170)).toBeUndefined();
    expect(storage.getItem(key)).toBeNull();
    storage.setItem(key, '{bad json');
    expect(restoreInvestigation(storage, key, 'recording', 170)).toBeUndefined();
    expect(storage.getItem(key)).toBeNull();
  });

  it('does not persist incomplete analysis', () => {
    const storage = memoryStorage();
    const key = investigationStorageKey('dataset', 'recording');
    persistInvestigation(storage, key, 'recording', { ...message, status: 'cancelled' });
    expect(storage.getItem(key)).toBeNull();
  });
});

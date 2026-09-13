import { describe, expect, it } from 'vitest';
import { investigationContextFingerprint, investigationStorageKey, persistInvestigation, restoreInvestigation, type PersistableInvestigation } from './investigationSession';
import type { DemoData } from '../types';

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
const telemetry: DemoData = {
  recording: { id: 'recording', name: 'Recording', durationSeconds: 3, sampleRateHz: 1000, displaySampleRateHz: 1000,
    sourceUrl: 'https://example.com/source', archive: 'archive', channelCount: 7, eventCount: 0 },
  times: [1, 1.001],
  channels: Array.from({ length: 7 }, (_, index) => ({ id: `joint_${index + 1}`, name: `Joint ${index + 1}`, unit: 'Nm', values: [index, index + 1] })),
  detail: { startSeconds: 1, endSeconds: 1.002, times: [1, 1.001],
    channels: Array.from({ length: 7 }, (_, index) => ({ id: `joint_${index + 1}`, name: `Joint ${index + 1}`, unit: 'Nm', values: [index, index + 1] })) },
  events: [],
};

describe('browser-session investigation snapshot', () => {
  it('restores only a completed result for the matching recording', () => {
    const storage = memoryStorage();
    const key = investigationStorageKey('dataset', 'recording', 'collision');
    persistInvestigation(storage, key, 'recording', 'context-a', message);
    expect(restoreInvestigation(storage, key, 'recording', 170, 'context-a')).toEqual(message);
    expect(restoreInvestigation(storage, key, 'other-recording', 170, 'context-a')).toBeUndefined();
  });

  it('drops corrupt or out-of-range snapshots instead of showing stale evidence', () => {
    const storage = memoryStorage();
    const key = investigationStorageKey('dataset', 'recording');
    storage.setItem(key, JSON.stringify({ schemaVersion: 2, recordingId: 'recording', contextFingerprint: 'context-a', message: { ...message, interval: { start: 169.5, end: 170.524 } } }));
    expect(restoreInvestigation(storage, key, 'recording', 170, 'context-a')).toBeUndefined();
    expect(storage.getItem(key)).toBeNull();
    storage.setItem(key, '{bad json');
    expect(restoreInvestigation(storage, key, 'recording', 170, 'context-a')).toBeUndefined();
    expect(storage.getItem(key)).toBeNull();
  });

  it('rejects malformed evidence and cursors outside the recording', () => {
    const storage = memoryStorage();
    const key = investigationStorageKey('dataset', 'recording');
    for (const invalid of [
      { ...message, evidence: [{}] },
      { ...message, playhead: 171 },
      { ...message, replayCursor: -1 },
      { ...message, replayCursor: 7 },
      { ...message, modelOutput: { contact: true } },
      { ...message, evidence: [{ channelId: 'joint_1', channelIds: ['joint_1', 'joint_1'], label: 'Duplicate channels', interval: { start: 5.787, end: 6.811 } }] },
    ]) {
      storage.setItem(key, JSON.stringify({ schemaVersion: 2, recordingId: 'recording', contextFingerprint: 'context-a', message: invalid }));
      expect(restoreInvestigation(storage, key, 'recording', 170, 'context-a')).toBeUndefined();
      expect(storage.getItem(key)).toBeNull();
    }
  });

  it('restores bounded evidence and an incident-start replay cursor', () => {
    const storage = memoryStorage();
    const key = investigationStorageKey('dataset', 'recording');
    const valid = {
      ...message,
      replayCursor: 5.787,
      evidence: [{ channelId: 'joint_1', channelIds: ['joint_1', 'joint_2'], label: 'Inspect 2 input channels', interval: { start: 5.787, end: 6.811 } }],
    };
    persistInvestigation(storage, key, 'recording', 'context-a', valid);
    expect(restoreInvestigation(storage, key, 'recording', 170, 'context-a')).toEqual(valid);
  });

  it('does not persist incomplete analysis', () => {
    const storage = memoryStorage();
    const key = investigationStorageKey('dataset', 'recording');
    persistInvestigation(storage, key, 'recording', 'context-a', { ...message, status: 'cancelled' });
    expect(storage.getItem(key)).toBeNull();
  });

  it('separates cases and rejects a snapshot when the selected telemetry changes', () => {
    const storage = memoryStorage();
    expect(investigationStorageKey('dataset', 'recording', 'collision')).not.toBe(investigationStorageKey('dataset', 'recording', 'free'));
    const interval = { start: 1, end: 1.002 };
    const fingerprint = investigationContextFingerprint(telemetry, 'dataset', interval);
    persistInvestigation(storage, investigationStorageKey('dataset', 'recording', 'collision'), 'recording', fingerprint, message);
    const changed = structuredClone(telemetry); changed.detail.channels[0].values[1] += 0.01;
    const changedFingerprint = investigationContextFingerprint(changed, 'dataset', interval);
    expect(changedFingerprint).not.toBe(fingerprint);
    expect(restoreInvestigation(storage, investigationStorageKey('dataset', 'recording', 'collision'), 'recording', 3, changedFingerprint)).toBeUndefined();
  });
});

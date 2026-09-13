import type { InvestigationAnswer } from './investigationReport';
import { selectWindow } from '../lib/data';
import type { DemoData, EvidenceLink, Interval } from '../types';

export type PersistableInvestigation = InvestigationAnswer & {
  id: string;
  tools: string[];
  status: 'running' | 'complete' | 'error' | 'cancelled';
};

type StorageLike = Pick<Storage, 'getItem' | 'setItem' | 'removeItem'>;

export function investigationStorageKey(datasetId: string, recordingId: string, scope = 'original'): string {
  return `trace:last-investigation:${datasetId}:${recordingId}:${scope.replace(/[^a-zA-Z0-9_-]/g, '_')}`;
}

/** Fast identity check for restoring a report against the exact selected telemetry. */
export function investigationContextFingerprint(data: DemoData, datasetId: string, interval: Interval): string {
  let hash = 14695981039346656037n;
  const mixByte = (value: number) => { hash ^= BigInt(value); hash = BigInt.asUintN(64, hash * 1099511628211n); };
  const mixText = (value: string) => { for (const byte of new TextEncoder().encode(value)) mixByte(byte); };
  const bytes = new Uint8Array(8); const view = new DataView(bytes.buffer);
  const mixNumber = (value: number) => { view.setFloat64(0, value, true); bytes.forEach(mixByte); };
  const selected = selectWindow(data, interval);
  [datasetId, data.recording.id, data.recording.sourceUrl, data.recording.archive, selected.resolution].forEach(mixText);
  mixNumber(selected.sampleRateHz); mixNumber(interval.start); mixNumber(interval.end);
  selected.times.forEach(mixNumber);
  selected.channels.forEach(channel => { mixText(channel.id); mixText(channel.unit); channel.values.forEach(mixNumber); });
  return `fnv1a64:${hash.toString(16).padStart(16, '0')}`;
}

function validInterval(value: unknown, durationSeconds: number): value is { start: number; end: number } {
  if (!value || typeof value !== 'object') return false;
  const interval = value as { start?: unknown; end?: unknown };
  return typeof interval.start === 'number' && Number.isFinite(interval.start)
    && typeof interval.end === 'number' && Number.isFinite(interval.end)
    && interval.start >= 0 && interval.end > interval.start && interval.end <= durationSeconds;
}

function validEvidenceLink(value: unknown, durationSeconds: number): value is EvidenceLink {
  if (!value || typeof value !== 'object') return false;
  const evidence = value as Partial<EvidenceLink>;
  return typeof evidence.channelId === 'string' && evidence.channelId.length > 0
    && typeof evidence.label === 'string' && evidence.label.length > 0
    && validInterval(evidence.interval, durationSeconds)
    && (evidence.channelIds === undefined || (
      Array.isArray(evidence.channelIds)
      && evidence.channelIds.length > 0
      && evidence.channelIds.every(channelId => typeof channelId === 'string' && channelId.length > 0)
      && new Set(evidence.channelIds).size === evidence.channelIds.length
    ));
}

function validMessage(value: unknown, durationSeconds: number): value is PersistableInvestigation {
  if (!value || typeof value !== 'object') return false;
  const message = value as Partial<PersistableInvestigation>;
  return message.status === 'complete'
    && typeof message.id === 'string'
    && typeof message.question === 'string'
    && typeof message.text === 'string'
    && typeof message.source === 'string'
    && (message.mode === 'assistant' || message.mode === 'local')
    && (message.modelId === undefined || typeof message.modelId === 'string')
    && (message.modelRevision === undefined || typeof message.modelRevision === 'string')
    && (message.modelOutput === undefined || typeof message.modelOutput === 'string')
    && typeof message.playhead === 'number' && Number.isFinite(message.playhead)
    && message.playhead >= 0 && message.playhead <= durationSeconds
    && (message.replayCursor === undefined || (
      typeof message.replayCursor === 'number' && Number.isFinite(message.replayCursor)
      && message.replayCursor >= 0 && message.replayCursor <= message.playhead
    ))
    && validInterval(message.interval, durationSeconds)
    && message.playhead >= message.interval.end
    && Array.isArray(message.tools) && message.tools.every(tool => typeof tool === 'string')
    && Array.isArray(message.evidence) && message.evidence.every(item => validEvidenceLink(item, durationSeconds));
}

export function restoreInvestigation(storage: StorageLike | undefined, key: string, recordingId: string, durationSeconds: number, contextFingerprint: string): PersistableInvestigation | undefined {
  if (!storage) return;
  try {
    const raw = storage.getItem(key);
    if (!raw) return;
    const saved = JSON.parse(raw) as { schemaVersion?: unknown; recordingId?: unknown; contextFingerprint?: unknown; message?: unknown };
    if (saved.schemaVersion !== 2 || saved.recordingId !== recordingId || saved.contextFingerprint !== contextFingerprint || !validMessage(saved.message, durationSeconds)) {
      storage.removeItem(key);
      return;
    }
    return saved.message;
  } catch {
    try { storage.removeItem(key); } catch { /* storage may be unavailable */ }
    return;
  }
}

export function persistInvestigation(storage: StorageLike | undefined, key: string, recordingId: string, contextFingerprint: string, message: PersistableInvestigation): void {
  if (!storage || message.status !== 'complete') return;
  try { storage.setItem(key, JSON.stringify({ schemaVersion: 2, recordingId, contextFingerprint, message })); }
  catch { /* a finished analysis remains usable when browser storage is unavailable */ }
}

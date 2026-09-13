import type { InvestigationAnswer } from './investigationReport';

export type PersistableInvestigation = InvestigationAnswer & {
  id: string;
  tools: string[];
  status: 'running' | 'complete' | 'error' | 'cancelled';
};

type StorageLike = Pick<Storage, 'getItem' | 'setItem' | 'removeItem'>;

export function investigationStorageKey(datasetId: string, recordingId: string): string {
  return `trace:last-investigation:${datasetId}:${recordingId}`;
}

function validInterval(value: unknown, durationSeconds: number): value is { start: number; end: number } {
  if (!value || typeof value !== 'object') return false;
  const interval = value as { start?: unknown; end?: unknown };
  return typeof interval.start === 'number' && Number.isFinite(interval.start)
    && typeof interval.end === 'number' && Number.isFinite(interval.end)
    && interval.start >= 0 && interval.end > interval.start && interval.end <= durationSeconds;
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
    && typeof message.playhead === 'number' && Number.isFinite(message.playhead)
    && validInterval(message.interval, durationSeconds)
    && message.playhead >= message.interval.end
    && Array.isArray(message.tools) && message.tools.every(tool => typeof tool === 'string')
    && Array.isArray(message.evidence);
}

export function restoreInvestigation(storage: StorageLike | undefined, key: string, recordingId: string, durationSeconds: number): PersistableInvestigation | undefined {
  if (!storage) return;
  try {
    const raw = storage.getItem(key);
    if (!raw) return;
    const saved = JSON.parse(raw) as { schemaVersion?: unknown; recordingId?: unknown; message?: unknown };
    if (saved.schemaVersion !== 1 || saved.recordingId !== recordingId || !validMessage(saved.message, durationSeconds)) {
      storage.removeItem(key);
      return;
    }
    return saved.message;
  } catch {
    try { storage.removeItem(key); } catch { /* storage may be unavailable */ }
    return;
  }
}

export function persistInvestigation(storage: StorageLike | undefined, key: string, recordingId: string, message: PersistableInvestigation): void {
  if (!storage || message.status !== 'complete') return;
  try { storage.setItem(key, JSON.stringify({ schemaVersion: 1, recordingId, message })); }
  catch { /* a finished analysis remains usable when browser storage is unavailable */ }
}

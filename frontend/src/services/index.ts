import type { DemoCase } from '../types';
import { validateDemoData } from '../lib/data';
import { isRequirementsPreview, isRunAccepted, isSourcingManifest, isSourcingRun, type CreateSourcingRun, type RequirementPreviewRequest, type SourcingReview } from './sourcing';
import {
  isApprovedSourceDetail,
  isApprovedManifest,
  isApprovedSourcePage,
  isAssetReceipts,
  isFinalReceipt,
  isImportedRecordPage,
  isImportJob,
  isMappingProposals,
  isMappingSpec,
  isOnboardingJobView,
  type HumanResolution,
  type MappingSpec,
} from './ingestion';
export * from './sourcing';
export * from './ingestion';

export type WindowRef = {
  datasetId: string; recordingId: string; startSec: number; endSec: number; channelIds: string[];
};
export type Channel = { id: string; name: string; unit: string; sampleRateHz: number };
export type Dataset = { id: string; name: string; sourceUrl?: string; revision: string };
export type Recording = { id: string; datasetId: string; name: string; durationSec: number; channels: Channel[] };
export type SignalWindow = {
  window: WindowRef;
  series: { channelId: string; timeSec: number[]; values: (number | null)[] }[];
  resolution: 'raw' | 'display'; aggregation?: string;
};
export type SignalEvent = {
  id: string; recordingId: string; startSec: number; endSec?: number; channelIds: string[];
  label: string; origin: 'publisher_annotation' | 'model_prediction' | 'derived_statistic';
  source: string; confidence?: number;
};
export type Evidence = {
  id: string; window: WindowRef; eventId?: string; label: string; value?: number; unit?: string; source: string;
};
export type ModelProfile = {
  id: string; label: string; available: boolean;
  capabilities: ('language' | 'classification' | 'localization')[];
  reason?: string; revision?: string;
};
export const DECLARED_MODELS: readonly ModelProfile[] = [
  { id: 'assistant', label: 'Telemetry assistant', available: false, capabilities: ['language'], reason: 'Model service not connected' },
  { id: 'cnn-1d', label: '1D CNN', available: false, capabilities: ['classification', 'localization'], reason: 'Model service not connected' },
  { id: 'direct-llm', label: 'Direct LLM', available: false, capabilities: ['language'], reason: 'Model service not connected' },
  { id: 'opentslm', label: 'OpenTSLM', available: false, capabilities: ['language', 'classification', 'localization'], reason: 'Model service not connected' },
];
export type QueryRequest = {
  mode: 'assistant' | 'direct'; modelId?: string; question: string; window: WindowRef;
  playheadSec: number; conversationId?: string;
};
export type QueryEventPayload = {
  callId?: string; tool?: string; label?: string; summary?: string; evidence?: Evidence[];
  text?: string; answer?: string; modelId?: string; modelRevision?: string;
  code?: string; message?: string; retryable?: boolean;
  labels?: { label: string; score?: number }[];
  [key: string]: unknown;
};
export type QueryEvent = {
  id: string; queryId: string;
  type: 'tool.started' | 'tool.completed' | 'answer.delta' | 'answer.completed' | 'query.error' | 'query.cancelled';
  payload: QueryEventPayload;
};
export type DatasetSearchResult = {
  id: string; name: string; sourceUrl: string; description?: string; revision?: string;
};
export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly retryable: boolean;
  constructor(message: string, status = 0, code = 'REQUEST_FAILED', retryable = false) {
    super(message); this.name = 'ApiError'; this.status = status; this.code = code; this.retryable = retryable;
  }
}
export class ServiceUnavailableError extends ApiError {
  constructor() { super('No backend service is configured.', 0, 'SERVICE_UNAVAILABLE', false); this.name = 'ServiceUnavailableError'; }
}
export class ProtocolError extends ApiError {
  constructor(message: string) { super(message, 0, 'INVALID_RESPONSE', false); this.name = 'ProtocolError'; }
}

function validateInterval(startSec: number, endSec: number) {
  if (!Number.isFinite(startSec) || !Number.isFinite(endSec) || startSec < 0 || endSec <= startSec) {
    throw new ApiError('Select a valid, nonempty time interval.', 0, 'INVALID_WINDOW');
  }
}
function validateChannels(channelIds: string[]) {
  if (!channelIds.length || channelIds.some(id => !id.trim()) || new Set(channelIds).size !== channelIds.length) {
    throw new ApiError('Select one or more distinct channels.', 0, 'INVALID_WINDOW');
  }
}
function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}
function validateWindow(data: SignalWindow) {
  if (!isObject(data) || !isObject(data.window) || !Array.isArray(data.series) || !['raw', 'display'].includes(data.resolution)) {
    throw new ProtocolError('The signal response does not match the window contract.');
  }
  validateInterval(data.window.startSec, data.window.endSec);
  for (const series of data.series) {
    if (!Array.isArray(series.timeSec) || !Array.isArray(series.values) || series.timeSec.length !== series.values.length) {
      throw new ProtocolError('Signal timestamps and values have different lengths.');
    }
    if (series.timeSec.some((time, index) => !Number.isFinite(time) || (index > 0 && time <= series.timeSec[index - 1])) ||
      series.values.some(value => value !== null && !Number.isFinite(value))) {
      throw new ProtocolError('The signal response contains invalid timestamps or values.');
    }
  }
  return data;
}

const UUID_PATTERN = '[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}';
const IMPORTED_RECORDING_ID = new RegExp(`^imported:(${UUID_PATTERN}):([a-f0-9]{24})$`);
const INGESTION_ID = new RegExp(`^${UUID_PATTERN}$`);

function importedRecordingPath(recordingId: string, resource: 'replay' | 'signals' | 'events') {
  const match = IMPORTED_RECORDING_ID.exec(recordingId);
  return match
    ? `/ingestions/${encodeURIComponent(match[1])}/records/${encodeURIComponent(match[2])}/${resource}`
    : null;
}

/** baseUrl is the API prefix, e.g. /api. Omission deliberately means disconnected. */
export function createServices(baseUrl?: string) {
  const base = baseUrl?.trim().replace(/\/+$/, '');
  const connected = Boolean(base);
  function endpoint(path: string) {
    if (!base) throw new ServiceUnavailableError();
    return `${base}${path}`;
  }
  async function checkResponse(response: Response) {
    if (response.ok) return response;
    let details: unknown;
    try { details = await response.json(); } catch { /* Error bodies may be empty or HTML. */ }
    const error = isObject(details) && isObject(details.error) ? details.error : {};
    throw new ApiError(
      typeof error.message === 'string' ? error.message : `Request failed (${response.status}).`,
      response.status, typeof error.code === 'string' ? error.code : 'HTTP_ERROR', error.retryable === true,
    );
  }
  async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
    const response = await fetch(endpoint(path), {
      ...init, credentials: 'same-origin',
      headers: { Accept: 'application/json', ...(init.body ? { 'Content-Type': 'application/json' } : {}), ...init.headers },
    });
    await checkResponse(response);
    if (response.status === 204) return undefined as T;
    try { return await response.json() as T; }
    catch { throw new ProtocolError('The service returned invalid JSON.'); }
  }
  async function requestText(path: string): Promise<string> {
    const response = await fetch(endpoint(path), { headers: { Accept: 'text/markdown' }, credentials: 'same-origin' });
    await checkResponse(response);
    return response.text();
  }
  async function streamQuery(streamUrl: string, onEvent: (event: QueryEvent) => void, signal?: AbortSignal) {
    endpoint(''); // Disconnected clients cannot stream arbitrary URLs.
    const origin = globalThis.location?.origin ?? 'http://localhost';
    const apiUrl = new URL(base!, `${origin}/`);
    const url = new URL(streamUrl, `${apiUrl.href}/`);
    if (url.origin !== apiUrl.origin) throw new ProtocolError('The stream URL must use the configured service origin.');
    const response = await fetch(url.href, { headers: { Accept: 'text/event-stream' }, credentials: 'same-origin', signal });
    await checkResponse(response);
    if (!response.body) throw new ProtocolError('The query stream has no body.');
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = ''; let lastId = -1n; let activeQueryId: string | undefined; let terminal = false;
    const types = new Set(['tool.started', 'tool.completed', 'answer.delta', 'answer.completed', 'query.error', 'query.cancelled']);
    function dispatch(block: string) {
      const lines = block.split('\n');
      const dataLines: string[] = []; let sseId = ''; let eventType = '';
      for (const line of lines) {
        if (line.startsWith(':')) continue;
        const colon = line.indexOf(':');
        const field = colon === -1 ? line : line.slice(0, colon);
        const value = colon === -1 ? '' : line.slice(colon + 1).replace(/^ /, '');
        if (field === 'data') dataLines.push(value);
        else if (field === 'id') sseId = value;
        else if (field === 'event') eventType = value;
      }
      if (!dataLines.length) return;
      let data: unknown;
      try { data = JSON.parse(dataLines.join('\n')); }
      catch { throw new ProtocolError('The query stream contains invalid JSON.'); }
      if (!isObject(data)) throw new ProtocolError('The query stream event is invalid.');
      const id = typeof data.id === 'string' ? data.id : sseId;
      if (!/^\d+$/.test(id)) throw new ProtocolError('Query event IDs must be increasing decimal integers.');
      const numericId = BigInt(id);
      if (numericId <= lastId) return;
      if (sseId && sseId !== id) throw new ProtocolError('Query event IDs disagree.');
      const type = typeof data.type === 'string' ? data.type : eventType;
      if (typeof data.queryId !== 'string' || !types.has(type) || !isObject(data.payload)) {
        throw new ProtocolError('The query stream event does not match the contract.');
      }
      if (activeQueryId && activeQueryId !== data.queryId) throw new ProtocolError('The stream switched query identity.');
      activeQueryId = data.queryId; lastId = numericId;
      const event = { id, queryId: data.queryId, type, payload: data.payload } as QueryEvent;
      terminal = ['answer.completed', 'query.error', 'query.cancelled'].includes(type);
      onEvent(event);
    }
    try {
      while (!terminal) {
        const { done, value } = await reader.read();
        buffer += done ? decoder.decode() : decoder.decode(value, { stream: true });
        // Preserve trailing CR until the next chunk, which may begin with LF.
        buffer = buffer.replace(/\r\n/g, '\n');
        let boundary: number;
        while (!terminal && (boundary = buffer.indexOf('\n\n')) >= 0) {
          dispatch(buffer.slice(0, boundary)); buffer = buffer.slice(boundary + 2);
        }
        if (done) {
          if (!terminal) throw new ApiError('Connection lost before the answer completed.', 0, 'STREAM_DISCONNECTED', true);
          break;
        }
      }
    } finally {
      await reader.cancel().catch(() => undefined);
      reader.releaseLock();
    }
  }
  return {
    connected,
    listDemoCases: () => request<DemoCase[]>('/demo-cases'),
    getReplay: async (recordingId: string) => {
      const importedPath = importedRecordingPath(recordingId, 'replay');
      return validateDemoData(await request(importedPath ?? `/recordings/${encodeURIComponent(recordingId)}/replay`));
    },
    getImportedReplay: async (ingestionId: string, recordKey: string) => {
      if (!INGESTION_ID.test(ingestionId) || !/^[a-f0-9]{24}$/.test(recordKey)) {
        throw new ApiError('The imported record reference is invalid.', 0, 'INVALID_RECORD');
      }
      return validateDemoData(await request(
        `/ingestions/${encodeURIComponent(ingestionId)}/records/${encodeURIComponent(recordKey)}/replay`,
      ));
    },
    listDatasets: () => request<Dataset[]>('/datasets'),
    listRecordings: (datasetId: string) => request<Recording[]>(`/datasets/${encodeURIComponent(datasetId)}/recordings`),
    getWindow: async (recordingId: string, startSec: number, endSec: number, channelIds: string[], maxPoints: number) => {
      validateInterval(startSec, endSec); validateChannels(channelIds);
      if (!Number.isInteger(maxPoints) || maxPoints < 1) throw new ApiError('Invalid display point budget.', 0, 'INVALID_WINDOW');
      const query = new URLSearchParams({ startSec: String(startSec), endSec: String(endSec), channelIds: channelIds.join(','), maxPoints: String(maxPoints) });
      const importedPath = importedRecordingPath(recordingId, 'signals');
      return validateWindow(await request<SignalWindow>(`${importedPath ?? `/recordings/${encodeURIComponent(recordingId)}/signals`}?${query}`));
    },
    getEvents: (recordingId: string) => request<SignalEvent[]>(
      importedRecordingPath(recordingId, 'events') ?? `/recordings/${encodeURIComponent(recordingId)}/events`,
    ),
    listModels: () => connected ? request<ModelProfile[]>('/models') : Promise.resolve(DECLARED_MODELS.map(model => ({ ...model, capabilities: [...model.capabilities] }))),
    searchDatasets: (query: string) => request<DatasetSearchResult[]>(`/datasets/search?${new URLSearchParams({ query })}`),
    listApprovedSources: async (page = 1, pageSize = 10) => {
      const result = await request<unknown>(`/approved-sources?${new URLSearchParams({ page: String(page), pageSize: String(pageSize) })}`);
      if (!isApprovedSourcePage(result)) throw new ProtocolError('The approved-source list does not match the contract.');
      return result;
    },
    getApprovedSource: async (approvedSourceId: string) => {
      const result = await request<unknown>(`/approved-sources/${encodeURIComponent(approvedSourceId)}`);
      if (!isApprovedSourceDetail(result)) throw new ProtocolError('The approved-source detail does not match the contract.');
      return result;
    },
    getApprovedSourceManifest: async (approvedSourceId: string) => {
      const result = await request<unknown>(`/approved-sources/${encodeURIComponent(approvedSourceId)}/manifest`);
      if (!isApprovedManifest(result)) throw new ProtocolError('The approved manifest does not match the acquisition contract.');
      return result;
    },
    deleteApprovedSource: (approvedSourceId: string) => request<void>(
      `/approved-sources/${encodeURIComponent(approvedSourceId)}`,
      { method: 'DELETE' },
    ),
    startImport: async (approvedSourceId: string, assetIds?: string[]) => {
      const selected = assetIds ? [...assetIds].sort() : undefined;
      const idempotencyKey = `approved-source:${approvedSourceId}:${selected?.join(',') ?? 'all-data-assets'}`;
      const result = await request<unknown>('/ingestions', {
        method: 'POST', headers: { 'Idempotency-Key': idempotencyKey },
        body: JSON.stringify({ approvedSourceId, ...(selected ? { assetIds: selected } : {}) }),
      });
      if (!isImportJob(result)) throw new ProtocolError('The ingestion response does not match the job contract.');
      return result;
    },
    getImport: async (id: string) => {
      const result = await request<unknown>(`/ingestions/${encodeURIComponent(id)}`);
      if (!isImportJob(result)) throw new ProtocolError('The ingestion response does not match the job contract.');
      return result;
    },
    getImportOnboarding: async (id: string) => {
      const result = await request<unknown>(`/ingestions/${encodeURIComponent(id)}/onboarding`);
      if (!isOnboardingJobView(result)) throw new ProtocolError('The onboarding status does not match the contract.');
      return result;
    },
    resolveImportBlocker: async (id: string, resolution: HumanResolution) => {
      const result = await request<unknown>(`/ingestions/${encodeURIComponent(id)}/human-resolutions`, {
        method: 'POST', body: JSON.stringify(resolution),
      });
      if (!isOnboardingJobView(result)) throw new ProtocolError('The onboarding resolution response does not match the contract.');
      return result;
    },
    getImportAssets: async (id: string) => {
      const result = await request<unknown>(`/ingestions/${encodeURIComponent(id)}/assets`);
      if (!isAssetReceipts(result)) throw new ProtocolError('The ingestion asset receipts do not match the contract.');
      return result;
    },
    getImportForSource: async (approvedSourceId: string) => {
      const result = await request<unknown>(`/ingestions/by-source/${encodeURIComponent(approvedSourceId)}`);
      if (result !== null && !isImportJob(result)) throw new ProtocolError('The source ingestion response does not match the job contract.');
      return result;
    },
    getMappingProposals: async (id: string) => {
      const result = await request<unknown>(`/ingestions/${encodeURIComponent(id)}/mapping-proposals`);
      if (!isMappingProposals(result)) throw new ProtocolError('The mapping proposals do not match the contract.');
      return result;
    },
    confirmMapping: async (id: string, mapping: MappingSpec) => {
      const result = await request<unknown>(`/ingestions/${encodeURIComponent(id)}/mapping`, {
        method: 'PUT', body: JSON.stringify(mapping),
      });
      if (!isObject(result) || !isMappingSpec(result.mapping)) throw new ProtocolError('The confirmed mapping does not match the contract.');
      return result;
    },
    getImportReceipt: async (id: string) => {
      const result = await request<unknown>(`/ingestions/${encodeURIComponent(id)}/receipt`);
      if (!isFinalReceipt(result)) throw new ProtocolError('The ingestion receipt does not match the contract.');
      return result;
    },
    getImportedRecords: async (id: string, page = 1, pageSize = 20) => {
      const result = await request<unknown>(`/ingestions/${encodeURIComponent(id)}/records?${new URLSearchParams({ page: String(page), pageSize: String(pageSize) })}`);
      if (!isImportedRecordPage(result)) throw new ProtocolError('The imported-record list does not match the contract.');
      return result;
    },
    previewSourcingRequirements: async (input: RequirementPreviewRequest) => {
      const preview = await request<unknown>('/sourcing-requirement-previews', {
        method: 'POST', body: JSON.stringify(input),
      });
      if (!isRequirementsPreview(preview)) throw new ProtocolError('The sourcing requirement preview does not match the contract.');
      return preview;
    },
    startSourcingRun: async (input: CreateSourcingRun, idempotencyKey: string) => {
      const accepted = await request<unknown>('/sourcing-runs', {
        method: 'POST', headers: { 'Idempotency-Key': idempotencyKey }, body: JSON.stringify(input),
      });
      if (!isRunAccepted(accepted)) throw new ProtocolError('The sourcing response does not match the accepted-run contract.');
      return accepted;
    },
    getSourcingRun: async (runId: string) => {
      const run = await request<unknown>(`/sourcing-runs/${encodeURIComponent(runId)}`);
      if (!isSourcingRun(run)) throw new ProtocolError('The sourcing response does not match the run contract.');
      return run;
    },
    reviewSourcingRun: async (runId: string, review: SourcingReview) => {
      const run = await request<unknown>(`/sourcing-runs/${encodeURIComponent(runId)}/approvals`, {
        method: 'POST', body: JSON.stringify(review),
      });
      if (!isSourcingRun(run)) throw new ProtocolError('The sourcing response does not match the run contract.');
      return run;
    },
    getSourcingReport: (runId: string) => requestText(`/sourcing-runs/${encodeURIComponent(runId)}/report`),
    getSourcingManifest: async (runId: string) => {
      const manifest = await request<unknown>(`/sourcing-runs/${encodeURIComponent(runId)}/manifest`);
      if (!isSourcingManifest(manifest)) throw new ProtocolError('The sourcing response does not match the manifest contract.');
      return manifest;
    },
    startQuery: async (query: QueryRequest, signal?: AbortSignal) => {
      validateInterval(query.window.startSec, query.window.endSec); validateChannels(query.window.channelIds);
      if (!Number.isFinite(query.playheadSec) || query.window.endSec > query.playheadSec) {
        throw new ApiError('The query window cannot extend beyond the playback cursor.', 0, 'INVALID_WINDOW');
      }
      if (!query.question.trim()) throw new ApiError('Enter a question.', 0, 'INVALID_QUERY');
      if (query.mode === 'direct' && !query.modelId) throw new ApiError('Select a model for direct mode.', 0, 'MODEL_REQUIRED');
      return request<{ queryId: string; streamUrl: string }>('/queries', { method: 'POST', body: JSON.stringify(query), signal });
    },
    streamQuery,
    // Aborting a fetch closes transport only. The caller must also invoke this endpoint.
    cancelQuery: (queryId: string) => request<void>(`/queries/${encodeURIComponent(queryId)}`, { method: 'DELETE' }),
  };
}
export type Services = ReturnType<typeof createServices>;

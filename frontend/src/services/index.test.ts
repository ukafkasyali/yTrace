import { afterEach, describe, expect, it, vi } from 'vitest';
import { ApiError, createServices, ProtocolError, ServiceUnavailableError, type QueryEvent, type QueryRequest } from './index';

const query: QueryRequest = {
  mode: 'assistant', question: 'Describe this interval.', playheadSec: 7,
  window: { datasetId: 'dataset', recordingId: 'recording', startSec: 5, endSec: 7, channelIds: ['joint-1'] },
};
function event(id: string, type: QueryEvent['type'], payload: Record<string, unknown> = {}) {
  return `id: ${id}\r\ndata: ${JSON.stringify({ id, queryId: 'q-1', type, payload })}\r\n\r\n`;
}
function stream(chunks: string[]) {
  const encoder = new TextEncoder();
  return new Response(new ReadableStream({
    start(controller) { chunks.forEach(chunk => controller.enqueue(encoder.encode(chunk))); controller.close(); },
  }), { headers: { 'Content-Type': 'text/event-stream' } });
}
afterEach(() => vi.unstubAllGlobals());

describe('service contracts', () => {
  it('keeps an unconfigured service unavailable without fabricated results', async () => {
    const fetch = vi.fn(); vi.stubGlobal('fetch', fetch);
    const service = createServices();
    expect(service.connected).toBe(false);
    await expect(service.listDatasets()).rejects.toBeInstanceOf(ServiceUnavailableError);
    expect((await service.listModels()).every(model => !model.available)).toBe(true);
    expect(fetch).not.toHaveBeenCalled();
  });

  it('rejects future context and invalid intervals before network access', async () => {
    const fetch = vi.fn(); vi.stubGlobal('fetch', fetch);
    const service = createServices('/api');
    await expect(service.startQuery({ ...query, playheadSec: 6 })).rejects.toMatchObject({ code: 'INVALID_WINDOW' });
    await expect(service.startQuery({ ...query, window: { ...query.window, startSec: 8 } })).rejects.toMatchObject({ code: 'INVALID_WINDOW' });
    await expect(service.startQuery({ ...query, mode: 'direct' })).rejects.toMatchObject({ code: 'MODEL_REQUIRED' });
    expect(fetch).not.toHaveBeenCalled();
  });

  it('preserves the exact query context and uses same-origin credentials', async () => {
    const fetch = vi.fn().mockResolvedValue(Response.json({ queryId: 'q-1', streamUrl: '/api/queries/q-1/events', cacheHit: true }));
    vi.stubGlobal('fetch', fetch);
    const controller = new AbortController();
    expect((await createServices('/api').startQuery(query, controller.signal)).cacheHit).toBe(true);
    expect(fetch).toHaveBeenCalledWith('/api/queries', expect.objectContaining({
      method: 'POST', credentials: 'same-origin', body: JSON.stringify(query), signal: controller.signal,
    }));
  });

  it('maps backend error envelopes without returning sample answers', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(Response.json({
      error: { code: 'MODEL_BUSY', message: 'Another inference is running.', retryable: true, estimatedWaitMs: 12_400 },
    }, { status: 409, headers: { 'Retry-After': '13' } })));
    await expect(createServices('/api').startQuery(query)).rejects.toMatchObject({
      name: 'ApiError', status: 409, code: 'MODEL_BUSY', retryable: true,
      message: 'Another inference is running.', estimatedWaitMs: 12_400, retryAfterSeconds: 13,
    });
  });

  it('handles SSE chunk boundaries, CRLF and duplicate/out-of-order IDs', async () => {
    const text = event('1', 'answer.delta', { text: 'Joint ' }) + event('1', 'answer.delta', { text: 'duplicate' }) +
      event('0', 'answer.delta', { text: 'stale' }) + event('2', 'answer.delta', { text: '2.' }) +
      event('3', 'answer.completed', { answer: 'Joint 2.', evidence: [] }) + event('4', 'answer.delta', { text: 'late' });
    // Split into individual characters to exercise CRLF and JSON chunk boundaries.
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(stream([...text])));
    const events: QueryEvent[] = [];
    await createServices('/api').streamQuery('/api/queries/q-1/events', e => events.push(e));
    expect(events.map(e => e.id)).toEqual(['1', '2', '3']);
    expect(events[1].payload.text).toBe('2.');
  });

  it('routes backend stream paths through a configured proxy prefix', async () => {
    const fetch = vi.fn().mockResolvedValue(stream([event('1', 'answer.completed', { answer: 'done' })]));
    vi.stubGlobal('fetch', fetch);
    vi.stubGlobal('location', { origin: 'http://localhost' });

    await createServices('/api-v6').streamQuery('/api/queries/q-1/events', () => undefined);

    expect(fetch).toHaveBeenCalledWith(
      'http://localhost/api-v6/queries/q-1/events',
      expect.objectContaining({ credentials: 'same-origin' }),
    );
  });

  it('reports early disconnect and malformed stream data', async () => {
    const fetch = vi.fn().mockResolvedValueOnce(stream([event('1', 'answer.delta', { text: 'Partial' })]))
      .mockResolvedValueOnce(stream(['data: broken\n\n']));
    vi.stubGlobal('fetch', fetch);
    const service = createServices('/api');
    await expect(service.streamQuery('/api/events', () => undefined)).rejects.toMatchObject({ code: 'STREAM_DISCONNECTED', retryable: true });
    await expect(service.streamQuery('/api/events', () => undefined)).rejects.toBeInstanceOf(ProtocolError);
  });

  it('rejects streams outside the configured service origin', async () => {
    const fetch = vi.fn(); vi.stubGlobal('fetch', fetch);
    await expect(createServices('/api').streamQuery('https://untrusted.example/events', () => undefined)).rejects.toBeInstanceOf(ProtocolError);
    expect(fetch).not.toHaveBeenCalled();
  });

  it('forwards abort and performs explicit server cancellation independently', async () => {
    const controller = new AbortController(); controller.abort();
    const fetch = vi.fn().mockRejectedValueOnce(new DOMException('Aborted', 'AbortError'))
      .mockResolvedValueOnce(new Response(null, { status: 204 }));
    vi.stubGlobal('fetch', fetch);
    const service = createServices('/api');
    await expect(service.streamQuery('/api/events', () => undefined, controller.signal)).rejects.toMatchObject({ name: 'AbortError' });
    expect(fetch).toHaveBeenCalledTimes(1);
    await service.cancelQuery('q/1');
    expect(fetch).toHaveBeenLastCalledWith('/api/queries/q%2F1', expect.objectContaining({ method: 'DELETE' }));
  });

  it('preserves null gaps and rejects mismatched signal lengths', async () => {
    const window = { window: query.window, resolution: 'raw', series: [{ channelId: 'joint-1', timeSec: [5, 6], values: [1, null] }] };
    const fetch = vi.fn().mockResolvedValueOnce(Response.json(window)).mockResolvedValueOnce(Response.json({
      ...window, series: [{ channelId: 'joint-1', timeSec: [5, 6], values: [1] }],
    }));
    vi.stubGlobal('fetch', fetch);
    const service = createServices('/api');
    expect((await service.getWindow('recording', 5, 7, ['joint-1'], 500)).series[0].values[1]).toBeNull();
    await expect(service.getWindow('recording', 5, 7, ['joint-1'], 500)).rejects.toBeInstanceOf(ApiError);
  });

  it('uses an idempotency key and preserves the sourcing brief', async () => {
    const accepted = { runId: 'run-1', status: 'QUEUED', statusUrl: '/api/sourcing-runs/run-1' };
    const fetch = vi.fn().mockResolvedValue(Response.json(accepted, { status: 202 }));
    vi.stubGlobal('fetch', fetch);
    const input = { brief: 'Find public robot collision time-series data.' };
    expect(await createServices('/api').startSourcingRun(input, 'intent-1')).toEqual(accepted);
    expect(fetch).toHaveBeenCalledWith('/api/sourcing-runs', expect.objectContaining({
      method: 'POST', body: JSON.stringify(input), headers: expect.objectContaining({ 'Idempotency-Key': 'intent-1' }),
    }));
  });

  it('previews sourcing requirements before creating a run', async () => {
    const preview = {
      requirements: [{
        id: 'req_provenance', label: 'Canonical provenance', description: 'Versioned source',
        priority: 'MUST', category: 'PROVENANCE', expectedValues: [], isSystemRequired: true,
      }],
    };
    const fetch = vi.fn().mockResolvedValue(Response.json(preview));
    vi.stubGlobal('fetch', fetch);
    const input = {
      brief: 'Find public robot collision time-series data.',
      customRequirements: ['At least 200 labelled collision events'],
    };

    expect(await createServices('/api').previewSourcingRequirements(input)).toEqual(preview);
    expect(fetch).toHaveBeenCalledWith('/api/sourcing-requirement-previews', expect.objectContaining({
      method: 'POST', body: JSON.stringify(input),
    }));
  });

  it('validates sourcing runs and supports approval artifacts', async () => {
    const run = {
      runId: 'run-1', status: 'AWAITING_APPROVAL', brief: 'Find robot collision time-series data.',
      requirements: [], candidates: [], profiles: [], evidence: [], assessments: [], errors: [], reportMarkdown: '# Report',
      executionMode: 'CACHED', manifest: null, approvedCandidateId: null, excludedCandidateIds: [],
      reviewFeedback: [], reviewIterationsUsed: 0, refinementOutcomes: [],
    };
    const fetch = vi.fn()
      .mockResolvedValueOnce(Response.json(run))
      .mockResolvedValueOnce(Response.json({
        ...run, status: 'APPROVED', approvedCandidateId: 'candidate-1',
        approvedCandidateIds: ['candidate-1', 'candidate-2'],
      }))
      .mockResolvedValueOnce(new Response('# Report', { headers: { 'Content-Type': 'text/markdown' } }))
      .mockResolvedValueOnce(Response.json({
        runId: 'run-1', candidateId: 'candidate-1', name: 'Robot telemetry', canonicalUrl: 'https://zenodo.org/records/1',
        licenseId: 'cc-by-4.0', labels: ['collision'], fileExtensions: ['.mat'], evidenceIds: ['ev-1'],
        limitations: [], approvedAt: '2026-09-12T12:00:00Z',
      }));
    vi.stubGlobal('fetch', fetch);
    const service = createServices('/api');
    expect((await service.getSourcingRun('run/1')).status).toBe('AWAITING_APPROVAL');
    const reviewed = await service.reviewSourcingRun('run/1', {
      decision: 'APPROVE', candidateId: 'candidate-1', note: 'Reviewed',
    });
    expect(reviewed.approvedCandidateIds).toEqual(['candidate-1', 'candidate-2']);
    expect(fetch).toHaveBeenNthCalledWith(2, '/api/sourcing-runs/run%2F1/approvals', expect.objectContaining({
      method: 'POST', body: JSON.stringify({ decision: 'APPROVE', candidateId: 'candidate-1', note: 'Reviewed' }),
    }));
    expect(await service.getSourcingReport('run/1')).toBe('# Report');
    expect((await service.getSourcingManifest('run/1')).candidateId).toBe('candidate-1');
  });

  it('rejects malformed sourcing status responses', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(Response.json({ runId: 'run-1', status: 'DONE' })));
    await expect(createServices('/api').getSourcingRun('run-1')).rejects.toBeInstanceOf(ProtocolError);
  });

  it('rejects unsafe source URLs in sourcing artifacts', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(Response.json({
      runId: 'run-1', candidateId: 'candidate-1', name: 'Unsafe', canonicalUrl: 'javascript:alert(1)',
      licenseId: 'unknown', labels: [], fileExtensions: [], evidenceIds: [], limitations: [], approvedAt: 'now',
    })));
    await expect(createServices('/api').getSourcingManifest('run-1')).rejects.toBeInstanceOf(ProtocolError);
  });

  it('validates approved sources and starts ingestion from opaque IDs only', async () => {
    const source = {
      approvedSourceId: `src_${'1'.repeat(24)}`, name: 'Robot telemetry',
      canonicalUrl: 'https://zenodo.org/records/1', sourceKind: 'ZENODO',
      sourceRevision: '1.r1', licenseId: 'cc-by-4.0', datasetLicenseId: 'cc-by-4.0',
      codeLicenseId: null, labels: ['robot'], fileExtensions: ['.csv'], totalSizeBytes: 42,
      isAcquisitionReady: true, approvalCount: 1, latestManifestSha256: 'a'.repeat(64),
      createdAt: '2026-09-13T10:00:00Z', latestApprovedAt: '2026-09-13T10:00:00Z',
    };
    const job = {
      ingestionId: '11111111-1111-4111-8111-111111111111',
      approvedSourceId: source.approvedSourceId, manifestSha256: 'a'.repeat(64),
      sourceUrl: source.canonicalUrl, sourceKind: 'ZENODO', sourceRevision: '1.r1',
      datasetLicenseId: 'cc-by-4.0', assetIds: [`asset_${'2'.repeat(16)}`],
      jobRevision: 1, state: 'queued', message: 'Queued',
      createdAt: '2026-09-13T10:00:00Z', updatedAt: '2026-09-13T10:00:00Z',
    };
    const fetch = vi.fn()
      .mockResolvedValueOnce(Response.json({ data: [source], pagination: { page: 1, pageSize: 6, totalItems: 1, totalPages: 1 } }))
      .mockResolvedValueOnce(Response.json({ ...source, approvals: [{ sourcingRunId: 'run-1', candidateId: 'candidate-1', manifestSha256: 'a'.repeat(64), approvedAt: '2026-09-13T10:00:00Z' }] }))
      .mockResolvedValueOnce(Response.json({ schemaVersion: '1.1', assets: [{ assetId: `asset_${'2'.repeat(16)}`, name: 'signals.csv', role: 'DATA', sizeBytes: 42 }], limitations: [] }))
      .mockResolvedValueOnce(Response.json(job, { status: 202 }))
      .mockResolvedValueOnce(Response.json(job))
      .mockResolvedValueOnce(Response.json([{
        ingestionId: job.ingestionId, assetId: job.assetIds[0], providerLocator: 'zenodo:1:file',
        expectedSizeBytes: 42, sourceChecksumAlgorithm: null, sourceChecksumValue: null,
        observedSizeBytes: 42, contentSha256: 'c'.repeat(64),
        contentKey: `sha256/cc/${'c'.repeat(64)}`, acquiredAt: '2026-09-13T10:01:00Z',
      }]));
    vi.stubGlobal('fetch', fetch);
    const service = createServices('/api');

    expect((await service.listApprovedSources(1, 6)).data[0].approvedSourceId).toBe(source.approvedSourceId);
    expect((await service.getApprovedSource(source.approvedSourceId)).approvals).toHaveLength(1);
    expect((await service.getApprovedSourceManifest(source.approvedSourceId)).assets[0].role).toBe('DATA');
    await service.startImport(source.approvedSourceId, job.assetIds);
    expect(fetch).toHaveBeenNthCalledWith(4, '/api/ingestions', expect.objectContaining({
      method: 'POST', body: JSON.stringify({ approvedSourceId: source.approvedSourceId, assetIds: job.assetIds }),
      headers: expect.objectContaining({
        'Idempotency-Key': `approved-source:${source.approvedSourceId}:${job.assetIds.join(',')}`,
      }),
    }));
    expect((await service.getImport(job.ingestionId)).state).toBe('queued');
    expect((await service.getImportAssets(job.ingestionId))[0].observedSizeBytes).toBe(42);
    fetch.mockResolvedValueOnce(new Response(null, { status: 204 }));
    await service.deleteApprovedSource(source.approvedSourceId);
    expect(fetch).toHaveBeenLastCalledWith(`/api/approved-sources/${source.approvedSourceId}`, expect.objectContaining({
      method: 'DELETE',
    }));
  });

  it('rejects malformed approved-source and ingestion responses', async () => {
    const fetch = vi.fn()
      .mockResolvedValueOnce(Response.json({ data: [{ canonicalUrl: 'https://example.com' }], pagination: {} }))
      .mockResolvedValueOnce(Response.json({ ingestionId: 'job', state: 'complete' }));
    vi.stubGlobal('fetch', fetch);
    const service = createServices('/api');
    await expect(service.listApprovedSources()).rejects.toBeInstanceOf(ProtocolError);
    await expect(service.getImport('job')).rejects.toBeInstanceOf(ProtocolError);
  });

  it('surfaces orchestrator blockers and submits an explicit human resolution', async () => {
    const blocker = {
      field_path: 'signals[0].unit', semantic_status: 'unresolved',
      downstream_system: 'TimeF TimeSeriesSpec.unit_value',
      downstream_requirement: 'TimeF requires a concrete unit.', candidate: null,
      evidence_refs: ['ev_documentation_unit'], remaining_uncertainty: 'Unit is not encoded.',
    };
    const paused = {
      ingestion_id: '11111111-1111-4111-8111-111111111111',
      job_id: `job-${'1'.repeat(32)}`, status: 'NEEDS_HUMAN_RESOLUTION',
      stage: 'NEEDS_HUMAN_RESOLUTION', blockers: [blocker], artifacts: {}, error: null, result: null,
    };
    const ready = { ...paused, status: 'PENDING', stage: 'CONNECTOR_READY', blockers: [] };
    const fetch = vi.fn()
      .mockResolvedValueOnce(Response.json(paused))
      .mockResolvedValueOnce(Response.json(ready));
    vi.stubGlobal('fetch', fetch);
    const service = createServices('/api');
    const resolution = {
      field_path: blocker.field_path, value: 'newton * meter',
      approved_by: 'engineer@example.test', rationale: 'Confirmed from the signal dictionary.',
    };

    expect((await service.getImportOnboarding(paused.ingestion_id)).status).toBe('NEEDS_HUMAN_RESOLUTION');
    expect((await service.resolveImportBlocker(paused.ingestion_id, resolution)).stage).toBe('CONNECTOR_READY');
    expect(fetch).toHaveBeenNthCalledWith(
      2,
      `/api/ingestions/${paused.ingestion_id}/human-resolutions`,
      expect.objectContaining({ method: 'POST', body: JSON.stringify(resolution) }),
    );
  });

  it('validates and requests paginated imported TimeF records', async () => {
    const page = {
      datasetId: 'kuka/collision-part1', datasetVersion: '1.0.0',
      data: [{
        recordId: 'batch-01/run-01', recordKey: '1'.repeat(24), isReplayCompatible: true,
        seriesCount: 14, valueCount: 140,
        durationSeconds: 0.009, signals: ['joint_1'], annotationKeys: ['collision'],
      }],
      pagination: { page: 2, pageSize: 20, totalItems: 206, totalPages: 11 },
    };
    const fetch = vi.fn().mockResolvedValue(Response.json(page));
    vi.stubGlobal('fetch', fetch);

    const result = await createServices('/api').getImportedRecords('job/1', 2, 20);

    expect(result.data[0].recordId).toBe('batch-01/run-01');
    expect(fetch).toHaveBeenCalledWith(
      '/api/ingestions/job%2F1/records?page=2&pageSize=20',
      expect.objectContaining({ credentials: 'same-origin' }),
    );
  });

  it('routes imported replay, raw windows, and events through the receipt-bound ingestion API', async () => {
    const ingestionId = '11111111-1111-4111-8111-111111111111';
    const recordKey = '2'.repeat(24);
    const recordingId = `imported:${ingestionId}:${recordKey}`;
    const channels = Array.from({ length: 7 }, (_, index) => ({
      id: `joint_${index + 1}`, name: `Joint ${index + 1}`, unit: 'Nm', values: [index, index + 0.5],
    }));
    const replay = {
      recording: { id: recordingId, name: 'batch-01/run-01', durationSeconds: 1, sampleRateHz: 1000, displaySampleRateHz: 2, sourceUrl: '', archive: 'Imported TimeF record', channelCount: 7, eventCount: 1 },
      times: [0, 0.5], channels,
      events: [{ id: 'event-1', timeSeconds: 0.5, kind: 'publisher_annotation', label: 'collision', source: 'source.mat' }],
      detail: { startSeconds: 0, endSeconds: 1, times: [0, 0.5], channels },
    };
    const window = {
      window: { datasetId: 'kuka/collision-part1', recordingId, startSec: 0, endSec: 1, channelIds: ['joint_1'] },
      resolution: 'raw', series: [{ channelId: 'joint_1', timeSec: [0, 0.5], values: [0, 0.5] }],
    };
    const fetch = vi.fn()
      .mockResolvedValueOnce(Response.json(replay))
      .mockResolvedValueOnce(Response.json(window))
      .mockResolvedValueOnce(Response.json(replay.events));
    vi.stubGlobal('fetch', fetch);
    const service = createServices('/api');

    expect((await service.getImportedReplay(ingestionId, recordKey)).recording.id).toBe(recordingId);
    await service.getWindow(recordingId, 0, 1, ['joint_1'], 100);
    await service.getEvents(recordingId);

    const prefix = `/api/ingestions/${ingestionId}/records/${recordKey}`;
    expect(fetch).toHaveBeenNthCalledWith(1, `${prefix}/replay`, expect.any(Object));
    expect(fetch).toHaveBeenNthCalledWith(2, `${prefix}/signals?startSec=0&endSec=1&channelIds=joint_1&maxPoints=100`, expect.any(Object));
    expect(fetch).toHaveBeenNthCalledWith(3, `${prefix}/events`, expect.any(Object));
    await expect(service.getImportedReplay('not-an-ingestion', recordKey)).rejects.toMatchObject({ code: 'INVALID_RECORD' });
    expect(fetch).toHaveBeenCalledTimes(3);
  });

  it('confirms a version-bound mapping through the existing ingestion', async () => {
    const mapping = {
      schemaVersion: '1.0' as const, jobRevision: 3,
      resourceId: `res_${'1'.repeat(24)}`, resourceSha256: 'a'.repeat(64),
      layout: 'WIDE_TABLE' as const, recordSelector: null, timeSelector: 'time',
      channelSelector: null, valueSelector: null, signalSelector: null,
      sampleAxis: null, channelAxis: null,
      channels: [{ selector: 'joint', name: 'Joint', unit: 'newton * meter' }],
      annotations: [],
    };
    const fetch = vi.fn().mockResolvedValue(Response.json({
      mappingSha256: 'b'.repeat(64), mapping, created: true,
    }));
    vi.stubGlobal('fetch', fetch);

    await createServices('/api').confirmMapping('job/1', mapping);

    expect(fetch).toHaveBeenCalledWith('/api/ingestions/job%2F1/mapping', expect.objectContaining({
      method: 'PUT', body: JSON.stringify(mapping),
    }));
  });
});

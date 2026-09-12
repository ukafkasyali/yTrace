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
    const fetch = vi.fn().mockResolvedValue(Response.json({ queryId: 'q-1', streamUrl: '/api/queries/q-1/events' }));
    vi.stubGlobal('fetch', fetch);
    const controller = new AbortController();
    await createServices('/api').startQuery(query, controller.signal);
    expect(fetch).toHaveBeenCalledWith('/api/queries', expect.objectContaining({
      method: 'POST', credentials: 'same-origin', body: JSON.stringify(query), signal: controller.signal,
    }));
  });

  it('maps backend error envelopes without returning sample answers', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(Response.json({ error: { code: 'MODEL_UNAVAILABLE', message: 'Model is starting.', retryable: true } }, { status: 503 })));
    await expect(createServices('/api').startQuery(query)).rejects.toMatchObject({
      name: 'ApiError', status: 503, code: 'MODEL_UNAVAILABLE', retryable: true, message: 'Model is starting.',
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

  it('validates sourcing runs and supports approval artifacts', async () => {
    const run = {
      runId: 'run-1', status: 'AWAITING_APPROVAL', brief: 'Find robot collision time-series data.',
      requirements: [], candidates: [], profiles: [], evidence: [], assessments: [], errors: [], reportMarkdown: '# Report',
      executionMode: 'CACHED', manifest: null, approvedCandidateId: null,
      reviewFeedback: [], reviewIterationsUsed: 0,
    };
    const fetch = vi.fn()
      .mockResolvedValueOnce(Response.json(run))
      .mockResolvedValueOnce(Response.json({ ...run, status: 'APPROVED' }))
      .mockResolvedValueOnce(new Response('# Report', { headers: { 'Content-Type': 'text/markdown' } }))
      .mockResolvedValueOnce(Response.json({
        runId: 'run-1', candidateId: 'candidate-1', name: 'Robot telemetry', canonicalUrl: 'https://zenodo.org/records/1',
        licenseId: 'cc-by-4.0', labels: ['collision'], fileExtensions: ['.mat'], evidenceIds: ['ev-1'],
        limitations: [], approvedAt: '2026-09-12T12:00:00Z',
      }));
    vi.stubGlobal('fetch', fetch);
    const service = createServices('/api');
    expect((await service.getSourcingRun('run/1')).status).toBe('AWAITING_APPROVAL');
    await service.reviewSourcingRun('run/1', {
      decision: 'APPROVE', candidateId: 'candidate-1', note: 'Reviewed',
    });
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
});

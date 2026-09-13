import { useEffect, useState } from 'react';
import { ArrowUpRight, Database, History, Trash2 } from 'lucide-react';
import type {
  ApprovedSource,
  ApprovedSourceDetail,
  ApprovedManifest,
  AssetReceipt,
  FinalReceipt,
  ImportJob,
  ImportedDatasetSelection,
  MappingSpec,
  OnboardingBlocker,
  Services,
} from '../services';

type Props = { services: Services; refreshKey?: number; onDatasetReady: (dataset: ImportedDatasetSelection) => void };
const terminal = new Set(['ready', 'unsupported_format', 'needs_input', 'needs_human_resolution', 'failed']);
const errorText = (value: unknown) => value instanceof Error ? value.message : 'The request failed.';

export type ImportProgress = {
  completedAssets: number;
  totalAssets: number;
  verifiedBytes: number;
  totalBytes: number;
};

export function summarizeImportProgress(
  job: ImportJob,
  manifest: ApprovedManifest,
  receipts: AssetReceipt[],
): ImportProgress {
  const selected = new Set(job.assetIds);
  const assets = manifest.assets.filter(asset => selected.has(asset.assetId));
  const completed = receipts.filter(receipt => selected.has(receipt.assetId));
  return {
    completedAssets: completed.length,
    totalAssets: assets.length,
    verifiedBytes: completed.reduce((total, receipt) => total + receipt.observedSizeBytes, 0),
    totalBytes: assets.reduce((total, asset) => total + asset.sizeBytes, 0),
  };
}

export function IngestionProgressView({ progress }: { progress: ImportProgress | null }) {
  if (!progress) return <p className="ingestion-progress-note">Reading verified download progress…</p>;
  const percent = progress.totalBytes > 0
    ? Math.min(100, Math.floor((progress.verifiedBytes / progress.totalBytes) * 100))
    : 0;
  return <div className="ingestion-progress">
    <progress aria-label="Verified ingestion bytes" max={progress.totalBytes || 1} value={progress.verifiedBytes} />
    <p><strong>{percent}% verified</strong><span>{sizeLabel(progress.verifiedBytes)} of {sizeLabel(progress.totalBytes)} · {progress.completedAssets} of {progress.totalAssets} assets</span></p>
  </div>;
}

async function loadImportProgress(services: Services, job: ImportJob) {
  try {
    const [manifest, receipts] = await Promise.all([
      services.getApprovedSourceManifest(job.approvedSourceId),
      services.getImportAssets(job.ingestionId),
    ]);
    return summarizeImportProgress(job, manifest, receipts);
  } catch {
    return null;
  }
}

export function approvedSourceAction(job: ImportJob | null | undefined) {
  if (job?.state === 'ready') return 'View result';
  return job ? 'Resume' : 'Select assets';
}

export function terminalJobNote(state: ImportJob['state']) {
  if (state === 'unsupported_format') return 'No supported time-series resource was found. Nothing was imported.';
  if (state === 'needs_input') return 'Ingestion is paused until the missing dataset licence or mapping decision is supplied.';
  if (state === 'failed') return 'The same ingestion can be retried; a second logical job will not be created.';
  return '';
}

function HumanResolutionForm({ job, services, onResolved }: {
  job: ImportJob; services: Services; onResolved: () => void;
}) {
  const [blockers, setBlockers] = useState<OnboardingBlocker[]>(job.onboardingBlockers ?? []);
  const [values, setValues] = useState<Record<string, string>>({});
  const [approvedBy, setApprovedBy] = useState('');
  const [rationale, setRationale] = useState('');
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  useEffect(() => {
    services.getImportOnboarding(job.ingestionId).then(view => setBlockers(view.blockers))
      .catch(reason => setError(errorText(reason)));
  }, [job.ingestionId, services]);
  if (!blockers.length) return <p className="status-note">Loading human-resolution blockers…</p>;
  return <div className="human-resolution" aria-label="Human resolution required">
    <strong>NEEDS_HUMAN_RESOLUTION</strong>
    <p>Semantic truth remains unresolved. An identified human may approve an implementation-only value for TimeF.</p>
    {blockers.map(blocker => <form key={blocker.field_path} onSubmit={event => {
      event.preventDefault(); setBusy(blocker.field_path); setError('');
      services.resolveImportBlocker(job.ingestionId, {
        field_path: blocker.field_path,
        value: values[blocker.field_path]?.trim(),
        approved_by: approvedBy.trim(),
        rationale: rationale.trim(),
      }).then(view => { setBlockers(view.blockers); onResolved(); })
        .catch(reason => setError(errorText(reason))).finally(() => setBusy(''));
    }}>
      <dl><div><dt>Field</dt><dd className="mono">{blocker.field_path}</dd></div><div><dt>Semantic status</dt><dd>{blocker.semantic_status}</dd></div><div><dt>Downstream requirement</dt><dd>{blocker.downstream_requirement}</dd></div><div><dt>Candidate</dt><dd>{blocker.candidate == null ? 'None established' : JSON.stringify(blocker.candidate)}</dd></div><div><dt>Evidence</dt><dd>{blocker.evidence_refs.join(', ') || 'No bounded evidence reference'}</dd></div><div><dt>Remaining uncertainty</dt><dd>{blocker.remaining_uncertainty ?? 'None recorded'}</dd></div></dl>
      <label>Approved implementation value<input required value={values[blocker.field_path] ?? ''} onChange={event => setValues(current => ({ ...current, [blocker.field_path]: event.target.value }))} /></label>
      <label>Approved by<input required value={approvedBy} onChange={event => setApprovedBy(event.target.value)} /></label>
      <label>Rationale<textarea required value={rationale} onChange={event => setRationale(event.target.value)} /></label>
      <button className="btn btn-primary" disabled={Boolean(busy)}>{busy === blocker.field_path ? 'Approving…' : 'Approve implementation value'}</button>
    </form>)}
    {error && <p className="error-message" role="alert">{error}</p>}
  </div>;
}

export function ApprovedSourceFeedback({ connected, loading, error, empty, onRetry }: {
  connected: boolean; loading: boolean; error: string; empty: boolean; onRetry: () => void;
}) {
  if (!connected) return <p className="status-note">Configure the team API to browse approved sources.</p>;
  if (loading) return <div className="source-library-loading" role="status" aria-label="Loading approved sources"><i /><i /><i /></div>;
  if (error) return <div><p className="error-message" role="alert">{error}</p><button className="btn" onClick={onRetry}>Retry</button></div>;
  if (empty) return <div className="empty-state" role="status"><h3>No approved sources yet</h3><p>Approve a scout recommendation first. Approval stores it here but does not start ingestion.</p></div>;
  return null;
}

export function ApprovedSourcePagination({ page, totalPages, loading, onPage }: {
  page: number; totalPages: number; loading: boolean; onPage: (page: number) => void;
}) {
  if (totalPages <= 1) return null;
  return <nav className="source-pagination" aria-label="Approved source pages"><button className="btn" disabled={page <= 1 || loading} onClick={() => onPage(page - 1)}>Previous</button><span>Page {page} of {totalPages}</span><button className="btn" disabled={page >= totalPages || loading} onClick={() => onPage(page + 1)}>Next</button></nav>;
}

export function ApprovedSourceStatusWarning({ error, onRetry }: {
  error: string; onRetry: () => void;
}) {
  if (!error) return null;
  return <div><p className="error-message" role="alert">{error}</p><button className="btn" onClick={onRetry}>Retry ingestion status</button></div>;
}

export function DeleteSourceButton({ disabled, reason, onDelete }: {
  disabled: boolean; reason: string; onDelete: () => void;
}) {
  return <button className="btn btn-danger" disabled={disabled} title={reason || 'Delete approved source'} onClick={onDelete}><Trash2 size={13} aria-hidden="true" />Delete</button>;
}

export function DeleteSourceConfirmation({ busy, onConfirm, onCancel }: {
  busy: boolean; onConfirm: () => void; onCancel: () => void;
}) {
  return <div className="delete-confirmation" role="alert"><p>Delete this source from the approved library? Its sourcing-run audit remains available.</p><div><button className="btn btn-danger" disabled={busy} onClick={onConfirm}>{busy ? 'Deleting…' : 'Delete source'}</button><button className="btn" disabled={busy} onClick={onCancel}>Cancel</button></div></div>;
}

export function AssetSelectionActions({ busy, canStart, onStart, onCancel }: {
  busy: boolean; canStart: boolean; onStart: () => void; onCancel: () => void;
}) {
  return <div className="asset-selection-actions"><button className="btn btn-primary" disabled={!canStart || busy} onClick={onStart}>{busy ? 'Starting…' : 'Start ingestion once'}</button><button className="btn" disabled={busy} onClick={onCancel}>Cancel</button></div>;
}

function sizeLabel(bytes: number | null) {
  if (bytes === null) return 'Size unavailable';
  if (bytes < 1_000_000) return `${(bytes / 1_000).toFixed(1)} kB`;
  if (bytes < 1_000_000_000) return `${(bytes / 1_000_000).toFixed(1)} MB`;
  return `${(bytes / 1_000_000_000).toFixed(1)} GB`;
}

function MappingForm({ job, services, onConfirmed }: {
  job: ImportJob; services: Services; onConfirmed: () => void;
}) {
  const [candidates, setCandidates] = useState<MappingSpec[]>([]);
  const [candidate, setCandidate] = useState<MappingSpec | null>(null);
  const [issues, setIssues] = useState<string[]>([]);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    let alive = true;
    services.getMappingProposals(job.ingestionId).then(proposals => {
      if (!alive) return;
      const candidates = proposals.flatMap(item => item.candidates);
      setCandidates(candidates);
      setIssues(proposals.flatMap(item => item.issues));
      if (candidates.length > 0) {
        setCandidate(candidates[0]);
      }
    }).catch(reason => { if (alive) setError(errorText(reason)); });
    return () => { alive = false; };
  }, [job.ingestionId, services]);
  if (error) return <p className="error-message" role="alert">Could not load mapping: {error}</p>;
  if (!candidate) return <p className="status-note">{issues.length ? `Mapping needs a manual selector: ${issues.join(', ')}` : 'Loading mapping proposal…'}</p>;
  return <form className="mapping-confirmation" onSubmit={event => {
    event.preventDefault();
    setBusy(true); setError('');
    const mapping = {
      ...candidate,
      jobRevision: job.jobRevision,
    };
    services.confirmMapping(job.ingestionId, mapping).then(onConfirmed)
      .catch(reason => setError(errorText(reason))).finally(() => setBusy(false));
  }}>
    <p className="status-note">Review the proposed {candidate.layout.toLowerCase().replaceAll('_', ' ')} structure. Semantic units are resolved separately through onboarding.</p>
    {candidates.length > 1 && <label>Mapping candidate<select value={candidates.indexOf(candidate)} onChange={event => { const next = candidates[Number(event.target.value)]; if (next) setCandidate(next); }}>{candidates.map((item, index) => <option key={`${item.resourceId}:${index}`} value={index}>{item.layout.toLowerCase().replaceAll('_', ' ')} · {item.resourceId}</option>)}</select></label>}
    {error && <p className="error-message" role="alert">{error}</p>}
    <button className="btn btn-primary" disabled={busy}>{busy ? 'Confirming…' : 'Confirm structure'}</button>
  </form>;
}

export default function ApprovedSourceLibrary({ services, refreshKey = 0, onDatasetReady }: Props) {
  const [page, setPage] = useState(1);
  const [totalPages, setTotalPages] = useState(0);
  const [sources, setSources] = useState<ApprovedSource[]>([]);
  const [jobs, setJobs] = useState<Record<string, ImportJob | null>>({});
  const [progress, setProgress] = useState<Record<string, ImportProgress | null>>({});
  const [detail, setDetail] = useState<ApprovedSourceDetail | null>(null);
  const [manifest, setManifest] = useState<ApprovedManifest | null>(null);
  const [selectedAssets, setSelectedAssets] = useState<string[]>([]);
  const [receipt, setReceipt] = useState<FinalReceipt | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const [ingestionError, setIngestionError] = useState('');
  const [deleteConfirmation, setDeleteConfirmation] = useState('');
  const [revision, setRevision] = useState(0);

  useEffect(() => {
    if (!services.connected) { setLoading(false); return; }
    let alive = true; setLoading(true); setError(''); setIngestionError('');
    services.listApprovedSources(page, 6).then(async result => {
      let statusUnavailable = false;
      const pairs = await Promise.all(result.data.map(async source => {
        try {
          return [source.approvedSourceId, await services.getImportForSource(source.approvedSourceId)] as const;
        } catch {
          statusUnavailable = true;
          return [source.approvedSourceId, null] as const;
        }
      }));
      const progressPairs = await Promise.all(pairs.map(async ([sourceId, job]) => [
        sourceId,
        job?.state === 'acquiring' ? await loadImportProgress(services, job) : null,
      ] as const));
      if (!alive) return;
      setSources(result.data); setTotalPages(result.pagination.totalPages);
      setJobs(Object.fromEntries(pairs));
      setProgress(Object.fromEntries(progressPairs));
      if (statusUnavailable) setIngestionError('Approved sources loaded, but ingestion status is unavailable. Start the ingestion service on port 8002, then retry.');
    }).catch(reason => { if (alive) setError(errorText(reason)); })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [page, refreshKey, revision, services]);

  useEffect(() => {
    const active = Object.values(jobs).filter((job): job is ImportJob => Boolean(job && !terminal.has(job.state)));
    if (!active.length) return;
    const timer = setTimeout(() => {
      Promise.all(active.map(async job => {
        const updated = await services.getImport(job.ingestionId);
        return {
          job: updated,
          progress: updated.state === 'acquiring'
            ? await loadImportProgress(services, updated)
            : null,
        };
      })).then(updated => {
        setJobs(current => ({ ...current, ...Object.fromEntries(updated.map(item => [item.job.approvedSourceId, item.job])) }));
        setProgress(current => ({ ...current, ...Object.fromEntries(updated.map(item => [item.job.approvedSourceId, item.progress])) }));
      }).catch(reason => setError(`Could not refresh ingestion status: ${errorText(reason)}`));
    }, 2_000);
    return () => clearTimeout(timer);
  }, [jobs, services]);

  async function show(source: ApprovedSource) {
    setBusy(`detail-${source.approvedSourceId}`); setError(''); setReceipt(null);
    try {
      const [next, approvedManifest] = await Promise.all([
        services.getApprovedSource(source.approvedSourceId),
        services.getApprovedSourceManifest(source.approvedSourceId),
      ]);
      setDetail(next);
      setManifest(approvedManifest);
      setSelectedAssets(approvedManifest.assets.filter(asset => asset.role === 'DATA').map(asset => asset.assetId));
      const job = jobs[source.approvedSourceId];
      if (job?.state === 'ready') setReceipt(await services.getImportReceipt(job.ingestionId));
    } catch (reason) { setError(errorText(reason)); }
    finally { setBusy(''); }
  }

  async function ingest(source: ApprovedSource) {
    const existing = jobs[source.approvedSourceId];
    if (existing?.state === 'ready') { await show(source); return; }
    if (!existing) { await show(source); return; }
    setBusy(`ingest-${source.approvedSourceId}`); setError('');
    try {
      const job = existing.state === 'failed'
        ? await services.startImport(source.approvedSourceId, existing.assetIds)
        : existing;
      const refreshed = await services.getImport(job.ingestionId);
      setJobs(current => ({ ...current, [source.approvedSourceId]: refreshed }));
    } catch (reason) { setError(errorText(reason)); }
    finally { setBusy(''); }
  }

  async function startSelected() {
    if (!detail || !selectedAssets.length) return;
    setBusy(`ingest-${detail.approvedSourceId}`); setError('');
    try {
      const job = await services.startImport(detail.approvedSourceId, selectedAssets);
      setJobs(current => ({ ...current, [detail.approvedSourceId]: job }));
      if (manifest) {
        setProgress(current => ({
          ...current,
          [detail.approvedSourceId]: summarizeImportProgress(job, manifest, []),
        }));
      }
      setDetail(null); setManifest(null);
    } catch (reason) { setError(errorText(reason)); }
    finally { setBusy(''); }
  }

  function closeDetail() {
    setDetail(null); setManifest(null); setSelectedAssets([]); setReceipt(null);
  }

  async function deleteSource(source: ApprovedSource) {
    if (jobs[source.approvedSourceId] || ingestionError) return;
    setBusy(`delete-${source.approvedSourceId}`); setError('');
    try {
      await services.deleteApprovedSource(source.approvedSourceId);
      if (detail?.approvedSourceId === source.approvedSourceId) closeDetail();
      setDeleteConfirmation('');
      if (sources.length === 1 && page > 1) setPage(page - 1);
      else setRevision(value => value + 1);
    } catch (reason) { setError(errorText(reason)); }
    finally { setBusy(''); }
  }

  return <section className="workspace-section approved-library" aria-labelledby="approved-sources-title">
    <div className="section-heading"><div><p className="eyebrow">Saved after review</p><h2 id="approved-sources-title">Approved sources</h2><p>Return to a reviewed source and ingest it once, on demand.</p></div><Database size={20} aria-hidden="true" /></div>
    <ApprovedSourceFeedback connected={services.connected} loading={loading} error={error} empty={sources.length === 0} onRetry={() => setRevision(value => value + 1)} />
    <ApprovedSourceStatusWarning error={ingestionError} onRetry={() => setRevision(value => value + 1)} />
    {sources.length > 0 && <ul className="approved-source-list">{sources.map(source => {
      const job = jobs[source.approvedSourceId];
      const action = approvedSourceAction(job);
      return <li key={source.approvedSourceId}>
        <div className="approved-source-main"><div><span className="source-provider">{source.sourceKind.replace('_', ' ')}</span><h3>{source.name}</h3><p>{source.sourceRevision} · {source.datasetLicenseId ?? 'Dataset licence unresolved'} · {sizeLabel(source.totalSizeBytes)}</p></div><span className={`ingestion-state state-${job?.state ?? 'not-started'}`}>{job?.state.replace('_', ' ') ?? 'not ingested'}</span></div>
        <p className="source-formats">{source.fileExtensions.length ? source.fileExtensions.join(' · ') : 'Formats unavailable'} · approved {source.approvalCount} {source.approvalCount === 1 ? 'time' : 'times'} · latest {new Date(source.latestApprovedAt).toLocaleString()}</p>
        <div className="source-actions"><a href={source.canonicalUrl} target="_blank" rel="noopener noreferrer">Source <ArrowUpRight size={13} aria-hidden="true" /></a><button className="btn" disabled={busy === `detail-${source.approvedSourceId}`} onClick={() => void show(source)}><History size={13} aria-hidden="true" />History</button><button className="btn btn-primary" disabled={Boolean(busy) || !source.isAcquisitionReady} onClick={() => void ingest(source)}>{busy === `ingest-${source.approvedSourceId}` ? 'Opening…' : action}</button><DeleteSourceButton disabled={Boolean(busy) || Boolean(job) || Boolean(ingestionError)} reason={job ? 'This source is retained because an ingestion references it.' : ingestionError ? 'Retry ingestion status before deleting this source.' : ''} onDelete={() => setDeleteConfirmation(source.approvedSourceId)} /></div>
        {deleteConfirmation === source.approvedSourceId && <DeleteSourceConfirmation busy={busy === `delete-${source.approvedSourceId}`} onConfirm={() => void deleteSource(source)} onCancel={() => setDeleteConfirmation('')} />}
        {job && <div className="source-job" aria-live="polite"><strong>{job.message}</strong>{job.state === 'acquiring' && <IngestionProgressView progress={progress[source.approvedSourceId] ?? null} />}{job.state === 'mapping' && <MappingForm job={job} services={services} onConfirmed={() => setRevision(value => value + 1)} />}{job.state === 'needs_human_resolution' && <HumanResolutionForm job={job} services={services} onResolved={() => setRevision(value => value + 1)} />}{terminalJobNote(job.state) && <p>{terminalJobNote(job.state)}</p>}</div>}
      </li>;
    })}</ul>}
    <ApprovedSourcePagination page={page} totalPages={totalPages} loading={loading} onPage={setPage} />
    {detail && <aside className="source-detail" aria-labelledby="source-detail-title"><button className="text-button" onClick={closeDetail}>Close details</button><h3 id="source-detail-title">{detail.name}</h3><dl><div><dt>Revision</dt><dd>{detail.sourceRevision}</dd></div><div><dt>Dataset licence</dt><dd>{detail.datasetLicenseId ?? 'Unresolved'}</dd></div><div><dt>Approval history</dt><dd>{detail.approvals.length} immutable {detail.approvals.length === 1 ? 'event' : 'events'}</dd></div></dl><ol>{detail.approvals.map(event => <li key={`${event.sourcingRunId}-${event.approvedAt}`}>{new Date(event.approvedAt).toLocaleString()} · run <span className="mono">{event.sourcingRunId}</span></li>)}</ol>{!jobs[detail.approvedSourceId] && manifest && <fieldset className="asset-selection"><legend>Data assets to ingest</legend>{manifest.assets.filter(asset => asset.role === 'DATA').map(asset => <label key={asset.assetId}><input type="checkbox" checked={selectedAssets.includes(asset.assetId)} onChange={event => setSelectedAssets(current => event.target.checked ? [...current, asset.assetId] : current.filter(id => id !== asset.assetId))} /> <span>{asset.name} · {sizeLabel(asset.sizeBytes)}</span></label>)}<AssetSelectionActions busy={Boolean(busy)} canStart={selectedAssets.length > 0} onStart={() => void startSelected()} onCancel={closeDetail} /></fieldset>}{manifest?.limitations.map(limitation => <p className="status-note" key={limitation}>{limitation}</p>)}{receipt && <div className="ready-receipt"><strong>Validated TimeF result</strong><p>{receipt.output.datasetId} · {receipt.output.datasetVersion}</p><p>{receipt.validation.recordCount} records · {receipt.validation.seriesCount} series · {receipt.validation.valueCount.toLocaleString()} values</p><code>{receipt.receiptSha256}</code><button className="btn btn-primary" onClick={() => onDatasetReady({ ingestionId: receipt.ingestionId, datasetId: receipt.output.datasetId, datasetVersion: receipt.output.datasetVersion })}>Open imported dataset</button></div>}</aside>}
  </section>;
}

import { useEffect, useState } from 'react';
import { ArrowUpRight, Database, Download } from 'lucide-react';
import type { DemoData } from '../types';
import type { Dataset, ImportJob, Recording, Services } from '../services';
import DatasetScout from '../sourcing/DatasetScout';

type Props = { services: Services; data: DemoData; onOpenRecording: (record: Recording) => Promise<void> };
const stages = ['Inspect source', 'Map channels', 'Validate signals', 'Import into TimeNet'];
const errorText = (error: unknown) => error instanceof Error ? error.message : 'The request failed.';

export default function DataWorkspace({ services, data, onOpenRecording }: Props) {
  const [datasets, setDatasets] = useState<Dataset[]>([]);
  const [datasetId, setDatasetId] = useState('');
  const [recordings, setRecordings] = useState<Recording[]>([]);
  const [sourceUrl, setSourceUrl] = useState('');
  const [sourceApproved, setSourceApproved] = useState(false);
  const [job, setJob] = useState<ImportJob | null>(null);
  const [jobId, setJobId] = useState('');
  const [importStatusError, setImportStatusError] = useState('');
  const [checkingStatus, setCheckingStatus] = useState(false);
  const [statusRevision, setStatusRevision] = useState(0);
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const [catalogRevision, setCatalogRevision] = useState(0);

  useEffect(() => {
    let alive = true;
    if (services.connected) services.listDatasets().then(items => {
      if (!alive) return;
      setDatasets(items); setDatasetId(current => items.some(item => item.id === current) ? current : items[0]?.id ?? '');
    }).catch(reason => { if (alive) setError(`Could not load backend catalog: ${errorText(reason)}`); });
    return () => { alive = false; };
  }, [services, catalogRevision]);

  useEffect(() => {
    let alive = true; setRecordings([]);
    if (services.connected && datasetId) services.listRecordings(datasetId).then(items => { if (alive) setRecordings(items); })
      .catch(reason => { if (alive) setError(`Could not load recordings for this dataset: ${errorText(reason)}`); });
    return () => { alive = false; };
  }, [services, datasetId, catalogRevision]);

  useEffect(() => {
    if (!jobId || !services.connected) return;
    let alive = true; let timer: ReturnType<typeof setTimeout> | undefined;
    const poll = async () => {
      setCheckingStatus(true);
      try {
        const next = await services.getImport(jobId);
        if (!alive) return;
        setJob(next);
        setImportStatusError('');
        if (!['ready', 'needs_input', 'failed', 'cancelled'].includes(next.state)) timer = setTimeout(poll, 2000);
      } catch (reason) {
        if (alive) setImportStatusError(errorText(reason));
      } finally {
        if (alive) setCheckingStatus(false);
      }
    };
    void poll();
    return () => { alive = false; clearTimeout(timer); };
  }, [services, jobId, statusRevision]);

  async function run(action: string, work: () => Promise<void>) {
    setBusy(action); setError('');
    try { await work(); } catch (reason) { setError(errorText(reason)); } finally { setBusy(''); }
  }

  function useApprovedSource(url: string) {
    setSourceUrl(url);
    setSourceApproved(true);
    requestAnimationFrame(() => document.getElementById('ingestion-handoff')?.scrollIntoView({ behavior: 'smooth', block: 'start' }));
  }

  return <section className="workspace-content" aria-labelledby="data-title">
    <header className="workspace-heading data-heading"><div><h1 id="data-title">Data sources</h1><p>Find robot telemetry, verify its evidence, and prepare it for TimeNet.</p></div><Database size={24} aria-hidden="true" /></header>
    {error && <p className="error-message" role="alert">{error}</p>}

    <details className="recording-summary">
      <summary><span><strong>Current recording</strong>{data.recording.name}</span><small>{data.recording.channelCount} channels · {data.recording.sampleRateHz.toLocaleString('en-US')} Hz · raw {data.detail.startSeconds.toFixed(3)}–{data.detail.endSeconds.toFixed(3)} s</small></summary>
      <div className="recording-details">
        <p><strong>{data.recording.id}</strong> · Recorded telemetry with a {data.recording.displaySampleRateHz} Hz overview. Outside the raw interval, Trace only shows display-resolution data.</p>
        <div className="field-row">{data.recording.sourceUrl && <a className="btn" href={data.recording.sourceUrl} target="_blank" rel="noopener noreferrer">Source dataset <ArrowUpRight size={14} aria-hidden="true" /></a>}<a className="btn" href="https://docs.timenet.ai" target="_blank" rel="noopener noreferrer">TimeNet documentation <ArrowUpRight size={14} aria-hidden="true" /></a></div>
        <div className="table-scroll"><table className="data-table"><caption>Loaded channel mapping</caption><thead><tr><th>Channel</th><th>Signal</th><th>Unit</th><th>Source sampling</th></tr></thead><tbody>{data.channels.map(channel => <tr key={channel.id}><td>{channel.id}</td><td>{channel.name}</td><td>{channel.unit}</td><td>{data.recording.sampleRateHz} Hz</td></tr>)}</tbody></table></div>
        <p className="status-note">Publisher markers are annotations, not verified physical collision-onset times. Original archive: {data.recording.archive}.</p>
      </div>
    </details>

    <DatasetScout services={services} onUseSource={useApprovedSource} />

    {sourceApproved && <section className="workspace-section ingestion-handoff" id="ingestion-handoff" aria-labelledby="ingestion-title">
      <div className="section-heading"><div><h2 id="ingestion-title">Ingest an approved source</h2><p>The source stays reviewable before the backend validates and imports it.</p></div><Download size={20} aria-hidden="true" /></div>
      {!services.connected && <p className="status-note">Ingestion requires the team API. The current recording remains available locally.</p>}
      <form onSubmit={event => { event.preventDefault(); void run('import', async () => { const result = await services.startImport(sourceUrl); setJob(null); setImportStatusError(''); setJobId(result.ingestionId); }); }}>
        <label htmlFor="source-url">Approved source URL</label><div className="field-row"><input id="source-url" type="url" value={sourceUrl} onChange={event => setSourceUrl(event.target.value)} placeholder="Approve a source above or paste its canonical URL" disabled={!services.connected} required /><button className="btn btn-primary" disabled={!services.connected || !sourceUrl.trim() || Boolean(busy) || Boolean(jobId && (!job || !['ready', 'needs_input', 'failed', 'cancelled'].includes(job.state)))}><Download size={15} aria-hidden="true" />{busy === 'import' ? 'Starting…' : 'Start ingestion'}</button></div>
      </form>

      <div className="ingestion-progress" aria-live="polite">
        <h3>Ingestion activity</h3>
        {importStatusError && <div><p className="error-message" role="alert">Could not refresh ingestion status: {importStatusError}</p><p className="status-note">The import may still be running. Retry checks the same job and does not start another import.</p><button className="btn" type="button" disabled={checkingStatus || !services.connected} onClick={() => { setCheckingStatus(true); setStatusRevision(value => value + 1); }}>{checkingStatus ? 'Checking status…' : 'Retry status'}</button></div>}
        {job ? <><p>{importStatusError && <span className="status-note">Last known status: </span>}<strong>{job.state.replace('_', ' ')}</strong>{typeof job.progress === 'number' && Number.isFinite(job.progress) ? ` · ${job.progress}%` : ''}</p>{job.message && <p>{job.message}</p>}{job.steps && <ol>{job.steps.map((step, index) => <li key={index}>{step.label} — {step.completed ? 'Complete' : 'Pending'}</li>)}</ol>}{job.warnings?.map((warning, index) => <p className="status-note" key={index}>{warning}</p>)}{job.state === 'ready' && <button className="btn btn-primary" onClick={() => { setCatalogRevision(value => value + 1); if (job.datasetId || job.datasetIds?.[0]) setDatasetId(job.datasetId ?? job.datasetIds![0]); }}>Refresh imported recordings</button>}</> : jobId ? <p className="status-note">{importStatusError ? 'No status has been received for this import yet.' : 'Waiting for ingestion status…'}</p> : <ol className="ingestion-stages">{stages.map(stage => <li key={stage}><span aria-hidden="true" />{stage}</li>)}</ol>}
      </div>
    </section>}

    {services.connected && <details className="catalog-summary"><summary><span><strong>Available recordings</strong>Open a recording already loaded by the backend.</span><small>{recordings.length} recording{recordings.length === 1 ? '' : 's'}</small></summary><div className="catalog-details"><label htmlFor="catalog-dataset">Dataset</label><select id="catalog-dataset" value={datasetId} onChange={event => setDatasetId(event.target.value)}><option value="">Select dataset</option>{datasets.map(dataset => <option key={dataset.id} value={dataset.id}>{dataset.name}</option>)}</select>{recordings.length ? <div className="table-scroll"><table className="data-table"><thead><tr><th>Recording</th><th>Duration</th><th>Channels</th><th>Action</th></tr></thead><tbody>{recordings.map(record => <tr key={record.id}><td>{record.name}</td><td>{record.durationSec.toFixed(1)} s</td><td>{record.channels.length}</td><td><button className="btn" disabled={Boolean(busy)} onClick={() => void run(`open-${record.id}`, () => onOpenRecording(record))}>Open recording</button></td></tr>)}</tbody></table></div> : <p className="empty-state">No recordings loaded for this dataset.</p>}</div></details>}
  </section>;
}

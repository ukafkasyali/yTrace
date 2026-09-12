import { useEffect, useState } from 'react';
import { ArrowUpRight, Database, Download, Search } from 'lucide-react';
import type { DemoData } from '../types';
import type { Dataset, DatasetSearchResult, ImportJob, Recording, Services } from '../services';

type Props = { services: Services; data: DemoData; onOpenRecording: (record: Recording) => Promise<void> };
const stages = ['Inspect source', 'Map channels', 'Validate signals', 'Import into TimeNet'];
const errorText = (error: unknown) => error instanceof Error ? error.message : 'The request failed.';

export default function DataWorkspace({ services, data, onOpenRecording }: Props) {
  const [datasets, setDatasets] = useState<Dataset[]>([]);
  const [datasetId, setDatasetId] = useState('');
  const [recordings, setRecordings] = useState<Recording[]>([]);
  const [search, setSearch] = useState('');
  const [results, setResults] = useState<DatasetSearchResult[]>([]);
  const [sourceUrl, setSourceUrl] = useState('');
  const [job, setJob] = useState<ImportJob | null>(null);
  const [jobId, setJobId] = useState('');
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const [catalogRevision, setCatalogRevision] = useState(0);

  useEffect(() => {
    let alive = true;
    if (services.connected) services.listDatasets().then(items => {
      if (!alive) return;
      setDatasets(items); setDatasetId(current => items.some(item => item.id === current) ? current : items[0]?.id ?? '');
    }).catch(reason => { if (alive) setError(errorText(reason)); });
    return () => { alive = false; };
  }, [services, catalogRevision]);

  useEffect(() => {
    let alive = true; setRecordings([]);
    if (services.connected && datasetId) services.listRecordings(datasetId).then(items => { if (alive) setRecordings(items); })
      .catch(reason => { if (alive) setError(errorText(reason)); });
    return () => { alive = false; };
  }, [services, datasetId, catalogRevision]);

  useEffect(() => {
    if (!jobId || !services.connected) return;
    let alive = true; let timer: ReturnType<typeof setTimeout> | undefined;
    const poll = async () => {
      try {
        const next = await services.getImport(jobId);
        if (!alive) return;
        setJob(next);
        if (!['ready', 'needs_input', 'failed', 'cancelled'].includes(next.state)) timer = setTimeout(poll, 2000);
      } catch (reason) { if (alive) setError(errorText(reason)); }
    };
    void poll();
    return () => { alive = false; clearTimeout(timer); };
  }, [services, jobId]);

  async function run(action: string, work: () => Promise<void>) {
    setBusy(action); setError('');
    try { await work(); } catch (reason) { setError(errorText(reason)); } finally { setBusy(''); }
  }

  return <section className="workspace-content" aria-labelledby="data-title">
    <header className="workspace-heading"><div><span className="eyebrow">Data workspace</span><h1 id="data-title">From source to signal</h1><p>Inspect the loaded recording, discover datasets, and follow ingestion.</p></div><Database size={24} aria-hidden="true" /></header>
    {error && <p className="error-message" role="alert">{error}</p>}
    <section className="workspace-section">
      <h2>Loaded recording</h2>
      <p><strong>{data.recording.name}</strong> · {data.recording.id}</p>
      <p className="status-note">Real KUKA recording · {data.recording.channelCount} torque channels · {data.recording.sampleRateHz.toLocaleString()} Hz source · {data.recording.displaySampleRateHz} Hz overview</p>
      <p className="status-note">Raw detail: {data.detail.startSeconds.toFixed(3)}–{data.detail.endSeconds.toFixed(3)} s. Outside this interval, the local preview uses display-resolution data.</p>
      <div className="field-row"><a className="btn" href={data.recording.sourceUrl} target="_blank" rel="noopener noreferrer">Source dataset <ArrowUpRight size={14} aria-hidden="true" /></a><a className="btn" href="https://docs.timenet.ai" target="_blank" rel="noopener noreferrer">TimeNet documentation <ArrowUpRight size={14} aria-hidden="true" /></a></div>
      <div className="table-scroll"><table className="data-table"><caption>Loaded channel mapping</caption><thead><tr><th>Channel</th><th>Signal</th><th>Unit</th><th>Source sampling</th></tr></thead><tbody>{data.channels.map(channel => <tr key={channel.id}><td>{channel.id}</td><td>{channel.name}</td><td>{channel.unit}</td><td>{data.recording.sampleRateHz} Hz</td></tr>)}</tbody></table></div>
      <p className="status-note">Publisher markers are annotations, not verified physical collision-onset times. Original archive: {data.recording.archive}.</p>
    </section>

    <section className="workspace-section"><h2>Dataset discovery</h2>
      {!services.connected && <p className="status-note">Discovery and ingestion require the team’s backend. The loaded recording remains available locally.</p>}
      <form onSubmit={event => { event.preventDefault(); void run('search', async () => { setResults(await services.searchDatasets(search)); }); }}>
        <label htmlFor="dataset-search">Search for a dataset</label><div className="field-row"><input id="dataset-search" value={search} onChange={event => setSearch(event.target.value)} placeholder="Robot torque, contact events…" disabled={!services.connected} /><button className="btn" disabled={!services.connected || !search.trim() || Boolean(busy)}><Search size={15} aria-hidden="true" />{busy === 'search' ? 'Searching…' : 'Search'}</button></div>
      </form>
      {results.length > 0 && <ul className="source-results">{results.map(result => <li key={result.id}><strong>{result.name}</strong>{result.description && <p>{result.description}</p>}<a href={result.sourceUrl} target="_blank" rel="noopener noreferrer">View source</a><button className="btn" onClick={() => setSourceUrl(result.sourceUrl)}>Use this source</button></li>)}</ul>}
      <form onSubmit={event => { event.preventDefault(); void run('import', async () => { const result = await services.startImport(sourceUrl); setJob(null); setJobId(result.ingestionId); }); }}>
        <label htmlFor="source-url">Source URL</label><div className="field-row"><input id="source-url" type="url" value={sourceUrl} onChange={event => setSourceUrl(event.target.value)} placeholder="https://zenodo.org/records/…" disabled={!services.connected} required /><button className="btn btn-primary" disabled={!services.connected || !sourceUrl.trim() || Boolean(busy) || Boolean(jobId && (!job || !['ready', 'needs_input', 'failed', 'cancelled'].includes(job.state)))}><Download size={15} aria-hidden="true" />{busy === 'import' ? 'Starting…' : 'Start ingestion'}</button></div>
      </form>
    </section>

    <section className="workspace-section" aria-live="polite"><h2>Ingestion activity</h2>
      {job ? <><p><strong>{job.state.replace('_', ' ')}</strong>{typeof job.progress === 'number' && Number.isFinite(job.progress) ? ` · ${job.progress}%` : ''}</p>{job.message && <p>{job.message}</p>}{job.steps && <ol>{job.steps.map((step, index) => <li key={index}>{step.label} — {step.completed ? 'Complete' : 'Pending'}</li>)}</ol>}{job.warnings?.map((warning, index) => <p className="status-note" key={index}>{warning}</p>)}{job.state === 'ready' && <button className="btn btn-primary" onClick={() => { setCatalogRevision(value => value + 1); if (job.datasetId || job.datasetIds?.[0]) setDatasetId(job.datasetId ?? job.datasetIds![0]); }}>Refresh imported recordings</button>}</> : <><p className="status-note">{jobId ? 'Waiting for ingestion status…' : 'No ingestion running. Planned stages:'}</p><ol className="ingestion-stages">{stages.map(stage => <li key={stage}>{stage} <span className="status-note">— not started</span></li>)}</ol></>}
    </section>

    {services.connected && <section className="workspace-section"><h2>Backend catalog</h2><label htmlFor="catalog-dataset">Dataset</label><select id="catalog-dataset" value={datasetId} onChange={event => setDatasetId(event.target.value)}><option value="">Select dataset</option>{datasets.map(dataset => <option key={dataset.id} value={dataset.id}>{dataset.name}</option>)}</select>{recordings.length ? <div className="table-scroll"><table className="data-table"><thead><tr><th>Recording</th><th>Duration</th><th>Channels</th><th>Action</th></tr></thead><tbody>{recordings.map(record => <tr key={record.id}><td>{record.name}</td><td>{record.durationSec.toFixed(1)} s</td><td>{record.channels.length}</td><td><button className="btn" disabled={Boolean(busy)} onClick={() => void run(`open-${record.id}`, () => onOpenRecording(record))}>Open recording</button></td></tr>)}</tbody></table></div> : <p className="empty-state">No recordings loaded for this dataset.</p>}</section>}
  </section>;
}

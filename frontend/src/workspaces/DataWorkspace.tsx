import { useEffect, useState } from 'react';
import { ArrowUpRight, Database } from 'lucide-react';
import type { DemoData } from '../types';
import type { Dataset, Recording, Services } from '../services';
import ApprovedSourceLibrary from '../sourcing/ApprovedSourceLibrary';
import DatasetScout from '../sourcing/DatasetScout';

type Props = { services: Services; data: DemoData; onOpenRecording: (record: Recording) => Promise<void> };
const errorText = (error: unknown) => error instanceof Error ? error.message : 'The request failed.';

export default function DataWorkspace({ services, data, onOpenRecording }: Props) {
  const [datasets, setDatasets] = useState<Dataset[]>([]);
  const [datasetId, setDatasetId] = useState('');
  const [recordings, setRecordings] = useState<Recording[]>([]);
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const [catalogRevision, setCatalogRevision] = useState(0);
  const [approvedRevision, setApprovedRevision] = useState(0);

  useEffect(() => {
    let alive = true;
    if (services.connected) services.listDatasets().then(items => {
      if (!alive) return;
      setDatasets(items);
      setDatasetId(current => items.some(item => item.id === current) ? current : items[0]?.id ?? '');
    }).catch(reason => { if (alive) setError(`Could not load backend catalog: ${errorText(reason)}`); });
    return () => { alive = false; };
  }, [services, catalogRevision]);

  useEffect(() => {
    let alive = true; setRecordings([]);
    if (services.connected && datasetId) services.listRecordings(datasetId)
      .then(items => { if (alive) setRecordings(items); })
      .catch(reason => { if (alive) setError(`Could not load recordings for this dataset: ${errorText(reason)}`); });
    return () => { alive = false; };
  }, [services, datasetId, catalogRevision]);

  async function open(record: Recording) {
    setBusy(record.id); setError('');
    try { await onOpenRecording(record); }
    catch (reason) { setError(errorText(reason)); }
    finally { setBusy(''); }
  }

  function refreshApprovedSources() {
    setApprovedRevision(value => value + 1);
    requestAnimationFrame(() => document.getElementById('approved-source-library')?.scrollIntoView({ behavior: 'smooth', block: 'start' }));
  }

  return <section className="workspace-content" aria-labelledby="data-title">
    <header className="workspace-heading data-heading"><div><h1 id="data-title">Data sources</h1><p>Find robot telemetry, verify its evidence, and ingest an approved revision into TimeNet.</p></div><Database size={24} aria-hidden="true" /></header>
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

    <DatasetScout services={services} onUseSource={refreshApprovedSources} />
    <div id="approved-source-library"><ApprovedSourceLibrary services={services} refreshKey={approvedRevision} onDatasetReady={id => { setDatasetId(id); setCatalogRevision(value => value + 1); }} /></div>

    {services.connected && <details className="catalog-summary"><summary><span><strong>Available recordings</strong>Open a recording already loaded by the backend.</span><small>{recordings.length} recording{recordings.length === 1 ? '' : 's'}</small></summary><div className="catalog-details"><label htmlFor="catalog-dataset">Dataset</label><select id="catalog-dataset" value={datasetId} onChange={event => setDatasetId(event.target.value)}><option value="">Select dataset</option>{datasets.map(dataset => <option key={dataset.id} value={dataset.id}>{dataset.name}</option>)}</select>{recordings.length ? <div className="table-scroll"><table className="data-table"><thead><tr><th>Recording</th><th>Duration</th><th>Channels</th><th>Action</th></tr></thead><tbody>{recordings.map(record => <tr key={record.id}><td>{record.name}</td><td>{record.durationSec.toFixed(1)} s</td><td>{record.channels.length}</td><td><button className="btn" disabled={Boolean(busy)} onClick={() => void open(record)}>{busy === record.id ? 'Opening…' : 'Open recording'}</button></td></tr>)}</tbody></table></div> : <p className="empty-state">No recordings loaded for this dataset.</p>}</div></details>}
  </section>;
}

import { useEffect, useRef, useState } from 'react';
import { Database } from 'lucide-react';
import type {
  ImportedDatasetSelection,
  ImportedRecordPage,
  Services,
} from '../services';

const errorText = (error: unknown) => error instanceof Error ? error.message : 'The request failed.';

export function ImportedRecordTable({ result }: { result: ImportedRecordPage }) {
  return <div className="table-scroll"><table className="data-table imported-record-table">
    <caption>{result.pagination.totalItems.toLocaleString()} validated TimeF records</caption>
    <thead><tr><th>Record</th><th>Duration</th><th>Series</th><th>Values</th><th>Signals</th><th>Annotations</th></tr></thead>
    <tbody>{result.data.map(record => <tr key={record.recordId}>
      <td className="mono">{record.recordId}</td>
      <td>{record.durationSeconds === null ? 'Irregular axis' : `${record.durationSeconds.toFixed(3)} s`}</td>
      <td>{record.seriesCount.toLocaleString()}</td>
      <td>{record.valueCount.toLocaleString()}</td>
      <td>{record.signals.length ? record.signals.join(', ') : 'None declared'}</td>
      <td>{record.annotationKeys.length ? record.annotationKeys.join(', ') : 'None'}</td>
    </tr>)}</tbody>
  </table></div>;
}

export function ImportedRecordPagination({ result, loading, onPage }: {
  result: ImportedRecordPage; loading: boolean; onPage: (page: number) => void;
}) {
  if (result.pagination.totalPages <= 1) return null;
  return <nav className="source-pagination" aria-label="Imported record pages">
    <button className="btn" disabled={loading || result.pagination.page <= 1} onClick={() => onPage(result.pagination.page - 1)}>Previous</button>
    <span>Page {result.pagination.page} of {result.pagination.totalPages}</span>
    <button className="btn" disabled={loading || result.pagination.page >= result.pagination.totalPages} onClick={() => onPage(result.pagination.page + 1)}>Next</button>
  </nav>;
}

export default function ImportedDatasetBrowser({ selection, services, onClose }: {
  selection: ImportedDatasetSelection | null;
  services: Services;
  onClose: () => void;
}) {
  const [page, setPage] = useState(1);
  const [result, setResult] = useState<ImportedRecordPage | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [attempt, setAttempt] = useState(0);
  const heading = useRef<HTMLHeadingElement>(null);

  useEffect(() => { setPage(1); setResult(null); }, [selection?.ingestionId]);
  useEffect(() => {
    if (!selection) { setResult(null); setError(''); return; }
    let alive = true;
    setLoading(true); setError('');
    services.getImportedRecords(selection.ingestionId, page, 20)
      .then(next => { if (alive) setResult(next); })
      .catch(reason => { if (alive) setError(errorText(reason)); })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [attempt, page, selection, services]);
  useEffect(() => {
    if (!selection) return;
    heading.current?.focus();
    heading.current?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }, [selection]);

  if (!selection) return null;
  return <section className="workspace-section imported-dataset-browser" aria-labelledby="imported-dataset-title" aria-busy={loading}>
    <header className="section-heading"><div><p className="eyebrow">Validated TimeF import</p><h2 id="imported-dataset-title" ref={heading} tabIndex={-1}>{selection.datasetId}</h2><p>Version {selection.datasetVersion}. Browse persisted record metadata before choosing downstream analysis.</p></div><Database size={20} aria-hidden="true" /></header>
    <button className="text-button" onClick={onClose}>Close imported dataset</button>
    {loading && !result && <p role="status">Loading imported records…</p>}
    {loading && result && <p className="status-note" role="status">Loading record page…</p>}
    {error && <div><p className="error-message" role="alert">Could not open imported dataset: {error}</p><button className="btn" onClick={() => setAttempt(current => current + 1)}>Retry</button></div>}
    {result && result.data.length > 0 && <><ImportedRecordTable result={result} /><ImportedRecordPagination result={result} loading={loading} onPage={setPage} /></>}
    {result && result.data.length === 0 && <p className="empty-state" role="status">The validated dataset contains no records.</p>}
  </section>;
}

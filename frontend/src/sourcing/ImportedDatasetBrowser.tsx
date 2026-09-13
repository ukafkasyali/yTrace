import { useEffect, useRef, useState } from 'react';
import { Database } from 'lucide-react';
import type {
  ImportedDatasetSelection,
  ImportedRecordPage,
  Services,
} from '../services';

const errorText = (error: unknown) => error instanceof Error ? error.message : 'The request failed.';

export function ImportedRecordTable({ result, opening, onOpen }: {
  result: ImportedRecordPage;
  opening: string;
  onOpen: (recordKey: string) => void;
}) {
  return <div className="table-scroll"><table className="data-table imported-record-table">
    <caption>{result.pagination.totalItems.toLocaleString()} validated TimeF records</caption>
    <thead><tr><th>Record</th><th>Duration</th><th>Series</th><th>Values</th><th>Signals</th><th>Annotations</th><th>Action</th></tr></thead>
    <tbody>{result.data.map(record => <tr key={record.recordId}>
      <td className="mono">{record.recordId}</td>
      <td>{record.durationSeconds === null ? 'Irregular axis' : `${record.durationSeconds.toFixed(3)} s`}</td>
      <td>{record.seriesCount.toLocaleString()}</td>
      <td>{record.valueCount.toLocaleString()}</td>
      <td>{record.signals.length ? record.signals.join(', ') : 'None declared'}</td>
      <td>{record.annotationKeys.length ? record.annotationKeys.join(', ') : 'None'}</td>
      <td>{record.isReplayCompatible
        ? <button className="btn btn-primary" disabled={Boolean(opening)} onClick={() => onOpen(record.recordKey)}>{opening === record.recordKey ? 'Opening…' : 'Open in replay'}</button>
        : <span className="status-note">Metadata only</span>}</td>
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

export default function ImportedDatasetBrowser({ selection, services, onClose, onOpenRecord }: {
  selection: ImportedDatasetSelection | null;
  services: Services;
  onClose: () => void;
  onOpenRecord: (selection: ImportedDatasetSelection, recordKey: string) => Promise<void>;
}) {
  const [page, setPage] = useState(1);
  const [result, setResult] = useState<ImportedRecordPage | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [openError, setOpenError] = useState('');
  const [attempt, setAttempt] = useState(0);
  const [opening, setOpening] = useState('');
  const heading = useRef<HTMLHeadingElement>(null);

  useEffect(() => { setPage(1); setResult(null); setOpenError(''); }, [selection?.ingestionId]);
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
  const selected = selection;
  async function openRecord(recordKey: string) {
    setOpening(recordKey); setOpenError('');
    try { await onOpenRecord(selected, recordKey); }
    catch (reason) { setOpenError(errorText(reason)); }
    finally { setOpening(''); }
  }
  return <section className="workspace-section imported-dataset-browser" aria-labelledby="imported-dataset-title" aria-busy={loading || Boolean(opening)}>
    <header className="section-heading"><div><p className="eyebrow">Validated TimeF import</p><h2 id="imported-dataset-title" ref={heading} tabIndex={-1}>{selection.datasetId}</h2><p>Version {selection.datasetVersion}. Open a compatible seven-joint torque record in replay, or browse metadata for the others.</p></div><Database size={20} aria-hidden="true" /></header>
    <button className="text-button" onClick={onClose}>Close imported dataset</button>
    {loading && !result && <p role="status">Loading imported records…</p>}
    {loading && result && <p className="status-note" role="status">Loading record page…</p>}
    {error && <div><p className="error-message" role="alert">Could not open imported dataset: {error}</p><button className="btn" onClick={() => setAttempt(current => current + 1)}>Retry</button></div>}
    {openError && <p className="error-message" role="alert">Could not open imported record: {openError} Select it again to retry.</p>}
    {result && result.data.length > 0 && <><ImportedRecordTable result={result} opening={opening} onOpen={recordKey => void openRecord(recordKey)} /><ImportedRecordPagination result={result} loading={loading || Boolean(opening)} onPage={setPage} /></>}
    {result && result.data.length === 0 && <p className="empty-state" role="status">The validated dataset contains no records.</p>}
  </section>;
}

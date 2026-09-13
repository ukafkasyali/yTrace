import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it, vi } from 'vitest';
import type { ImportedRecordPage } from '../services';
import {
  ImportedRecordPagination,
  ImportedRecordTable,
} from './ImportedDatasetBrowser';

const result: ImportedRecordPage = {
  datasetId: 'kuka/collision-part1',
  datasetVersion: '1.0.0',
  data: [{
    recordId: 'batch-01/run-01',
    recordKey: '1'.repeat(24),
    isReplayCompatible: true,
    seriesCount: 14,
    valueCount: 3_248_014,
    durationSeconds: 232,
    signals: ['joint_1', 'joint_2'],
    annotationKeys: ['collision', 'source_run_id'],
  }],
  pagination: { page: 1, pageSize: 20, totalItems: 206, totalPages: 11 },
};

describe('imported dataset browser', () => {
  it('renders validated record provenance without raw arrays', () => {
    const markup = renderToStaticMarkup(<ImportedRecordTable result={result} opening="" onOpen={vi.fn()} />);
    expect(markup).toContain('206 validated TimeF records');
    expect(markup).toContain('batch-01/run-01');
    expect(markup).toContain('3,248,014');
    expect(markup).toContain('collision, source_run_id');
    expect(markup).toContain('Open in replay');
  });

  it('labels incompatible records as metadata only', () => {
    const incompatible = { ...result, data: [{ ...result.data[0], isReplayCompatible: false }] };
    const markup = renderToStaticMarkup(<ImportedRecordTable result={incompatible} opening="" onOpen={vi.fn()} />);
    expect(markup).toContain('Metadata only');
    expect(markup).not.toContain('Open in replay');
  });

  it('renders bounded accessible pagination', () => {
    const markup = renderToStaticMarkup(
      <ImportedRecordPagination result={result} loading={false} onPage={vi.fn()} />,
    );
    expect(markup).toContain('aria-label="Imported record pages"');
    expect(markup).toContain('Page 1 of 11');
    expect(markup).toContain('disabled');
  });
});

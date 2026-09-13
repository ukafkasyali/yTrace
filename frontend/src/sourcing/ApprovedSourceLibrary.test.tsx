import { renderToStaticMarkup } from 'react-dom/server';
import type { ReactNode } from 'react';
import { describe, expect, it, vi } from 'vitest';
import type { ImportJob } from '../services';
import {
  approvedSourceAction,
  AssetSelectionActions,
  ApprovedSourceFeedback,
  ApprovedSourcePagination,
  ApprovedSourceStatusWarning,
  DeleteSourceButton,
  DeleteSourceConfirmation,
  terminalJobNote,
} from './ApprovedSourceLibrary';

const render = (node: ReactNode) => renderToStaticMarkup(node);

describe('approved source library states', () => {
  it('renders disconnected, loading, empty, and protocol-error feedback accessibly', () => {
    expect(render(<ApprovedSourceFeedback connected={false} loading={false} error="" empty onRetry={vi.fn()} />)).toContain('Configure the team API');
    expect(render(<ApprovedSourceFeedback connected loading error="" empty onRetry={vi.fn()} />)).toContain('aria-label="Loading approved sources"');
    expect(render(<ApprovedSourceFeedback connected loading={false} error="Invalid response" empty onRetry={vi.fn()} />)).toContain('role="alert"');
    expect(render(<ApprovedSourceFeedback connected loading={false} error="" empty onRetry={vi.fn()} />)).toContain('No approved sources yet');
  });

  it('renders bounded pagination controls', () => {
    const markup = render(<ApprovedSourcePagination page={2} totalPages={3} loading={false} onPage={vi.fn()} />);
    expect(markup).toContain('Page 2 of 3');
    expect(markup).toContain('Previous');
    expect(markup).toContain('Next');
  });

  it('keeps ingestion status failures distinct from the approved-source catalog', () => {
    const markup = render(<ApprovedSourceStatusWarning error="Approved sources loaded, but ingestion status is unavailable." onRetry={vi.fn()} />);
    expect(markup).toContain('Approved sources loaded');
    expect(markup).toContain('Retry ingestion status');
    expect(markup).toContain('role="alert"');
  });

  it('offers the existing result instead of another ingestion when ready', () => {
    const ready = { state: 'ready' } as ImportJob;
    expect(approvedSourceAction(ready)).toBe('View result');
    expect(approvedSourceAction(null)).toBe('Select assets');
  });

  it('distinguishes paused, unsupported, and retryable failed jobs', () => {
    expect(terminalJobNote('needs_input')).toContain('paused');
    expect(terminalJobNote('unsupported_format')).toContain('Nothing was imported');
    expect(terminalJobNote('failed')).toContain('same ingestion');
    expect(terminalJobNote('ready')).toBe('');
  });

  it('offers cancellation after asset selection', () => {
    const markup = render(<AssetSelectionActions busy={false} canStart onStart={vi.fn()} onCancel={vi.fn()} />);
    expect(markup).toContain('Start ingestion once');
    expect(markup).toContain('Cancel');
  });

  it('guards source deletion and requires an explicit confirmation', () => {
    const disabled = render(<DeleteSourceButton disabled reason="This source is retained because an ingestion references it." onDelete={vi.fn()} />);
    expect(disabled).toContain('disabled');
    expect(disabled).toContain('ingestion references it');
    const confirmation = render(<DeleteSourceConfirmation busy={false} onConfirm={vi.fn()} onCancel={vi.fn()} />);
    expect(confirmation).toContain('Delete source');
    expect(confirmation).toContain('sourcing-run audit remains available');
    expect(confirmation).toContain('Cancel');
  });
});

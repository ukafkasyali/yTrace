import { renderToStaticMarkup } from 'react-dom/server';
import type { ReactNode } from 'react';
import { describe, expect, it, vi } from 'vitest';
import type { ImportJob } from '../services';
import {
  approvedSourceAction,
  ApprovedSourceFeedback,
  ApprovedSourcePagination,
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
});

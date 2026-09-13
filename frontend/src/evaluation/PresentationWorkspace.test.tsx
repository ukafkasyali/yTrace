import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import EvaluationWorkspace from './EvaluationWorkspace';
import PresentationWorkspace from './PresentationWorkspace';

describe('five-minute baseline presentation', () => {
  it('renders the opening scene and navigation', () => {
    const html = renderToStaticMarkup(<PresentationWorkspace onExit={() => undefined} />);

    expect(html).toContain('From seven torque traces to reviewable incident evidence');
    expect(html).toContain('512');
    expect(html).toContain('Next');
  });

  it('is accessible from the detailed evaluation page', () => {
    const html = renderToStaticMarkup(<EvaluationWorkspace />);

    expect(html).toContain('Open 5-minute view');
  });
});

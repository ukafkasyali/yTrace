import { describe, expect, it } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import RecordingExplorer from './RecordingExplorer';
import fixture from '../../public/data/kuka-demo.json';
import type { DemoData } from '../types';

describe('completed recording marker browser', () => {
  it('exposes every annotation even before the first marker is replayed', () => {
    const html = renderToStaticMarkup(<RecordingExplorer data={fixture as DemoData} playhead={0}
      interval={{start: 5.787, end: 6.811}} highlighted={[]} onMarker={() => {}}
      onHighlight={() => {}} onData={() => {}}/>);
    expect((html.match(/class="event-item /g) ?? []).length).toBe(fixture.events.length);
    expect(html).toContain('150.999 s');
    expect(html).not.toContain('No markers reached yet');
    expect(html).toContain('Run Analyze interval to start measurements and OpenTSLM.');
    expect(html).not.toContain('automatically runs');
  });
});

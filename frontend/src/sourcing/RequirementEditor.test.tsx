import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import type { RequirementDefinition } from '../services';
import RequirementEditor, { selectedRequirements } from './RequirementEditor';

const requirements: RequirementDefinition[] = [
  {
    id: 'req_provenance', label: 'Canonical provenance', description: 'Versioned source',
    priority: 'MUST', category: 'PROVENANCE', expectedValues: [], isSystemRequired: true,
  },
  {
    id: 'req_schema', label: 'Schema documentation', description: 'Columns are documented',
    priority: 'MUST', category: 'SCHEMA', expectedValues: [], isSystemRequired: false,
  },
  {
    id: 'req_custom_123456789abc', label: 'At least 200 events',
    description: 'Custom natural-language requirement', priority: 'MUST', category: 'OTHER',
    expectedValues: ['At least 200 events'], isSystemRequired: false,
  },
];

describe('requirement editor', () => {
  it('distinguishes fixed integrity checks from configurable requirements', () => {
    const markup = renderToStaticMarkup(<RequirementEditor
      requirements={requirements}
      priorities={{ req_provenance: 'MUST', req_schema: 'SHOULD', req_custom_123456789abc: 'MUST' }}
      onPriorityChange={() => undefined}
    />);

    expect(markup).toContain('Research contract');
    expect(markup).toContain('Always required');
    expect(markup).toContain('Priority for Schema documentation');
    expect(markup).toContain('Preferred');
    expect(markup).toContain('Custom');
  });

  it('submits must and preferred requirements but omits disabled ones', () => {
    const selected = selectedRequirements(requirements, {
      req_provenance: 'MUST', req_schema: 'DISABLED', req_custom_123456789abc: 'SHOULD',
    });

    expect(selected.map(item => item.id)).toEqual([
      'req_provenance', 'req_custom_123456789abc',
    ]);
    expect(selected[1].priority).toBe('SHOULD');
  });
});

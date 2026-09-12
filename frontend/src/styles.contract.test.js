import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

const styles = readFileSync(new URL('./styles.css', import.meta.url), 'utf8');

describe('responsive layout contracts', () => {
  it('keeps all three decision metrics in one desktop row', () => {
    expect(styles).toContain(
      '.workspace-section dl.decision-metrics{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:0',
    );
    expect(styles).toContain(
      '.workspace-section dl.decision-metrics{grid-template-columns:1fr}',
    );
  });
});

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

  it('styles actual data source result rows and scrollable evidence tables', () => {
    expect(styles).toContain('.source-results{display:flex;flex-direction:column;gap:10px;margin:12px 0;padding:0;list-style:none}');
    expect(styles).toContain('.source-results article,.source-results li,.model-result');
    expect(styles).toContain('.table-scroll{max-width:100%;overflow:auto;scrollbar-gutter:stable}');
    expect(styles).toContain('.evidence-table{min-width:720px}');
    expect(styles).toContain('.requirement-evidence-item{display:grid;grid-template-columns:minmax(90px,1fr) minmax(130px,1fr) auto');
    expect(styles).toContain('.evidence-table a{display:inline-flex;align-items:center;gap:4px;min-height:28px}');
  });
});

// MIK-R190 rule 13: the dashboard shows the `providerLimit` the server serves, and computes
// nothing about a provider limit of its own. Reading a provider's limit files, turning a used
// percentage into a limit, or parsing chat text for one would be a second answer to a question the
// server already answered — and a second answer nobody can prove agrees.
//
// This scan guards the two shapes that second answer takes in source: the file or the percentage
// it would have to read, and a place to read the served field that is not one of the surfaces that
// show it. Both are asserted over app code only; tests may name what they forbid.
import { readdirSync, readFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { describe, expect, it } from 'vitest';

const SRC = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

// Generated CSS and the served-type mirror carry no UI logic; the mirror declares the field.
const SKIPPED_DIRS = new Set(['node_modules', 'styled-system', 'dev', 'fixtures']);

const FORBIDDEN = [
  /provider-limits?\.json/i,
  /usedPct|used_pct|usedPercent|percentUsed|used_percent/i,
  /utili[sz]ation/i,
];

// The surfaces that may read the served limit, and each only shows it. The sidebar row is one of
// them: its progress mark names the served end beside the word (MIK-R190 rule 13).
const READERS = new Set([
  'panels/detail-panel/pageParts.tsx',
  'panels/detail-panel/orderGraph.tsx',
  'panels/lifecycle-list/LifecycleList.tsx',
  'panels/lifecycle-list/rowState.ts',
  'panels/sprint-graph/SprintGraphView.tsx',
  'topology/model.ts',
]);

function appFiles(dir: string): string[] {
  const found: string[] = [];
  for (const item of readdirSync(dir, { withFileTypes: true })) {
    if (item.name.startsWith('.')) continue;
    const full = path.join(dir, item.name);
    if (item.isDirectory()) {
      if (SKIPPED_DIRS.has(item.name)) continue;
      found.push(...appFiles(full));
      continue;
    }
    if (!/\.(ts|tsx)$/.test(item.name)) continue;
    if (/\.test\.(ts|tsx)$/.test(item.name)) continue;
    if (item.name === 'projection.ts') continue; // the mirror declares the served field
    found.push(path.relative(SRC, full));
  }
  return found;
}

const FILES = appFiles(SRC);

describe('the dashboard computes no provider limit of its own', () => {
  it('has app code to scan', () => {
    // A scan over nothing passes for the wrong reason.
    expect(FILES.length).toBeGreaterThan(200);
    expect(FILES).toContain('panels/detail-panel/pageParts.tsx');
  });

  it('names no limit file, no used percentage and no utilisation', () => {
    const offenders = FILES.flatMap((file) => {
      const text = readFileSync(path.join(SRC, file), 'utf8');
      return FORBIDDEN.filter((pattern) => pattern.test(text)).map(
        (pattern) => `${file}: ${pattern}`,
      );
    });
    expect(offenders).toEqual([]);
  });

  it('reads the served field only where a word is shown', () => {
    const readers = FILES.filter((file) =>
      /providerLimit|limitUntil/.test(readFileSync(path.join(SRC, file), 'utf8')),
    );
    expect(readers.sort()).toEqual([...READERS].sort());
  });
});

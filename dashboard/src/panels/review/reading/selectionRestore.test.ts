// A link's hunk selection (MIK-R89 v2 rules 9 and 13; Failure and Recovery): its trees and path are
// checked against the comparison and the lane, and its spans against the file's own classification,
// which the restore waits for. A span the file does not hold opens the page unselected, with its line.
import { describe, expect, it } from 'vitest';

import type { ReviewTreeComparison } from '../../../data/reviewTrees';
import { formatSelection, type HunkIdentity } from '../blockIdentity';
import { restoreSelection, type FileHunks, type RestoreFacts } from './selectionRestore';

const PATH = 'dashboard/src/added.ts';
const comparison = {
  number: 8,
  code_base: { tree: 'a'.repeat(40) },
  code_candidate: { tree: 'b'.repeat(40) },
  memory_base: { tree: 'c'.repeat(40) },
  memory_candidate: { tree: 'd'.repeat(40) },
} as ReviewTreeComparison;
const trees = { codeBase: 'a'.repeat(40), codeCandidate: 'b'.repeat(40) };

const hunk = (beforeStart: number, beforeCount: number, afterStart: number, afterCount: number): HunkIdentity => ({
  kind: 'hunk',
  path: PATH,
  before: { start: beforeStart, count: beforeCount },
  after: { start: afterStart, count: afterCount },
  trees,
});
const genuine = hunk(0, 0, 1, 45);
const deletion = hunk(60, 3, 105, 0);
const value = (block: HunkIdentity) => formatSelection({ kind: 'block', block });

function facts(file?: FileHunks): RestoreFacts {
  return {
    number: 8,
    comparison,
    cards: { state: 'ready', entries: [], comparison },
    catalogue: { loading: false, entries: [] },
    lane: {
      phase: 'ready',
      value: {
        state: 'measured',
        detail: '',
        unexplained_changes: {
          files: [{ path: PATH, status: 'added', content: 'text', bucket: 'unexplained', reason: '', counts: { hunks: 2, linked: 0, unexplained: 2, attribution_unknown: 0 }, unknown_reasons: [] }],
          bucket_files: 1,
          attributed_files: 0,
          hunks: 2,
          non_text: 0,
        },
        unmeasured: [],
        paths: [],
      },
    },
    ...(file ? { files: new Map([[PATH, file]]) } : {}),
  };
}

const read: FileHunks = { state: 'ready', spans: [genuine, deletion].map(({ before, after }) => ({ before, after })) };

describe('a hunk the link names', () => {
  it('waits for its file classification before it is selected', () => {
    expect(restoreSelection(value(genuine), facts())).toEqual({ state: 'wait' });
    expect(restoreSelection(value(genuine), facts({ state: 'reading' }))).toEqual({ state: 'wait' });
  });

  it('is rejected, with the line, when its span is not one of the file\'s hunks', () => {
    const forged = hunk(999999, 3, 999999, 3);
    const result = restoreSelection(value(forged), facts(read));
    expect(result.state).toBe('outside');
    expect(result.state === 'outside' && result.reason).toContain('comparison #8');
    expect(restoreSelection(value(hunk(0, 0, 1, 44)), facts(read)).state).toBe('outside');
  });

  it('restores a genuine hunk and a deletion', () => {
    expect(restoreSelection(value(genuine), facts(read))).toEqual({ state: 'select', selection: { kind: 'block', block: genuine } });
    expect(restoreSelection(value(deletion), facts(read))).toEqual({ state: 'select', selection: { kind: 'block', block: deletion } });
  });

  it('opens unselected with its reason when the file could not be read', () => {
    const result = restoreSelection(value(genuine), facts({ state: 'unavailable', reason: 'the read was refused' }));
    expect(result.state === 'outside' && result.reason).toContain('the read was refused');
  });

  it('is rejected on another comparison\'s trees before any file is read', () => {
    const foreign = { ...genuine, trees: { ...trees, codeCandidate: 'e'.repeat(40) } };
    expect(restoreSelection(value(foreign), facts()).state).toBe('outside');
  });
});

// Explicit synthetic lane/worklist rows test presentation, not classification.
import { expect, it } from 'vitest';
import { fileBadge, type ReviewChangeContext } from './reviewChangeSet';

function context(): ReviewChangeContext {
  const row = {
    path: 'a.py', status: 'modified', content: 'text', bucket: 'unexplained' as const,
    reason: 'No entry', unknown_reasons: [], counts: { hunks: 1, linked: 0, unexplained: 1, attribution_unknown: 0 },
  };
  const destination = { files: [row], bucket_files: 1, attributed_files: 0, hunks: 1, non_text: 0 };
  return {
    page: { comparison: 1, filter: 'unexplained' },
    inventory: { state: 'measured', listed_total: 1, entries: [], detail: 'synthetic',
      partial: false, command: 'synthetic', unrepresentable_paths: [] },
    lane: { phase: 'ready', value: { state: 'measured', detail: 'synthetic', changed_total: 1,
      attributed: 0, unexplained: 1, attribution_unknown: 0, unexplained_changes: destination,
      unknown_attribution: { files: [], bucket_files: 0, attributed_files: 0, hunks: 0, non_text: 0 },
      paths: [{ path: 'a.py', bucket: 'unexplained' }], unmeasured: [] } },
    worklist: { source: 'computed', state: 'complete', bound: true, items: [
      { id: 'item', kind: 'unexplained_hunk', subject: 'hunk:a.py@before..after', facts: { path: 'a.py' } },
    ], history_rows: [], changes: [], incomplete: [], item_states: { item: { state: 'answered', witness: 'counted change' } } },
  };
}
const file = { path: 'a.py', status: 'M', insertions: 1, deletions: 1 };

it('separates a no-invariant badge from answered work on the same trees', () => {
  const shown = context();
  expect(fileBadge(file, shown)).toBe('no invariant · answered');
  shown.worklist!.item_states!.item = { state: 'open', reason: 'No row' };
  expect(fileBadge(file, shown)).toBe('no invariant · 1 open');
});

it('does not claim file answers from an incomplete worklist', () => {
  const shown = context();
  shown.worklist!.state = 'incomplete';
  shown.worklist!.incomplete = [{ reason: 'Unread history' }];
  expect(fileBadge(file, shown)).toBe('no invariant');
});

it('claims no answered state while the worklist is absent or belongs to other trees', () => {
  const shown = context();
  shown.worklist!.bound = false;
  expect(fileBadge(file, shown)).toBe('no invariant');
  shown.worklist = undefined;
  expect(fileBadge(file, shown)).toBe('no invariant');
});

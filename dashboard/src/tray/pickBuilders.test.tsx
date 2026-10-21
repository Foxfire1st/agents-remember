// One reference pick of each L89 kind (MIK-R188 rule 2, evidence 1): each names its object exactly,
// as a structured reference, and carries none of the object's text. The written forms and the calls
// that open them are rendered and tested by the server (W28).
import { fireEvent, render } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { ReviewTreeComparison, ReviewTreeEntry } from '../data/reviewTrees';
import { DocHead } from '../panels/detail-panel/docHead';
import { taskEntry, taskReference, taskSummary } from '../panels/detail-panel/test-utils';
import { changeSetPick, fileLinePick, invariantPick, laneCodePick, reviewBlockPick, taskPick } from './pickBuilders';
import { EMPTY_TRAY } from './trayPersistence';
import { trayStore } from './trayStore';

const tree = (c: string) => c.repeat(40);

const comparison: ReviewTreeComparison = {
  schema: 'ar-review-tree-comparison/v1',
  task_id: '260928_maintained-invariant-knowledge',
  leaf_id: '260928-MIK-L181',
  number: 8,
  code_base: { repository: '/repos/agents-remember', tree: tree('1'), commit: tree('a') },
  code_candidate: { repository: '/repos/agents-remember', tree: tree('2'), ref: 'refs/ar/review/260928_maintained-invariant-knowledge/260928-MIK-L181/8' },
  memory_base: { repository: '/repos/ar-agents-remember', tree: tree('3'), commit: tree('b') },
  memory_candidate: { repository: '/repos/ar-agents-remember', tree: tree('4'), ref: 'refs/ar/review/260928_maintained-invariant-knowledge/260928-MIK-L181/8' },
  recorded_at: '2026-10-19T12:00:00Z',
};

const trees = { codeBase: tree('1'), codeCandidate: tree('2'), memoryBase: tree('3'), memoryCandidate: tree('4') };
const comparisonRef = {
  repository: 'agents-remember',
  task: '260928_maintained-invariant-knowledge',
  leaf: '260928-MIK-L181',
  number: 8,
  pinRef: 'refs/ar/review/260928_maintained-invariant-knowledge/260928-MIK-L181/8',
  trees,
};

const entry: ReviewTreeEntry = {
  id: 'RLZ-78JFRR',
  kind: 'realization',
  invariant: 'INV-1ZKP4R',
  invariant_key: 'INV-1ZKP4R',
  before: { recorded: true, state: 'resolved', path: 'mcp/x/turn_ended.ts', start_line: 90, end_line: 130, excerpt: 'old text' },
  after: { recorded: true, state: 'resolved', path: 'mcp/x/turn_ended.ts', start_line: 99, end_line: 144, excerpt: 'new text' },
  change: 'changed',
};

describe('a reference pick of each L89 kind', () => {
  it('names an invariant and a family by id, revision and memory tree', () => {
    expect(invariantPick({ repository: 'agents-remember', kind: 'invariant', id: 'INV-1ZKP4R', title: 'A silent turn end', revisionId: 'rev-3', memoryTree: tree('4') })).toEqual({
      kind: 'reference',
      title: 'INV-1ZKP4R · A silent turn end',
      reference: { kind: 'invariant', repository: 'agents-remember', id: 'INV-1ZKP4R', revisionId: 'rev-3', memoryTree: tree('4') },
    });
    expect(invariantPick({ repository: 'agents-remember', kind: 'family', id: 'FAM-8GST1A', title: 'One navigation', memoryTree: tree('4') })).toEqual({
      kind: 'reference',
      title: 'FAM-8GST1A · One navigation',
      reference: { kind: 'family', repository: 'agents-remember', id: 'FAM-8GST1A', memoryTree: tree('4') },
    });
  });

  it('names a review block by its rule-13 identity, the comparison and its pin ref, without its text', () => {
    const pick = reviewBlockPick({ repository: 'agents-remember', comparison, entry, invariantRevisionId: 'rev-3', symbol: 'TurnEndedTracker' });
    expect(pick).toEqual({
      kind: 'reference',
      title: 'RLZ-78JFRR · TurnEndedTracker · turn_ended.ts L99–144',
      reference: {
        kind: 'review-block',
        comparison: comparisonRef,
        block: { kind: 'entry', entry: 'RLZ-78JFRR', side: 'after', path: 'mcp/x/turn_ended.ts', range: { start: 99, end: 144 }, trees },
        invariant: 'INV-1ZKP4R',
        invariantRevisionId: 'rev-3',
      },
    });
    expect(JSON.stringify(pick)).not.toContain('new text');
  });

  it('names code without an invariant by file, or by hunk span', () => {
    expect(laneCodePick({ repository: 'agents-remember', comparison, path: 'mcp/x/index.server.ts' })).toMatchObject({
      title: 'index.server.ts · code without invariant',
      reference: { kind: 'code-without-invariant', comparison: comparisonRef, hunk: { kind: 'file', path: 'mcp/x/index.server.ts' } },
    });
    const hunk = { before: { start: 10, count: 4 }, after: { start: 10, count: 16 } };
    expect(laneCodePick({ repository: 'agents-remember', comparison, path: 'mcp/x/index.server.ts', hunk })).toMatchObject({
      title: 'index.server.ts L10–25 · code without invariant',
      reference: { hunk: { kind: 'hunk', path: 'mcp/x/index.server.ts', ...hunk, trees: { codeBase: tree('1'), codeCandidate: tree('2') } } },
    });
  });

  it('names a task document, file lines and a change set', () => {
    expect(taskPick({ reference: { repository: 'agents-remember', path: 'm/89.json' }, id: '260928-MIK-L89', title: 'The tray' })).toEqual({
      kind: 'reference',
      title: '260928-MIK-L89 · The tray',
      reference: { kind: 'task', reference: { repository: 'agents-remember', path: 'm/89.json' }, id: '260928-MIK-L89' },
    });
    expect(fileLinePick({ repository: 'agents-remember', scope: 'mainline', path: 'dashboard/src/a.ts', start: 14, end: 9 })).toEqual({
      kind: 'reference',
      title: 'a.ts L9–14 · agents-remember',
      reference: { kind: 'file-line', repository: 'agents-remember', scope: 'mainline', path: 'dashboard/src/a.ts', start: 9, end: 14 },
    });
    expect(changeSetPick({ repository: 'agents-remember', comparison })).toEqual({
      kind: 'reference',
      title: '260928-MIK-L181 @ #8 · change set',
      reference: { kind: 'change-set', comparison: comparisonRef },
    });
  });
});

describe('the task ⊕ in the document head', () => {
  beforeEach(() => trayStore.setState({ ...EMPTY_TRAY, open: false, refusal: null, outcome: null }));

  it('sits once in the head and takes the task by its document reference; a second click adds nothing', () => {
    const entry = taskEntry({ reference: taskReference('agents-remember/m/89.json'), id: 'L89', title: 'The tray' });
    const view = render(<DocHead entry={entry} doc={taskSummary({})} chain={[]} onJump={vi.fn()} openView={vi.fn()} />);
    const add = view.getByRole('button', { name: 'Add task L89 to the AR tray' });
    expect(view.getAllByTestId('doc-add-to-tray')).toHaveLength(1);
    fireEvent.click(add);
    expect(trayStore.getState().picks.map((pick) => pick.input)).toEqual([
      { kind: 'reference', title: 'L89 · The tray', reference: { kind: 'task', reference: entry.reference, id: 'L89' } },
    ]);
    const done = view.getByRole('button', { name: 'task L89 is in the AR tray' });
    expect(done.textContent).toBe('✓ in tray');
    fireEvent.click(done);
    expect(trayStore.getState().picks).toHaveLength(1);
  });
});

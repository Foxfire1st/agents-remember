import { render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import { cleanup } from '@testing-library/react';
import type { ReviewTreesRead, ReviewWorklistView } from '../../data/reviewTrees';
import { LeafWorklistView } from './LeafKnowledgeChanges';
import { fileAnswer, worklistAnswer } from './worklistState';

afterEach(cleanup);
const worklist: ReviewWorklistView = {
  source: 'computed',
  state: 'complete',
  bound: true,
  history_rows: [],
  changes: [],
  incomplete: [],
  items: [
    {
      id: 'one',
      kind: 'unexplained_hunk',
      subject: 'first',
      facts: { path: 'file.py', coverage: { state: 'covered' } },
    },
    { id: 'two', kind: 'touched_invariant', subject: 'INV-AAAAAA', facts: {} },
    { id: 'three', kind: 'reached_family', subject: 'FAM-AAAAAA', facts: {} },
  ],
  item_states: {
    one: { state: 'answered', witness: 'ROW-AAAAAA', row_id: 'ROW-AAAAAA' },
    two: { state: 'open', row_id: 'ROW-BBBBBB', reason: 'the revision moved' },
    three: { state: 'undecided', reason: 'the code tree cannot be read' },
  },
};
const read: ReviewTreesRead = {
  phase: 'trees',
  trees: {
    state: 'trees',
    repository_id: 'repo',
    master: 'master',
    leaf_id: 'leaf',
    comparison: { number: 1 } as never,
    knowledge_sides: [
      {
        side: 'before',
        state: 'legacy-unavailable',
        tree: 'a'.repeat(40),
        detail: 'This leaf predates the conversion.',
        problems: [],
      },
    ],
    worklist,
  },
};

describe('gate-owned worklist wording', () => {
  it('states the verdict on each line including stale and undecided reasons, and explains legacy sides', () => {
    render(
      <LeafWorklistView
        read={read}
        reviewComparison={1}
        onOpenChanges={() => {}}
        onOpenPath={() => {}}
      />,
    );
    expect(screen.getByText(/answered by ROW-AAAAAA/)).toBeTruthy();
    expect(screen.getByText(/row ROW-BBBBBB is stale: the revision moved/)).toBeTruthy();
    expect(screen.getByText(/undecided: the code tree cannot be read/)).toBeTruthy();
    expect(
      screen.getByText(
        /This leaf predates the conversion. The code changes of this leaf are shown in full./,
      ),
    ).toBeTruthy();
    expect(screen.queryByText(/before knowledge legacy-unavailable/)).toBeNull();
    expect(screen.getByText(/Changes without an invariant/)).toBeTruthy();
  });
  it('adds file states only for answered matching work, never claiming undecided work is answered', () => {
    expect(fileAnswer(worklist, 'file.py')).toBe('answered');
    expect(fileAnswer(worklist, 'not-listed.py')).toBeUndefined();
    expect(worklistAnswer(worklist)).toBeUndefined();
    const decided = {
      ...worklist,
      item_states: {
        ...worklist.item_states,
        three: { state: 'answered', witness: 'counted change' } as const,
      },
    };
    expect(worklistAnswer(decided)).toBe('1 open');
    expect(fileAnswer({ ...worklist, bound: false }, 'file.py')).toBeUndefined();
    expect(
      fileAnswer(
        { ...worklist, item_states: { one: { state: 'open', reason: 'no row' } } },
        'file.py',
      ),
    ).toBe('1 open');
    expect(fileAnswer({ ...worklist, item_states: {} }, 'file.py')).toBeUndefined();
  });
  it('does not mark items from a stored worklist belonging to another comparison', () => {
    const unbound = structuredClone(read);
    if (unbound.phase === 'trees' && unbound.trees.worklist) unbound.trees.worklist.bound = false;
    render(
      <LeafWorklistView
        read={unbound}
        reviewComparison={1}
        onOpenChanges={() => {}}
        onOpenPath={() => {}}
      />,
    );
    expect(screen.queryByText(/answered by/)).toBeNull();
    expect(screen.queryByText(/is stale/)).toBeNull();
    expect(screen.getAllByText(/not bound to this comparison/).length).toBeGreaterThan(0);
  });
});

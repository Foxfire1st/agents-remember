import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi, afterEach } from 'vitest';
import { cleanup } from '@testing-library/react';
import { FamilyTree } from './FamilyTree';
import { codeChangesSummary, CodeFactsScrollAnchor, codeFacts } from './codeFacts';
import { outlineGroups } from './changeTriage';
import { useRef, useLayoutEffect } from 'react';
import type { ReviewTreesRead } from '../../data/reviewTrees';
import type { ReviewOutline } from '../../data/review';
import { treeViewStore } from './triageViewPreference';

afterEach(cleanup);
const outline: ReviewOutline = {
  state: 'outlined',
  detail: '',
  families: [
    {
      id: 'FAM-AAAAAA',
      selector_id: 'family',
      title: 'A family',
      change: 'unchanged',
      members: [
        { id: 'INV-AAAAAA', selector_id: 'one', membership: 'both' },
        { id: 'INV-BBBBBB', selector_id: 'two', membership: 'both' },
      ],
    },
  ],
  invariants: [
    { id: 'INV-AAAAAA', selector_id: 'one', summary: 'Selected', change: 'changed' },
    { id: 'INV-BBBBBB', selector_id: 'two', summary: 'Code only', change: 'unchanged' },
  ],
};
const facts: ReviewTreesRead = {
  phase: 'trees',
  trees: {
    state: 'trees',
    repository_id: 'repo',
    master: 'master',
    leaf_id: 'leaf',
    knowledge_sides: [],
    comparison: { number: 1 } as never,
    code_changes: {
      state: 'facts',
      invariants: [
        {
          invariant: 'INV-AAAAAA',
          invariant_key: 'one',
          implementation: 'established',
          proof: false,
          range_unresolved: false,
          evidence: [],
          unknown_reasons: [],
        },
        {
          invariant: 'INV-BBBBBB',
          invariant_key: 'two',
          implementation: 'established',
          proof: true,
          range_unresolved: false,
          evidence: [],
          unknown_reasons: [],
        },
      ],
    },
  },
};

function AnchorShift({ loaded }: { loaded: boolean }) {
  const node = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    // Simulate native scroll anchoring as newly visible rows are inserted above the reader.
    const container = node.current?.closest<HTMLElement>('[data-testid="scroll-parent"]');
    if (loaded && container) container.scrollTop = 999;
  }, [loaded]);
  return <div ref={node}>row</div>;
}
function ScrollProbe({ loaded }: { loaded: boolean }) {
  const root = useRef<HTMLElement>(null);
  return (
    <div data-testid="scroll-parent">
      <section ref={root}>
        <CodeFactsScrollAnchor root={root} count={loaded ? 1 : undefined} comparison={1}>
          <AnchorShift loaded={loaded} />
        </CodeFactsScrollAnchor>
      </section>
    </div>
  );
}

describe('leaf-wide code facts', () => {
  it('does not relabel unknown recorded meaning as a code-only change', () => {
    const unknown = structuredClone(outline);
    unknown.invariants[1].change = 'unknown';
    const groups = outlineGroups(unknown, [], [], 'triage', codeFacts(facts, 1));
    const row = groups
      .flatMap((group) => group.members)
      .find((member) => member.selectorId === 'two');
    expect(row?.badge).toBe('unknown');
    expect(codeChangesSummary(unknown, facts, 1)).toBe('0 code changes');
  });
  it('preserves the real ancestor scroll container across native anchoring on facts arrival', () => {
    const view = render(<ScrollProbe loaded={false} />);
    const container = screen.getByTestId('scroll-parent');
    container.scrollTop = 83;
    view.rerender(<ScrollProbe loaded />);
    expect(container.scrollTop).toBe(83);
  });
  it('adds an unread member without moving the selected node, focus or scroll; traversal reaches it', () => {
    treeViewStore.getState().setView('changed');
    const select = vi.fn();
    const props = {
      outline,
      context: { entries: [] } as never,
      selection: null,
      subject: { kind: 'invariant', id: 'one' } as const,
      query: '',
      onQuery: vi.fn(),
      onSubjectSelect: select,
      reviewComparison: 1,
    };
    const { rerender } = render(
      <div data-kbzone="review">
        <FamilyTree {...props} treeRead={{ phase: 'loading' }} />
      </div>,
    );
    const selected = screen.getByRole('button', { name: 'INV-AAAAAA Selected changed' });
    selected.focus();
    const list = screen.getByTestId('review-family-list');
    list.scrollTop = 83;
    expect(screen.queryByText('code changed')).toBeNull();
    rerender(
      <div data-kbzone="review">
        <FamilyTree {...props} treeRead={facts} />
      </div>,
    );
    expect(document.activeElement).toBe(selected);
    expect(selected.getAttribute('aria-current')).toBe('true');
    expect(list.scrollTop).toBe(83);
    const changed = screen.getByRole('button', { name: 'INV-BBBBBB Code only code changed' });
    expect(changed.dataset.changePrimary).toBe('code changed');
    expect(screen.getByText('2 changed')).toBeTruthy();
    expect(screen.getByText('1 code changes found.')).toBeTruthy();
    // Later traversal updates must not announce the earlier code-facts notice again.
    expect(screen.queryByTestId('review-change-status')?.textContent ?? '').toBe('');
    expect(screen.getByTestId('review-code-facts-status').getAttribute('aria-live')).toBe('polite');
    fireEvent.keyDown(selected, { key: 'j', code: 'KeyJ' });
    expect(select).toHaveBeenCalledWith({ kind: 'invariant', id: 'two' }, expect.anything());
  });
  it('does not use facts of another comparison and distinguishes missing and unavailable', () => {
    expect(codeChangesSummary(outline, { phase: 'loading' }, 1)).toBe('…');
    expect(codeChangesSummary(outline, facts, 2)).toBe('…');
    expect(codeChangesSummary(outline, facts, 1)).toBe('1 code changes');
    const unavailable = structuredClone(facts);
    if (unavailable.phase === 'trees')
      unavailable.trees.code_changes = {
        state: 'unavailable',
        detail: 'missing code tree',
        invariants: [],
      };
    expect(codeChangesSummary(outline, unavailable, 1)).toBe('unavailable');
  });
});

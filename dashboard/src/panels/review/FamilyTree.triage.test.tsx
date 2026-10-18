// R86 outline navigation; explicit synthetic index facts exercise each display branch.
import { cleanup, fireEvent, render } from '@testing-library/react';
import { useState } from 'react';
import { afterEach, beforeEach, expect, it } from 'vitest';
import type { ReviewFamilyContext, ReviewOutline } from '../../data/review';
import { FamilyTree } from './FamilyTree';
import { initialSubject, ReviewNavigation, type ReviewSubject } from './ReviewNavigation';
import { outlineCounts, outlineGroups } from './changeTriage';
import { readTreeView, TREE_VIEW_KEY, treeViewStore } from './triageViewPreference';
import { treeOrderStore } from './triageOrderPreference';

const context: ReviewFamilyContext = {
  state: 'no_subject_selected',
  detail: 'No subject.',
  entries: [],
  families_total: 0,
  families_returned: 0,
  families_remaining: 0,
  membership_rows_total: 0,
  unique_member_revision_total: 0,
  limitations: [],
  references: {
    relationship_union: '',
    source_inventory: '',
    join_key: '',
    detail: '',
  },
};
const outline: ReviewOutline = {
  state: 'outlined',
  detail: 'Both indexes read.',
  families: [
    {
      selector_id: 'f1',
      id: 'FAM-1',
      title: 'first-family',
      change: 'unchanged',
      members: [
        { selector_id: 'i1', id: 'INV-1', membership: 'both' },
        { selector_id: 'i2', id: 'INV-2', membership: 'both' },
        { selector_id: 'i3', id: 'INV-3', membership: 'joined' },
      ],
    },
    {
      selector_id: 'f2',
      id: 'FAM-2',
      title: 'Next family',
      change: 'changed',
      members: [{ selector_id: 'i4', id: 'INV-4', membership: 'left' }],
    },
  ],
  invariants: [
    { selector_id: 'i1', id: 'INV-1', summary: 'Same recorded meaning', change: 'unchanged' },
    { selector_id: 'i2', id: 'INV-2', summary: 'Unreadable meaning', change: 'unknown' },
    { selector_id: 'i3', id: 'INV-3', summary: 'Added to this family', change: 'unchanged' },
    { selector_id: 'i4', id: 'INV-4', summary: 'Removed from this family', change: 'unchanged' },
    {
      selector_id: 'i5',
      id: 'INV-5',
      summary: 'No family lists this new meaning',
      change: 'added',
    },
  ],
};
function Harness({
  value = outline,
  selected,
  kept = false,
}: {
  value?: ReviewOutline;
  selected?: ReviewSubject;
  kept?: boolean;
}) {
  const [subject, setSubject] = useState(selected);
  const [query, setQuery] = useState('');
  return (
    <div data-kbzone="review">
      <FamilyTree
        context={context}
        outline={value}
        subject={subject}
        selection={subject?.kind === 'family' ? { familyId: subject.id } : null}
        query={query}
        onQuery={setQuery}
        onSubjectSelect={setSubject}
        walked={kept ? { entries: [], kept: new Map([['f1', 'INV-earlier']]) } : undefined}
        catalogueEntries={[
          { selector_kind: 'family', selector_id: 'f1', label: 'First family', presence: 'both' },
          {
            selector_kind: 'invariant',
            selector_id: 'i1',
            label: 'INV-1 summary',
            presence: 'both',
          },
        ]}
      />
    </div>
  );
}
beforeEach(() => {
  treeViewStore.getState().setView('changed');
  treeOrderStore.getState().setOrder('triage');
});
afterEach(cleanup);
it('draws compact rows with one badge, no record texts or disclosures, and persists both views', () => {
  const view = render(<Harness />);
  expect(view.container.querySelectorAll('details')).toHaveLength(0);
  expect(
    view.getAllByTestId('review-family-member-open').map((row) => row.dataset.subjectId),
  ).toEqual(['i4', 'i3', 'i5']);
  expect(view.getAllByTestId('review-family-open').map((row) => row.textContent)).toEqual([
    'Next familychanged',
    'first family1 changed',
  ]);
  fireEvent.click(view.getByTestId('review-tree-view'));
  expect(view.getAllByTestId('review-family-member-open')).toHaveLength(5);
  expect(localStorage.getItem(TREE_VIEW_KEY)).toBe('all');
  expect(view.getByRole('button', { name: 'INV-2 Unreadable meaning unknown' }).title).toBe(
    'INV-2 Unreadable meaning',
  );
  expect(
    readTreeView({
      getItem: () => {
        throw new Error('denied');
      },
    }),
  ).toBe('changed');
  expect(readTreeView(undefined)).toBe('changed');
});
it('opens on the first real change, respects named subjects and selects none without a real change', () => {
  expect(initialSubject({}, [], outline)).toEqual({ kind: 'family', id: 'f2' });
  expect(initialSubject({}, [], outline, 'authored')).toEqual({ kind: 'invariant', id: 'i3' });
  expect(initialSubject({ selectorKind: 'invariant', selectorId: 'i2' }, [], outline)).toEqual({
    kind: 'invariant',
    id: 'i2',
  });
  const unchanged: ReviewOutline = {
    ...outline,
    families: [],
    invariants: outline.invariants.filter(
      (row) => row.change === 'unchanged' || row.change === 'unknown',
    ),
  };
  expect(initialSubject({}, [], unchanged)).toBeUndefined();
  const view = render(<Harness value={unchanged} />);
  expect(view.getByText('This leaf changed no recorded meaning.')).toBeTruthy();
});
it('keeps selected and walked families across view changes, filters titles and summaries and skips unknown in j/k', () => {
  const view = render(<Harness selected={{ kind: 'family', id: 'f1' }} kept />);
  expect(view.getByTestId('review-family-kept').textContent).toBe('kept');
  expect(
    view.getByRole('button', { name: 'first family 1 changed' }).getAttribute('aria-describedby'),
  ).toBeTruthy();
  fireEvent.click(view.getByTestId('review-tree-view'));
  const unknown = view.getByRole('button', { name: 'INV-2 Unreadable meaning unknown' });
  unknown.focus();
  fireEvent.keyDown(unknown, { key: 'j', code: 'KeyJ' });
  expect((document.activeElement as HTMLElement).dataset.subjectId).toBe('i5');
  fireEvent.keyDown(document.activeElement!, { key: 'k', code: 'KeyK' });
  expect((document.activeElement as HTMLElement).dataset.subjectId).toBe('i3');
  fireEvent.change(view.getByTestId('review-family-filter'), { target: { value: 'Next family' } });
  expect(view.getAllByTestId('review-family-open')).toHaveLength(1);
  expect(view.getAllByTestId('review-family-member-open')).toHaveLength(1);
  fireEvent.change(view.getByTestId('review-family-filter'), { target: { value: 'new meaning' } });
  expect(view.getAllByTestId('review-family-member-open')[0].dataset.subjectId).toBe('i5');
});
it('lists catalogue rows under All without an outline and reports loading, known and unavailable counts', () => {
  const unavailable: ReviewOutline = {
    state: 'unavailable',
    detail: 'Memory side unreadable.',
    families: [],
    invariants: [],
  };
  const view = render(<Harness value={unavailable} />);
  expect(view.getByTestId('review-tree-view').textContent).toBe('View: All');
  expect(view.getAllByTestId('review-family-open')).toHaveLength(1);
  expect(view.getAllByTestId('review-family-member-open')).toHaveLength(1);
  expect(view.queryByTestId('review-outline-badge')).toBeNull();
  cleanup();
  const read = { loading: false, entries: [], outline, refresh: () => undefined };
  const navigation = render(
    <ReviewNavigation
      catalogue={read}
      onSelect={() => undefined}
      summaryCounts={{ unexplained: 7, unknown: 2 }}
    />,
  );
  expect(navigation.getByTestId('review-navigation-summary').textContent).toBe(
    '2 families changed · 1 invariants changed · 7 files without an invariant · 2 files of unknown attribution',
  );
  navigation.rerender(
    <ReviewNavigation
      catalogue={{ ...read, loading: true }}
      onSelect={() => undefined}
      summaryCounts={{ unexplained: '…', unknown: '…' }}
    />,
  );
  expect(navigation.getByTestId('review-navigation-summary').textContent).toContain(
    '… families changed',
  );
  navigation.rerender(
    <ReviewNavigation
      catalogue={{ ...read, outline: unavailable }}
      onSelect={() => undefined}
      summaryCounts={{ unexplained: 'unavailable', unknown: 'unavailable' }}
    />,
  );
  expect(navigation.getByTestId('review-outline-unavailable').textContent).toBe(unavailable.detail);
  expect(outlineCounts(outline)).toEqual({ families: 2, invariants: 1 });
  expect(outlineGroups(outline, [], [], 'authored')[0].members.map((row) => row.badge)).toEqual([
    'unchanged',
    'unknown',
    'joined',
  ]);
});

it('announces a comparison reset in the conditional polite status line', () => {
  const view = render(
    <FamilyTree
      onSubjectSelect={() => undefined}
      context={context}
      outline={outline}
      selection={null}
      query=""
      onQuery={() => undefined}
      walked={{
        entries: [],
        kept: new Map(),
        notice: 'The comparison changed; the family walk started again.',
      }}
    />,
  );
  const status = view.getByTestId('review-change-status');
  expect(status.getAttribute('aria-live')).toBe('polite');
  expect(status.textContent).toContain('comparison changed');
  view.rerender(
    <FamilyTree
      onSubjectSelect={() => undefined}
      context={context}
      outline={outline}
      selection={null}
      query=""
      onQuery={() => undefined}
      walked={{ entries: [], kept: new Map() }}
    />,
  );
  expect(view.queryByTestId('review-change-status')).toBeNull();
});

import { createRef } from 'react';
import { cleanup, fireEvent, render, within } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';

import type { ReviewOutline } from '../../../data/review';
import { changeSetModel } from '../changeSetOrder';
import { cardsOf, l181Answer, l181Entries, l181Inputs, L181_SUBJECT } from '../changeSetOrder.test-utils';
import type { ReadingContextValue } from './readingContext';
import { ReadingColumn, type ReadingColumnProps } from './ReadingColumn';

vi.mock('../ExpressionCards', async (original) => ({
  ...await original<typeof import('../ExpressionCards')>(),
  ReviewBlockCard: ({ node }: { node: { subject: string } }) => <div data-testid="focused-code-card" data-subject={node.subject} />,
}));

afterEach(cleanup);

const OTHER = 'other-invariant';
const FAMILY = 'family-one';
const SECOND_FAMILY = 'family-two';
const outline: ReviewOutline = {
  state: 'outlined', detail: 'Two test families share one invariant.',
  families: [
    { selector_id: FAMILY, id: 'FAM-ONE', title: 'First guarantee', change: 'changed', members: [
      { selector_id: L181_SUBJECT, id: 'INV-1ZKP4R', membership: 'both' },
      { selector_id: OTHER, id: 'INV-OTHER', membership: 'joined' },
    ] },
    { selector_id: SECOND_FAMILY, id: 'FAM-TWO', title: 'Second guarantee', change: 'changed', members: [
      { selector_id: L181_SUBJECT, id: 'INV-1ZKP4R', membership: 'both' },
    ] },
  ],
  invariants: [
    { selector_id: L181_SUBJECT, id: 'INV-1ZKP4R', summary: 'The selected invariant', change: 'changed', meaning: { state: 'steps', labels: ['clarify'] } },
    { selector_id: OTHER, id: 'INV-OTHER', summary: 'A second invariant', change: 'added' },
  ],
};

function model() {
  const first = l181Entries().find((entry) => entry.change === 'changed')!;
  const second = { ...structuredClone(first), id: 'other-entry', invariant_key: OTHER, invariant: 'INV-OTHER' };
  return changeSetModel(l181Inputs({ outline, cards: cardsOf(l181Answer, [first, second]) }), 'authored', 'all', { selection: null });
}

function props(): ReadingColumnProps {
  const context: ReadingContextValue = {
    place: { repo: 'agents-remember', master: 'master', leaf: 'leaf', repository: 'agents-remember' },
    comparison: l181Answer.comparison!, payload: null, planning: new Map(), layout: 'inline', collapse: true,
    sort: 'authored', selection: null, selectedBlock: null, openGroups: new Set(),
    toggleGroup: vi.fn(), select: vi.fn(), slot: vi.fn(() => null), familyMembers: new Map(),
  };
  return {
    scroller: createRef<HTMLDivElement>(), context, model: model(), focusedSubject: undefined,
    notes: <p>Page notes</p>, summary: <p>Comparison summary</p>, lane: <p>Selected lane</p>,
    canvas: null, details: <p>Technical details</p>,
  };
}

it('opens with compact family summaries and no code cards, lane or subject read', () => {
  const input = props();
  const view = render(<ReadingColumn {...input} />);
  expect(view.getByTestId('review-comparison-overview')).toBeTruthy();
  expect(view.getByText('Family First guarantee')).toBeTruthy();
  expect(view.getByText('Family Second guarantee')).toBeTruthy();
  expect(view.getAllByTestId('review-member-summary')).toHaveLength(3);
  expect(view.getAllByTestId('review-family-counts').map((node) => node.textContent)).toEqual([
    '2 invariants · 2 changed locations', '1 invariant · 1 changed location',
  ]);
  expect(view.queryByTestId('focused-code-card')).toBeNull();
  expect(view.queryByText('Selected lane')).toBeNull();
  expect(input.context.slot).not.toHaveBeenCalled();
  expect(view.getByText('Page notes')).toBeTruthy();
  expect(view.getByText('Technical details')).toBeTruthy();
});

it('shows a family’s member summaries and selects a member with its family context', () => {
  const input = props();
  input.focusedSubject = { kind: 'family', id: FAMILY };
  input.context.selection = { kind: 'subject', subject: input.focusedSubject };
  const view = render(<ReadingColumn {...input} />);
  const overview = view.getByTestId('review-family-overview');
  expect(within(overview).getAllByTestId('review-member-summary')).toHaveLength(2);
  expect(within(overview).getByText('clarify')).toBeTruthy();
  expect(within(overview).getByText('added')).toBeTruthy();
  expect(view.queryByText('Family Second guarantee')).toBeNull();
  expect(view.queryByTestId('focused-code-card')).toBeNull();
  // The family's own read is drawn (its state, guarantee change, steps and judgment); no member's.
  expect(input.context.slot).toHaveBeenCalled();
  expect(new Set(vi.mocked(input.context.slot).mock.calls.map(([subject]) => JSON.stringify(subject)))).toEqual(
    new Set([JSON.stringify({ kind: 'family', id: FAMILY })]),
  );
  fireEvent.click(within(overview).getByRole('button', { name: 'INV-OTHER · A second invariant' }));
  expect(input.context.select).toHaveBeenCalledWith({ kind: 'subject', subject: { kind: 'invariant', id: OTHER }, family: FAMILY });
});

it('shows complete inventory counts for a repeated family member before its code is read', () => {
  const input = props();
  for (const group of input.model.groups)
    for (const node of group.subjects) {
      node.cards = 'unread';
      node.locationsKnown = true;
    }
  input.focusedSubject = { kind: 'family', id: SECOND_FAMILY };
  input.context.selection = { kind: 'subject', subject: input.focusedSubject };
  const view = render(<ReadingColumn {...input} />);
  expect(view.getByTestId('review-member-location-counts').textContent).toBe('1 changed · 0 unchanged · 0 not comparable');
  expect(view.getByTestId('review-family-counts').textContent).toBe('1 invariant · 1 changed location');
  expect(view.queryByTestId('focused-code-card')).toBeNull();
  expect(new Set(vi.mocked(input.context.slot).mock.calls.map(([subject]) => JSON.stringify(subject)))).toEqual(
    new Set([JSON.stringify({ kind: 'family', id: SECOND_FAMILY })]),
  );
});

it('does not invent zero family counts before the location inventory is known', () => {
  const input = props();
  for (const group of input.model.groups)
    for (const node of group.subjects) { node.locationsKnown = false; delete node.counts; }
  input.focusedSubject = { kind: 'family', id: FAMILY };
  const view = render(<ReadingColumn {...input} />);
  expect(view.getAllByTestId('review-member-summary')).toHaveLength(2);
  expect(view.queryByTestId('review-member-location-counts')).toBeNull();
  expect(view.getByTestId('review-family-counts').textContent).toBe('2 invariants');
});

it('counts each family member once and withholds the aggregate while any member is unread', () => {
  const input = props();
  const family = input.model.groups.find((group) => group.family?.row.selectorId === FAMILY)!;
  family.subjects.push({ ...family.subjects[0], key: 'duplicate-membership' });
  input.focusedSubject = { kind: 'family', id: FAMILY };
  const view = render(<ReadingColumn {...input} />);
  expect(view.getByTestId('review-family-counts').textContent).toBe('2 invariants · 2 changed locations');
  family.subjects.find((node) => node.row.selectorId === OTHER)!.locationsKnown = false;
  delete family.subjects.find((node) => node.row.selectorId === OTHER)!.counts;
  view.rerender(<ReadingColumn {...input} />);
  expect(view.getByTestId('review-family-counts').textContent).toBe('2 invariants');
  expect(view.queryByTestId('focused-code-card')).toBeNull();
});

it('mounts only the chosen invariant and prefers its selected family occurrence even when not first', () => {
  const input = props();
  const chosen = input.model.groups.find((group) => group.family?.row.selectorId === SECOND_FAMILY)!.subjects[0];
  expect(chosen.first).toBe(false);
  input.focusedSubject = { kind: 'invariant', id: L181_SUBJECT };
  input.context.selection = { kind: 'subject', subject: input.focusedSubject, family: SECOND_FAMILY };
  const view = render(<ReadingColumn {...input} />);
  const section = view.getByTestId('review-subject-section');
  expect(section.dataset.family).toBe(SECOND_FAMILY);
  expect(view.queryByTestId('review-subject-elsewhere')).toBeNull();
  expect(view.getAllByTestId('focused-code-card').map((node) => node.dataset.subject)).toEqual([L181_SUBJECT]);
  expect(input.context.slot).toHaveBeenCalledWith(input.focusedSubject);
  input.focusedSubject = { kind: 'invariant', id: OTHER };
  input.context.selection = { kind: 'subject', subject: input.focusedSubject, family: FAMILY };
  view.rerender(<ReadingColumn {...input} />);
  expect(view.getAllByTestId('focused-code-card').map((node) => node.dataset.subject)).toEqual([OTHER]);
});

it('shows unread locations explicitly rather than saying a selected invariant has zero changes', () => {
  const input = props();
  input.focusedSubject = { kind: 'invariant', id: OTHER };
  input.context.selection = { kind: 'subject', subject: input.focusedSubject };
  for (const group of input.model.groups)
    for (const node of group.subjects)
      if (node.row.selectorId === OTHER) node.cards = 'unread';
  const view = render(<ReadingColumn {...input} />);
  expect(view.getByTestId('review-subject-cards-unread')).toBeTruthy();
  expect(view.queryByTestId('review-subject-no-changed')).toBeNull();
  expect(view.queryByTestId('focused-code-card')).toBeNull();
});

it('shows lane content only for a lane selection and leaves explicit replacements in control', () => {
  const input = props();
  input.context.selection = { kind: 'lane', path: 'source.py' };
  const view = render(<ReadingColumn {...input} />);
  expect(view.getByText('Selected lane')).toBeTruthy();
  expect(view.queryByTestId('review-comparison-overview')).toBeNull();
  expect(view.queryByTestId('focused-code-card')).toBeNull();
  input.context.selection = {
    kind: 'block', block: { kind: 'hunk', path: 'source.py', before: { start: 1, count: 1 }, after: { start: 1, count: 2 }, trees: { codeBase: 'base', codeCandidate: 'candidate' } },
  };
  view.rerender(<ReadingColumn {...input} />);
  expect(view.getByText('Selected lane')).toBeTruthy();
  expect(view.queryByTestId('review-comparison-overview')).toBeNull();
  view.rerender(<ReadingColumn {...input} replacement={<p>Explanation frame</p>} />);
  expect(view.getByText('Explanation frame')).toBeTruthy();
  expect(view.queryByText('Selected lane')).toBeNull();
});

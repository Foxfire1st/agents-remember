// In the intent review's right-hand list, each rule's row shows its state in the
// candidate memory tree and its writer as role and model beside its location counts, in the same
// words as the tree row's title. A rule the candidate tree names no state for shows none.
import { createRef } from 'react';
import { cleanup, render, within } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';

import type { ReviewOutline } from '../../../data/review';
import type { ReviewSelection } from '../reviewSelection';
import { changeSetModel, type ChangeSetModel } from '../changeSetOrder';
import { l181Answer, l181Inputs, l181Outline, L181_SUBJECT } from '../changeSetOrder.test-utils';
import { ReadingColumn } from './ReadingColumn';
import { ReadingContext, type ReadingContextValue } from './readingContext';
import { ComparisonOverview } from './SubjectSection';

vi.mock('../ExpressionCards', async (original) => ({
  ...await original<typeof import('../ExpressionCards')>(),
  ReviewBlockCard: () => <div data-testid="focused-code-card" />,
}));

function context(): ReadingContextValue {
  return {
    place: { repo: 'agents-remember', master: '260928_maintained-invariant-knowledge', leaf: '260928-MIK-L181', repository: 'agents-remember' },
    comparison: l181Answer.comparison!,
    payload: null,
    planning: new Map(),
    layout: 'inline',
    collapse: true,
    sort: 'priority',
    selection: null,
    selectedBlock: null,
    openGroups: new Set(),
    toggleGroup: () => undefined,
    select: () => undefined,
    slot: () => null,
    familyMembers: new Map(),
  };
}

const draw = (model: ChangeSetModel) =>
  render(
    <ReadingContext.Provider value={context()}>
      <ComparisonOverview groups={model.groups} />
    </ReadingContext.Provider>,
  );

const memberRow = (view: ReturnType<typeof draw>, selectorId: string) =>
  view.getAllByTestId('review-member-summary').find((row) => row.dataset.subject === selectorId)!;

afterEach(cleanup);

it("shows a rule's state and its writer as role and model beside its counts", () => {
  const model = changeSetModel(l181Inputs(), 'priority', 'changed', { selection: null });
  const node = model.subjects.find((one) => one.row.selectorId === L181_SUBJECT)!;
  node.row.standing = { record_state: 'reviewed', writer: { role: 'curator', model: 'claude-haiku-5-5' } };
  const row = memberRow(draw(model), L181_SUBJECT);
  const standing = within(row).getByTestId('review-member-standing');
  expect(standing.textContent).toBe('reviewed · written by curator · claude-haiku-5-5');
  expect(standing.dataset.recordState).toBe('reviewed');
});

it('shows an unconverted rule as proposed, written by unknown', () => {
  const model = changeSetModel(l181Inputs(), 'priority', 'changed', { selection: null });
  const node = model.subjects.find((one) => one.row.selectorId === L181_SUBJECT)!;
  node.row.standing = { record_state: 'proposed', writer: { role: 'unknown', model: 'unknown' } };
  const row = memberRow(draw(model), L181_SUBJECT);
  expect(within(row).getByTestId('review-member-standing').textContent).toBe(
    'proposed · written by unknown · unknown',
  );
});

it('shows no state for a rule the candidate tree names none for', () => {
  const model = changeSetModel(l181Inputs(), 'priority', 'changed', { selection: null });
  const node = model.subjects.find((one) => one.row.selectorId === L181_SUBJECT)!;
  node.row.standing = undefined;
  const row = memberRow(draw(model), L181_SUBJECT);
  expect(within(row).queryByTestId('review-member-standing')).toBeNull();
});

it('separates the writer from the location counts with a visible dot', () => {
  const model = changeSetModel(l181Inputs(), 'priority', 'changed', { selection: null });
  const node = model.subjects.find((one) => one.row.selectorId === L181_SUBJECT)!;
  node.row.standing = { record_state: 'reviewed', writer: { role: 'curator', model: 'claude-haiku-5-5' } };
  const row = memberRow(draw(model), L181_SUBJECT);
  expect(within(row).getByTestId('review-member-location-counts')).toBeTruthy();
  expect(row.textContent).toMatch(/written by curator · claude-haiku-5-5 · \d+ changed · /);
});

// The selected detail: a rule selected alone, by one of its locations, or as a member of its family,
// and the family itself. Each heads its detail with the state and writer of the candidate tree.
const FAMILY = 'family-standing';
const standingOutline = (writer: { role: string; model: string }, state: 'reviewed' | 'proposed'): ReviewOutline => ({
  ...l181Outline,
  families: [{
    selector_id: FAMILY, id: 'FAM-STATE1', title: 'State guarantee', change: 'changed',
    record_state: 'proposed', writer: { role: 'unknown', model: 'unknown' },
    members: [{ selector_id: L181_SUBJECT, id: 'INV-1ZKP4R', membership: 'both' }],
  }],
  invariants: l181Outline.invariants.map((row) =>
    row.selector_id === L181_SUBJECT ? { ...row, record_state: state, writer } : row),
});

type Pick = ReviewSelection | ((model: ChangeSetModel) => ReviewSelection);

function column(outline: ReviewOutline, pick: Pick, focused: { kind: 'invariant' | 'family'; id: string }) {
  const model = changeSetModel(l181Inputs({ outline }), 'authored', 'all', { selection: null });
  const selection = typeof pick === 'function' ? pick(model) : pick;
  return render(
    <ReadingColumn
      scroller={createRef<HTMLDivElement>()}
      context={{ ...context(), sort: 'authored', selection, slot: () => null }}
      model={model}
      focusedSubject={focused}
      notes={null}
      summary={null}
      lane={null}
      canvas={null}
      details={null}
    />,
  );
}

const curator = { role: 'curator', model: 'claude-haiku-5-5' };

it.each([
  ['the rule', { kind: 'subject', subject: { kind: 'invariant', id: L181_SUBJECT } }],
  ['a location of the rule', (model: ChangeSetModel): ReviewSelection => ({
    kind: 'block',
    block: model.subjects.find((node) => node.row.selectorId === L181_SUBJECT && node.first)!.changed[0].identity,
  })],
  ['the rule as a family member', { kind: 'subject', subject: { kind: 'invariant', id: L181_SUBJECT }, family: FAMILY }],
] as [string, Pick][])('heads the selected detail of %s with its state and writer', (_name, selection) => {
  const view = column(standingOutline(curator, 'reviewed'), selection, { kind: 'invariant', id: L181_SUBJECT });
  const section = view.getByTestId('review-subject-section');
  const standing = within(section).getByTestId('review-subject-standing');
  expect(standing.textContent).toBe('reviewed · written by curator · claude-haiku-5-5');
  expect(standing.dataset.recordState).toBe('reviewed');
});

it('heads an unconverted rule as proposed, written by unknown, in its selected detail', () => {
  const selection: ReviewSelection = { kind: 'subject', subject: { kind: 'invariant', id: L181_SUBJECT }, family: FAMILY };
  const view = column(standingOutline({ role: 'unknown', model: 'unknown' }, 'proposed'), selection, { kind: 'invariant', id: L181_SUBJECT });
  expect(view.getByTestId('review-subject-standing').textContent).toBe('proposed · written by unknown · unknown');
});

it("heads a selected family with the family's own state and writer", () => {
  const selection: ReviewSelection = { kind: 'subject', subject: { kind: 'family', id: FAMILY } };
  const view = column(standingOutline(curator, 'reviewed'), selection, { kind: 'family', id: FAMILY });
  const heading = view.getByTestId('review-family-heading');
  expect(within(heading).getByTestId('review-subject-standing').textContent).toBe(
    'proposed · written by unknown · unknown',
  );
});

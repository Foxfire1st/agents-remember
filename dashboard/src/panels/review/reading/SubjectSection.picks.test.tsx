// MIK-R188 rule 2 at the review page's own ⊕ call sites: an invariant's and a family's ⊕ name the
// record by its text id (INV-…/FAM-…), never by the identity UUID the outline selects it by, and
// every invariant, family and review-block pick names the comparison it was taken from and the
// memory side that holds the record, whose revision the server reads there.
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, expect, it } from 'vitest';

import type { ReviewOutline } from '../../../data/review';
import { trayStore } from '../../../tray/trayStore';
import type { TrayReference } from '../../../tray/types';
import { changeSetModel, type ChangeSetModel } from '../changeSetOrder';
import { l181Answer, l181Inputs, L181_SUBJECT } from '../changeSetOrder.test-utils';
import { ReadingContext, type ReadingContextValue } from './readingContext';
import { SubjectGroupSection } from './SubjectSection';

const comparison = l181Answer.comparison!;

function context(): ReadingContextValue {
  return {
    place: { repo: 'agents-remember', master: '260928_maintained-invariant-knowledge', leaf: '260928-MIK-L181', repository: 'agents-remember' },
    comparison,
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

function draw(model: ChangeSetModel) {
  return render(
    <ReadingContext.Provider value={context()}>
      {model.groups.map((group) => (
        <SubjectGroupSection key={group.key} group={group} labelUnlisted={false} />
      ))}
    </ReadingContext.Provider>,
  );
}

const picked = (): TrayReference[] =>
  trayStore.getState().picks.flatMap((pick) => (pick.input.kind === 'reference' ? [pick.input.reference] : []));

beforeEach(() => {
  for (const pick of trayStore.getState().picks) trayStore.getState().remove(pick.id);
});

afterEach(cleanup);

it("sends an invariant's text id with its comparison at the memory side that holds it", () => {
  const model = changeSetModel(l181Inputs(), 'priority', 'changed', { selection: null });
  const node = model.subjects.find((one) => one.row.selectorId === L181_SUBJECT)!;
  node.row.revisionId = 'invariant-revision-1';
  draw(model);
  fireEvent.click(screen.getByRole('button', { name: 'Add invariant INV-1ZKP4R to the AR tray' }));
  const block = node.changed[0].entry.id;
  fireEvent.click(screen.getByRole('button', { name: `Add block ${block} to the AR tray` }));
  const [invariant, reviewBlock] = picked();
  expect(invariant).toMatchObject({ kind: 'invariant', id: 'INV-1ZKP4R', memoryTree: comparison.memory_candidate.tree });
  expect(invariant.kind === 'invariant' && invariant.comparison?.number).toBe(comparison.number);
  // A walked revision does not say which side it is of: the server reads the revision at the
  // memory tree the pick names (MIK-R188 rule 2), so the page sends none of its own.
  expect(invariant).not.toHaveProperty('revisionId');
  expect(reviewBlock).toMatchObject({ kind: 'review-block' });
  expect(reviewBlock).not.toHaveProperty('invariantRevisionId');
});

it('names a removed invariant at the base memory tree, which alone holds it', () => {
  const model = changeSetModel(l181Inputs(), 'priority', 'changed', { selection: null });
  const node = model.subjects.find((one) => one.row.selectorId === L181_SUBJECT)!;
  node.row.badge = 'removed';
  draw(model);
  fireEvent.click(screen.getByRole('button', { name: 'Add invariant INV-1ZKP4R to the AR tray' }));
  const [invariant] = picked();
  expect(invariant).toMatchObject({ kind: 'invariant', memoryTree: comparison.memory_base.tree });
});

it("sends a family's text id with its comparison", () => {
  const outline: ReviewOutline = {
    state: 'outlined',
    detail: 'one family over the captured invariant',
    families: [
      {
        selector_id: 'family-identity-uuid',
        id: 'FAM-1ABCDE',
        title: 'Turn ends are kept',
        change: 'changed',
        members: [{ selector_id: L181_SUBJECT, id: 'INV-1ZKP4R', membership: 'both' }],
      },
    ],
    invariants: l181Inputs().outline!.invariants,
  };
  const model = changeSetModel(l181Inputs({ outline }), 'priority', 'changed', { selection: null });
  draw(model);
  fireEvent.click(screen.getByRole('button', { name: 'Add family FAM-1ABCDE to the AR tray' }));
  const [family] = picked();
  expect(family).toMatchObject({ kind: 'family', id: 'FAM-1ABCDE' });
  expect(family.kind === 'family' && family.comparison?.number).toBe(comparison.number);
});

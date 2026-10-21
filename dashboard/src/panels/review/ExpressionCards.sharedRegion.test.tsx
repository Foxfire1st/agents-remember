// One region, one card in the reading column (MIK-R86 v2 normative bullet 1; MIK-R89 v2's kept v1
// rule 1, MIK-R31's shared card): a subject's entries at one region on both sides are drawn as one
// card, which lists each entry with its rationale and its own ⊕, and which is the selected card, and
// the place a selection scrolls to, for each of its entries. L181 #8's captured card read:
// RLZ-EC5NRM, RLZ-ZA95M4 and RLZ-ZT2RRK all name turn_ended.ts L750–795 → L1697–1817.
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, it } from 'vitest';

import { trayStore } from '../../tray/trayStore';
import { blockKey } from './blockIdentity';
import { changeSetModel } from './changeSetOrder';
import { l181Answer, l181Inputs } from './changeSetOrder.test-utils';
import { blockElement } from './reading/blockDom';
import { ReadingContext, type ReadingContextValue } from './reading/readingContext';
import { SubjectGroupSection } from './reading/SubjectSection';

const TRIPLE = ['RLZ-EC5NRM', 'RLZ-ZA95M4', 'RLZ-ZT2RRK'];
const comparison = l181Answer.comparison!;
const model = changeSetModel(l181Inputs(), 'priority', 'changed', { selection: null });
const location = (id: string) => model.subjects[0].changed.find((node) => node.entry.id === id)!;

function draw(selected: string | null = null) {
  const value: ReadingContextValue = {
    place: { repo: 'agents-remember', master: '260928_maintained-invariant-knowledge', leaf: '260928-MIK-L181', repository: 'agents-remember' },
    comparison,
    payload: null,
    planning: new Map(),
    layout: 'inline',
    collapse: true,
    sort: 'priority',
    selection: null,
    selectedBlock: selected,
    openGroups: new Set(),
    toggleGroup: () => undefined,
    select: () => undefined,
    slot: () => null,
    familyMembers: new Map(),
  };
  return render(
    <ReadingContext.Provider value={value}>
      {model.groups.map((group) => (
        <SubjectGroupSection key={group.key} group={group} labelUnlisted={false} />
      ))}
    </ReadingContext.Provider>,
  );
}

const sharedCards = () =>
  screen.getAllByTestId('review-expression-card').filter((card) => card.dataset.entries === TRIPLE.join(','));

beforeEach(() => {
  for (const pick of trayStore.getState().picks) trayStore.getState().remove(pick.id);
});

afterEach(cleanup);

it('draws the triple as one card that lists each entry with its rationale and its own ⊕', () => {
  draw();
  expect(screen.getAllByTestId('review-expression-card')).toHaveLength(13);
  const [shared] = sharedCards();
  expect(sharedCards()).toHaveLength(1);
  expect(within(shared).getAllByTestId('review-card-voice').map((voice) => voice.dataset.entry)).toEqual(TRIPLE);
  expect(within(shared).getAllByTestId('review-card-excerpt')).toHaveLength(1);
  // The column counts entries still.
  expect(screen.getByText(/^Changed locations$/).textContent).toMatch(/^Changed locations 15 · /);
  for (const id of TRIPLE) fireEvent.click(within(shared).getByRole('button', { name: `Add block ${id} to the AR tray` }));
  const blocks = trayStore
    .getState()
    .picks.flatMap((pick) => (pick.input.kind === 'reference' && pick.input.reference.kind === 'review-block' ? [pick.input.reference.block] : []));
  expect(blocks.map((block) => block.entry)).toEqual(TRIPLE);
  expect(blocks.map((block) => blockKey(block))).toEqual(TRIPLE.map((id) => location(id).key));
});

it('is the selected card, and holds the place a selection scrolls to, for each of its entries', () => {
  for (const id of TRIPLE) {
    const view = draw(location(id).key);
    const [shared] = sharedCards();
    expect(shared.dataset.selected).toBe('true');
    expect(screen.getAllByTestId('review-expression-card').filter((card) => card.dataset.selected === 'true')).toHaveLength(1);
    const target = blockElement(view.container, location(id).key);
    expect(target !== null && shared.contains(target)).toBe(true);
    view.unmount();
  }
});

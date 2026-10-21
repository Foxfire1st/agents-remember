// The review page's tree (MIK-R86 v2 rules 1-5, 8, 9; MIK-R89 v2 rules 2, 3, 5, 7, 9): one-line rows
// with one reason, the control line (filter, Changed | All, the one Sort control), nothing selected
// on open, location and group rows, the Worklist row above the pinned lane, and the one "All changes"
// link. Explicit synthetic outline facts exercise each badge branch; L181's captured card read gives
// the locations.
import { act, cleanup, fireEvent, render, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';

import type { ReviewOutline } from '../../data/review';
import { changeSetModel, type ChangeSetInputs, type SubjectNode } from './changeSetOrder';
import { l181Inputs } from './changeSetOrder.test-utils';
import type { TreeLane } from './ReviewDestinations';
import type { ReviewSelection } from './reviewSelection';
import { ReviewTree } from './tree/ReviewTree';
import * as styles from './tree/treeStyles';
import { TREE_ORDER_KEY, treeOrderStore, useTreeOrder } from './triageOrderPreference';
import { readTreeView, TREE_VIEW_KEY, treeViewStore, useTreeView } from './triageViewPreference';

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
    { selector_id: 'i5', id: 'INV-5', summary: 'No family lists this new meaning', change: 'added' },
  ],
};
const synthetic: ChangeSetInputs = { outline, catalogue: [], code: new Map(), cards: { state: 'reading' } };
const lane: TreeLane = {
  state: 'ready',
  files: [
    { path: 'dashboard/src/a.ts', destination: 'unexplained', reason: '2 of 2 hunks unexplained' },
    { path: 'dashboard/src/b.ts', destination: 'unknown', reason: '1 of 3 hunks unknown' },
  ],
  counts: { unexplained: 1, unknown: 1 },
};

function Harness({
  inputs = synthetic,
  kept,
  onSelect = () => undefined,
  onOpenGroup = () => undefined,
  outside,
  laneRows = lane,
  codeFacts,
}: {
  inputs?: ChangeSetInputs;
  kept?: ReadonlyMap<string, string>;
  onSelect?: (selection: ReviewSelection) => void;
  onOpenGroup?: (node: SubjectNode, group: string) => void;
  outside?: ReviewSelection;
  laneRows?: TreeLane;
  codeFacts?: { count: number; comparison?: number };
}) {
  const [selection, setSelection] = useState<ReviewSelection | null>(null);
  const [query, setQuery] = useState('');
  const walked = kept ? { entries: [], kept } : undefined;
  const model = changeSetModel(inputs, useTreeOrder(), useTreeView(), { selection, walked });
  return (
    <div data-kbzone="review">
      <button type="button" onClick={() => outside && setSelection(outside)}>
        select elsewhere
      </button>
      <ReviewTree
        model={model}
        selection={selection}
        onSelect={(next) => {
          onSelect(next);
          setSelection(next);
        }}
        onOpenGroup={onOpenGroup}
        query={query}
        onQuery={setQuery}
        changedFiles={7}
        allChanges={7}
        onOpenAllChanges={() => undefined}
        lane={laneRows}
        onOpenLane={() => undefined}
        worklist={{ count: '3 open', open: false }}
        onOpenWorklist={() => undefined}
        codeFacts={codeFacts}
      />
    </div>
  );
}

beforeEach(() => {
  treeViewStore.getState().setView('changed');
  treeOrderStore.getState().setOrder('priority');
  // jsdom has no CSS.escape; react-aria's menu focuses its items through it, as browsers provide.
  if (!globalThis.CSS?.escape) vi.stubGlobal('CSS', { escape: (value: string) => value.replace(/["\\]/g, '\\$&') });
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const subjectRows = (view: ReturnType<typeof render>) =>
  view.getAllByTestId('review-family-member-open').map((row) => row.dataset.subjectId);

it('draws one-line rows with one reason, no disclosure, and persists both views', () => {
  const view = render(<Harness />);
  expect(view.container.querySelectorAll('details')).toHaveLength(0);
  // Priority: the changed family takes its member's place first; "Without a family" interleaves.
  expect(subjectRows(view)).toEqual(['i4', 'i5', 'i3']);
  expect(view.getAllByTestId('review-family-open').map((row) => row.getAttribute('aria-label'))).toEqual([
    'Next family changed',
    'first family 1 changed',
  ]);
  for (const row of view.container.querySelectorAll<HTMLElement>('[data-tree-node]'))
    expect(row.className).toMatch(/white-space_nowrap|wsp_nowrap|nowrap/);
  fireEvent.click(within(view.getByTestId('review-tree-view')).getByRole('button', { name: 'All' }));
  expect(view.getAllByTestId('review-family-member-open')).toHaveLength(5);
  expect(localStorage.getItem(TREE_VIEW_KEY)).toBe('all');
  expect(view.getByRole('button', { name: 'INV-2 Unreadable meaning unknown' }).title).toBe('INV-2 Unreadable meaning');
  expect(readTreeView({ getItem: () => { throw new Error('denied'); } })).toBe('changed');
  expect(readTreeView(undefined)).toBe('changed');
});

it('opens with nothing selected; a click selects its row and keeps the tree where it is', () => {
  const onSelect = vi.fn();
  const view = render(<Harness onSelect={onSelect} />);
  expect(view.container.querySelector('[aria-current="true"]')).toBeNull();
  const scroller = view.getByTestId('review-tree-scroll');
  scroller.scrollTop = 40;
  const row = view.getByRole('button', { name: /^INV-3 Added to this family/ });
  fireEvent.click(row);
  expect(onSelect).toHaveBeenCalledWith({ kind: 'subject', subject: { kind: 'invariant', id: 'i3' }, family: 'f1' });
  expect(row.getAttribute('aria-current')).toBe('true');
  expect(scroller.scrollTop).toBe(40);
  fireEvent.click(view.getByRole('button', { name: 'Next family changed' }));
  expect(onSelect).toHaveBeenLastCalledWith({ kind: 'subject', subject: { kind: 'family', id: 'f2' } });
});

it('reveals a selection made outside the tree by the scroller alone', () => {
  const outside: ReviewSelection = { kind: 'subject', subject: { kind: 'invariant', id: 'i3' }, family: 'f1' };
  const view = render(<Harness outside={outside} />);
  const scroller = view.getByTestId('review-tree-scroll');
  vi.spyOn(scroller, 'getBoundingClientRect').mockReturnValue({ top: 0, bottom: 100 } as DOMRect);
  const row = view.getByRole('button', { name: /^INV-3 Added to this family/ });
  vi.spyOn(row, 'getBoundingClientRect').mockImplementation(
    () => ({ top: 300 - scroller.scrollTop, bottom: 320 - scroller.scrollTop }) as DOMRect,
  );
  // The pinned rows sit in their place below the viewport.
  const pinned = scroller.querySelector<HTMLElement>('[data-review-tree-pinned]')!;
  vi.spyOn(pinned, 'getBoundingClientRect').mockReturnValue({ top: 400, bottom: 460 } as DOMRect);
  fireEvent.click(view.getByText('select elsewhere'));
  expect(scroller.scrollTop).toBe(220);
  expect(scroller.getAttribute('style')).toBeNull();
});

it('keeps a walked family with its kept tag in Changed', () => {
  const view = render(<Harness inputs={{ ...synthetic, outline: { ...outline, families: [...outline.families, { selector_id: 'f3', id: 'FAM-3', title: 'Walked family', change: 'unchanged', members: [] }] } }} kept={new Map([['f3', 'INV-earlier']])} />);
  const family = view.getByRole('button', { name: 'Walked family unchanged kept' });
  expect(within(family).getByTestId('review-family-kept').textContent).toBe('kept');
});

it('lists the changed locations under a subject and opens a group in the reading column only', () => {
  const onOpenGroup = vi.fn();
  const view = render(<Harness inputs={l181Inputs()} onOpenGroup={onOpenGroup} />);
  expect(view.getByRole('button', { name: /^INV-1ZKP4R .*weaken · 15 loc\.$/ })).toBeTruthy();
  // 15 changed entries at 13 regions: one row per region (captured: the turn_ended.ts triple).
  expect(view.getAllByTestId('review-tree-location')).toHaveLength(13);
  expect(view.getAllByTestId('review-tree-location')[0].dataset.entries).toBe('RLZ-EC5NRM,RLZ-ZA95M4,RLZ-ZT2RRK');
  expect(view.getAllByTestId('review-tree-location')[0].getAttribute('aria-label')).toMatch(/enforcement enforce · \+\d+ −\d+$/);
  const groups = view.getAllByTestId('review-tree-group');
  expect(groups.map((row) => row.textContent)).toEqual(['▸31 unchanged', '▸28 not comparable']);
  const rows = view.container.querySelectorAll('[data-tree-node]').length;
  fireEvent.click(groups[0]);
  expect(onOpenGroup).toHaveBeenCalledWith(expect.objectContaining({ key: expect.stringContaining('82ef402e') }), 'unchanged');
  expect(view.container.querySelectorAll('[data-tree-node]')).toHaveLength(rows);
  fireEvent.click(view.getAllByTestId('review-tree-location')[1]);
  expect(view.getAllByTestId('review-tree-location')[1].getAttribute('aria-current')).toBe('true');
});

it('pins the Worklist row above "Code without invariant", whose file rows come last, beside one All changes link', () => {
  const view = render(<Harness inputs={l181Inputs()} />);
  const nodes = [...view.container.querySelectorAll<HTMLElement>('[data-tree-node]')].map((node) => node.dataset.treeNode);
  expect(nodes.slice(-4)).toEqual(['worklist', 'lane', 'lane-file', 'lane-file']);
  expect(view.getByTestId('review-tree-worklist').getAttribute('aria-label')).toBe('Worklist 3 open');
  expect(view.getByTestId('review-tree-lane').getAttribute('aria-label')).toBe('Code without invariant 2 files');
  expect(view.getAllByTestId('review-tree-full-page').map((link) => link.textContent)).toEqual([
    'Unexplained ↗',
    'Unknown attribution ↗',
  ]);
  expect(view.getAllByRole('button', { name: /^All changes/ })).toHaveLength(1);
  expect(view.getByTestId('review-tree-counts').textContent).toBe('1 inv. · 7 files');
  expect(view.queryByTestId('review-next-change')).toBeNull();
  expect(view.queryByTestId('review-tree-order')).toBeNull();
});

it('filters the tree and says when nothing matches', () => {
  const view = render(<Harness inputs={l181Inputs()} />);
  const filter = view.getByRole('searchbox', { name: 'Filter the change set' });
  fireEvent.change(filter, { target: { value: 'turn_log' } });
  expect(view.getAllByTestId('review-tree-location')).toHaveLength(1);
  expect(view.queryAllByTestId('review-tree-group')).toHaveLength(0);
  fireEvent.change(filter, { target: { value: 'nothing like this' } });
  expect(view.getByText('Nothing matches “nothing like this”.')).toBeTruthy();
  fireEvent.keyDown(filter, { key: 'Escape' });
  expect(view.getAllByTestId('review-tree-location')).toHaveLength(13);
});

it('finds an unchanged location by its path in All, and shows its subject and its group row', () => {
  const onOpenGroup = vi.fn();
  const view = render(<Harness inputs={l181Inputs()} onOpenGroup={onOpenGroup} />);
  fireEvent.click(within(view.getByTestId('review-tree-view')).getByRole('button', { name: 'All' }));
  const filter = view.getByRole('searchbox', { name: 'Filter the change set' });
  // Captured: RLZ-42490J is unchanged at role_agents.ts, which no changed location names.
  fireEvent.change(filter, { target: { value: 'server/role_agents.ts' } });
  expect(view.queryByText(/^Nothing matches/)).toBeNull();
  expect(subjectRows(view)).toEqual(['82ef402e-a852-59cf-a037-aeabbbe2dad6']);
  expect(view.queryAllByTestId('review-tree-location')).toHaveLength(0);
  const groups = view.getAllByTestId('review-tree-group');
  expect(groups.map((row) => row.textContent)).toEqual(['▸31 unchanged']);
  expect(view.getByTestId('review-tree-scroll').querySelectorAll('details, [aria-expanded]')).toHaveLength(0);
  fireEvent.click(groups[0]);
  expect(onOpenGroup).toHaveBeenCalledWith(expect.objectContaining({ key: expect.stringContaining('82ef402e') }), 'unchanged');
});

it('marks the one row of a shared region current for a selection of any of its entries', () => {
  // Captured: RLZ-ZA95M4 shares RLZ-EC5NRM's region; a selection made elsewhere (a URL) names it.
  const model = changeSetModel(l181Inputs(), 'priority', 'changed', { selection: null });
  const entry = model.subjects[0].changed.find((node) => node.entry.id === 'RLZ-ZA95M4')!;
  const view = render(<Harness inputs={l181Inputs()} outside={{ kind: 'block', block: entry.identity }} />);
  fireEvent.click(view.getByText('select elsewhere'));
  const current = view.container.querySelectorAll<HTMLElement>('[aria-current="true"]');
  expect([...current].map((row) => row.dataset.entries)).toEqual(['RLZ-EC5NRM,RLZ-ZA95M4,RLZ-ZT2RRK']);
});

it('sorts from the one Sort menu, stores the choice, and shows the memory sorts unavailable with the reason', async () => {
  const user = userEvent.setup();
  const view = render(<Harness />);
  await user.click(view.getByTestId('review-tree-sort'));
  const options = view.getAllByTestId('review-tree-sort-option');
  expect(options.map((option) => option.dataset.sort)).toEqual([
    'priority', 'authored', 'currentness', 'invariants-at-location', 'incoming-links', 'without-proof', 'open-weaknesses',
  ]);
  // The card read has not answered: its memory values are not there yet, so the sorts stay offered.
  await user.click(options[1]);
  expect(localStorage.getItem(TREE_ORDER_KEY)).toBe('authored');
  expect(subjectRows(view)).toEqual(['i3', 'i4', 'i5']);
  cleanup();
  const unavailable = render(
    <Harness inputs={{ ...l181Inputs(), cards: { ...(l181Inputs().cards as Extract<ChangeSetInputs['cards'], { state: 'ready' }>), memory: { state: 'unavailable', tree: 'x', reason: 'the after side is not indexed' } } }} />,
  );
  await user.click(unavailable.getByTestId('review-tree-sort'));
  const memory = unavailable.getAllByTestId('review-tree-sort-option').filter((option) => option.dataset.sort === 'incoming-links')[0];
  expect(memory.getAttribute('aria-disabled')).toBe('true');
  expect(memory.textContent).toContain('unavailable: the after side is not indexed');
  // The popup's explanatory section draws the reason once for the four (d9a1ec8f item 4).
  expect(unavailable.getByTestId('review-tree-sort-hint').textContent).toBe('Unavailable: the after side is not indexed.');
  act(() => treeOrderStore.getState().setOrder('incoming-links'));
  expect(unavailable.getByTestId('review-change-status').textContent).toContain(
    'Most incoming links is unavailable: the after side is not indexed; Priority applies.',
  );
});

it('moves focus between rows, pinned rows included, with the arrow keys', () => {
  const view = render(<Harness />);
  const rows = [...view.container.querySelectorAll<HTMLElement>('[data-tree-node]')];
  rows[0].focus();
  fireEvent.keyDown(rows[0], { key: 'ArrowDown' });
  expect(document.activeElement).toBe(rows[1]);
  fireEvent.keyDown(rows[1], { key: 'ArrowUp' });
  fireEvent.keyDown(rows[0], { key: 'ArrowUp' });
  expect(document.activeElement).toBe(rows.at(-1));
});

// MIK-R89 v2 rule 1 as amended by 719ade09 (mockup app.js:213 and :227): a location row's and a lane
// file row's line counts draw "+a" in mint and "−r" in alarm red. The signs and numbers stay in the
// text, so colour is not their only carrier; other reasons stay as they are.
it('draws the line counts of location and lane-file rows in mint and alarm red, signs kept', () => {
  const counted: TreeLane = { ...lane, files: [{ ...lane.files[0], reason: '+16 −4' }, lane.files[1]] };
  const view = render(<Harness inputs={l181Inputs()} laneRows={counted} />);
  const counts = (row: HTMLElement) =>
    [...row.querySelectorAll('span')].filter((span) => /^[+−]\d+$/.test(span.textContent ?? ''));
  const locations = view.getAllByTestId('review-tree-location');
  for (const row of locations) {
    const [plus, minus] = counts(row);
    expect([plus.className, minus.className]).toEqual([styles.ins, styles.del]);
    // The two counts are one element of their own inside the row's 0.7rem reason, the role kept as text.
    expect(plus.parentElement).toBe(minus.parentElement);
    expect(plus.parentElement?.textContent).toMatch(/^\+\d+ −\d+$/);
    const reason = row.lastElementChild as HTMLElement;
    expect(reason.className).toBe(styles.reason);
    expect(reason.textContent).toMatch(/^\w+ · \+\d+ −\d+$/);
    expect(row.getAttribute('aria-label')).toMatch(/ · \+\d+ −\d+$/);
  }
  const [file, unknown] = view.container.querySelectorAll<HTMLElement>('[data-tree-node="lane-file"]');
  expect(counts(file).map((span) => [span.textContent, span.className])).toEqual([
    ['+16', styles.ins],
    ['−4', styles.del],
  ]);
  expect(counts(file)[0].parentElement?.textContent).toBe('+16 −4');
  expect(file.textContent).toBe('a.ts+16 −4');
  expect(file.getAttribute('aria-label')).toBe('dashboard/src/a.ts +16 −4');
  expect(counts(unknown)).toHaveLength(0);
  expect(unknown.textContent).toBe('b.ts1 of 3 hunks unknown');
});

// MIK-R89 v2 rule 1 as amended by d9a1ec8f item 4 (mockup app.js:241-255): the Sort popup draws the
// mockup's rows (a mark, the name, its short reason at the end), a rule between the groups and one
// explanatory section. The four memory sorts carry their reason as the item's description, spoken but
// not drawn, so each row stays one line in the mockup's 350 px; the section says where their facts
// come from. The sorts and their order are rule 3's; L183 adds the judgment group (D383).
it("draws the Sort popup with the mockup's rows, one rule and an explanatory section", async () => {
  const user = userEvent.setup();
  // The card read has not answered, so the memory sorts are offered (see the case above).
  const view = render(<Harness />);
  await user.click(view.getByTestId('review-tree-sort'));
  const menu = view.getByRole('menu');
  const popover = view.getByTestId('review-tree-sort-popover');
  expect(popover.className).toBe(styles.popover);
  const options = within(menu).getAllByRole('menuitemradio');
  expect(options.map((option) => option.className)).toEqual(options.map(() => expect.stringContaining(styles.menuItem)));
  const drawn = (option: HTMLElement) =>
    [...option.querySelectorAll<HTMLElement>('[slot="description"]')].map((why) => [why.textContent, why.className]);
  expect(options.map(drawn)).toEqual([
    [['retire/weaken first, then size', styles.menuWhy]],
    [['order the curator wrote', styles.menuWhy]],
    [['least current first', styles.menuWhy]],
    [['invariants recorded at the location’s path', styles.unseen]],
    [['links into the location’s invariant', styles.unseen]],
    [['invariants the index lists without proof', styles.unseen]],
    [['open weaknesses recorded at the location’s path', styles.unseen]],
  ]);
  expect(within(popover).getAllByRole('separator')).toHaveLength(1);
  const hint = within(popover).getByTestId('review-tree-sort-hint');
  expect(hint.className).toBe(styles.menuHint);
  expect(hint.textContent).toBe('Read from the after side’s index.');
  expect(menu.getAttribute('aria-describedby')).toBe(hint.id);
  expect(within(popover).queryByText(/judgment/i)).toBeNull();
});

// MIK-R86 v2 rule 4 and MIK-R89 v2 rule 7: the Worklist row and "Code without invariant" are pinned
// at the bottom of the tree. The block is a direct child of the one scroller, so it sticks to the
// scroller's bottom over a long tree and sits at the panel's bottom under a short one (treeStyles.ts).
it("pins the Worklist and lane block in the one scroller, after the tree's rows", () => {
  const view = render(<Harness inputs={l181Inputs()} />);
  const scroller = view.getByTestId('review-tree-scroll');
  const block = scroller.querySelector<HTMLElement>('[data-review-tree-pinned]')!;
  expect(block.parentElement).toBe(scroller);
  expect(block.className).toBe(styles.pinned);
  expect(block.previousElementSibling).toBe(view.getByTestId('review-family-list'));
  expect(view.getByTestId('review-family-list').className).toBe(`${styles.list} ${styles.branches}`);
});

// MIK-R64 v2 rule 5: when the code facts arrive, the line says once what became a real change. A
// count of 0 made nothing real, so it is said to assistive technology and draws no line (the mockup
// has none); a count above 0 is drawn.
it("draws the code-facts line only for a count above 0, and speaks a 0", () => {
  const view = render(<Harness inputs={l181Inputs()} codeFacts={{ count: 0, comparison: 8 }} />);
  const line = view.getByTestId('review-code-facts-status');
  expect(line.textContent).toBe('No invariant changed only in its code.');
  expect(line.getAttribute('aria-live')).toBe('polite');
  expect(line.className).toBe(styles.unseen);
  view.rerender(<Harness inputs={l181Inputs()} codeFacts={{ count: 2, comparison: 8 }} />);
  expect(line.textContent).toBe('2 invariants changed only in their code.');
  expect(line.className).toBe(styles.status);
});

// MIK-R86 v1 rule 4 (kept by v2 rule 4): "every shown family with its shown members beneath it, then
// the row 'Without a family'". The row follows shown families; with none shown it heads nothing apart,
// and the reading column draws none either (SubjectSection.tsx `labelUnlisted`).
it('draws "Without a family" only after a shown family', () => {
  const view = render(<Harness />);
  expect(view.getByTestId('review-unlisted').querySelector('p')?.textContent).toBe('Without a family');
  cleanup();
  const alone = render(<Harness inputs={l181Inputs()} />);
  expect(alone.getByTestId('review-unlisted')).toBeTruthy();
  expect(alone.queryByText('Without a family')).toBeNull();
});

// MIK-R89 v2 rule 7: the one "All changes" link stays in the heading line. Its count is the same total
// the counts beside it show (pageParts.tsx passes one number for both), so it is spoken, not drawn,
// and the counts stay whole at 1440. In the 1440 tree Sort drops its drawn word "Sort:"; its name keeps
// it.
it('keeps the All changes link and the Sort name while their repeats are not drawn', () => {
  const view = render(<Harness inputs={l181Inputs()} />);
  const all = view.getByTestId('review-all-changes');
  expect(all.textContent).toBe('All changes 7');
  expect(all.className).toBe(styles.headLink);
  expect(all.querySelector('span')?.className).toBe(styles.unseen);
  const sort = view.getByTestId('review-tree-sort');
  expect(sort.getAttribute('aria-label')).toBe('Sort: Priority');
  expect(within(sort).getByText('Sort:').className).toBe(styles.sortWord);
});

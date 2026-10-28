// MIK-R89 v2 rules 8–11 and 13 on L181 #8 (reviewPage.test-utils.tsx): a selection reads its subject
// only, goes into the URL and comes back from it on a reload (a selection outside the comparison
// opens unselected, with its line); the column scrolls once to the selection after its read, unless
// the reader scrolled; the tree keeps its place; `j`/`k` walk the blocks; `[`/`]` toggle the sides.
import { act, cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { l181Answer } from '../changeSetOrder.test-utils';
import { blockIdentityOf, formatSelection } from '../blockIdentity';
import {
  isSelectionRead,
  LANE_PATH,
  L181_SUBJECT,
  pageReads,
  PLACE,
  renderPage,
  selectInvariant,
  waitForOpening,
  serveSelectedAnswer,
  servePage,
  stubIntersection,
} from './reviewPage.test-utils';
import { captured } from '../l86.test-utils';
import type { ReviewResult } from '../../../data/review';
import { resetCardCachesForTests } from '../../../data/reviewTrees';
import { proseLayoutStore } from '../intentDiffPreference';

const HASH = `#review?${new URLSearchParams({ repo: PLACE.repo, master: PLACE.master, leaf: PLACE.leaf })}`;

beforeEach(() => {
  window.localStorage.clear();
  window.history.replaceState(null, '', `/${HASH}`);
  // A kept card answer from an earlier case would answer a held read at once.
  resetCardCachesForTests();
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

async function opened() {
  await waitForOpening();
}

async function openedInvariant() {
  await selectInvariant();
  await waitFor(() => expect(screen.getAllByTestId('review-expression-card').length).toBeGreaterThan(0));
}

const subjectSection = () =>
  screen.getAllByTestId('review-subject-section').find((node) => node.dataset.subject === L181_SUBJECT)!;

const rect = (top: number) => ({ top, bottom: top + 40, left: 0, right: 0, width: 0, height: 40, x: 0, y: top, toJSON: () => ({}) });

// Records every scrollTop the page writes on the reading column.
function watchScroll(node: HTMLElement): number[] {
  const sets: number[] = [];
  let top = 0;
  Object.defineProperty(node, 'scrollTop', {
    configurable: true,
    get: () => top,
    set: (value: number) => {
      top = value;
      sets.push(value);
    },
  });
  return sets;
}

const firstChanged = () => (l181Answer.entries ?? []).find((entry) => entry.change === 'changed')!;

describe('a selection', () => {
  it('reads only its subject and cards, and scrolls the column once after the read', async () => {
    stubIntersection();
    const served = servePage(isSelectionRead);
    renderPage();
    await opened();
    const before = pageReads(served.asked).length;
    const scroller = document.querySelector<HTMLElement>('[data-review-scroller]')!;
    const sets = watchScroll(scroller);
    await selectInvariant();
    vi.spyOn(subjectSection(), 'getBoundingClientRect').mockReturnValue(rect(900));
    await waitFor(() => expect(screen.getByTestId('review-subject-slot').dataset.slotState).toBe('pending'));
    expect(sets).toEqual([]);
    await act(async () => served.release());
    await waitFor(() => expect(screen.getByTestId('review-subject-slot').dataset.slotState).toBe('answered'));
    const reads = pageReads(served.asked).slice(before);
    expect(reads.filter(isSelectionRead).map((url) => url.searchParams.get('selectorId'))).toEqual([L181_SUBJECT]);
    expect(reads.filter((url) => url.searchParams.has('invariants')).map((url) => url.searchParams.get('invariants'))).toEqual([L181_SUBJECT]);
    expect(sets).toHaveLength(1);
    expect(sets[0]).toBeGreaterThan(800);
    expect(subjectSection().dataset.selected).toBe('true');
    expect(window.location.hash).toContain(`sel=${encodeURIComponent(`s:invariant:${L181_SUBJECT}`)}`);
  });

  it('does not scroll when the reader scrolled while the read was pending', async () => {
    stubIntersection();
    const served = servePage(isSelectionRead);
    renderPage();
    await opened();
    const scroller = document.querySelector<HTMLElement>('[data-review-scroller]')!;
    const sets = watchScroll(scroller);
    await selectInvariant();
    vi.spyOn(subjectSection(), 'getBoundingClientRect').mockReturnValue(rect(900));
    fireEvent.wheel(scroller, { deltaY: 120 });
    await act(async () => served.release());
    await waitFor(() => expect(screen.getByTestId('review-subject-slot').dataset.slotState).toBe('answered'));
    expect(sets).toEqual([]);
  });

  it('keeps the tree mounted, shown and where it was after a tree click', async () => {
    stubIntersection();
    servePage();
    renderPage();
    await openedInvariant();
    const column = screen.getByTestId('review-tree-column');
    const scroll = screen.getByTestId('review-tree-scroll');
    const sets = watchScroll(scroll);
    const row = screen.getAllByTestId('review-tree-location')[0];
    fireEvent.click(row);
    await waitFor(() => expect(row.getAttribute('aria-current')).toBe('true'));
    expect(screen.getByTestId('review-tree-column')).toBe(column);
    expect(column.dataset.open).toBe('true');
    expect(screen.getByTestId('review-tree-scroll')).toBe(scroll);
    expect(sets).toEqual([]);
  });
});

describe('the URL', () => {
  it('restores a block selection on reload', async () => {
    stubIntersection();
    const block = blockIdentityOf(firstChanged(), l181Answer.comparison!);
    window.history.replaceState(null, '', `/${HASH}&sel=${encodeURIComponent(formatSelection({ kind: 'block', block }))}`);
    const { asked } = servePage();
    renderPage();
    await opened();
    await waitFor(() =>
      expect(screen.getAllByTestId('review-expression-card').find((card) => card.dataset.selected === 'true')).toBeTruthy(),
    );
    const selected = screen.getAllByTestId('review-expression-card').find((card) => card.dataset.selected === 'true')!;
    expect(selected.dataset.entries?.split(',')).toContain(firstChanged().id);
    expect(screen.queryByTestId('review-selection-outside')).toBeNull();
    const ownerReads = pageReads(asked).filter((url) => url.searchParams.has('entry'));
    expect(ownerReads).toHaveLength(1);
    expect(ownerReads[0].searchParams.get('entry')).toBe(firstChanged().id);
    expect(ownerReads[0].searchParams.get('entry_side')).toBe(block.side);
    expect(pageReads(asked).filter((url) => url.searchParams.has('invariants'))).toHaveLength(0);
  });

  it('restores the selected area with browser back and forward without remounting the tree', async () => {
    stubIntersection();
    servePage();
    renderPage();
    await openedInvariant();
    const tree = screen.getByTestId('review-tree-column');
    fireEvent.click(screen.getByTestId('review-tree-lane'));
    await waitFor(() => expect(screen.getByTestId('review-lane-section').dataset.selected).toBe('true'));
    expect(screen.queryAllByTestId('review-expression-card')).toHaveLength(0);

    act(() => window.history.back());
    await waitFor(() => expect(subjectSection()?.dataset.selected).toBe('true'));
    expect(screen.getByTestId('review-tree-column')).toBe(tree);
    expect(screen.queryByTestId('review-lane-section')).toBeNull();
    expect(new URLSearchParams(window.location.hash.split('?')[1]).get('sel')).toBe(`s:invariant:${L181_SUBJECT}`);

    act(() => window.history.forward());
    await waitFor(() => expect(screen.getByTestId('review-lane-section').dataset.selected).toBe('true'));
    expect(screen.queryAllByTestId('review-expression-card')).toHaveLength(0);
    expect(screen.getByTestId('review-tree-column')).toBe(tree);
  });

  it('restores both the comparison and its selection when traversing history', async () => {
    stubIntersection();
    const { asked } = servePage();
    renderPage();
    await openedInvariant();
    fireEvent.click(screen.getByTestId('review-comparison-button'));
    const older = await screen.findAllByTestId('review-comparison-option');
    fireEvent.click(older.find((option) => option.dataset.number === '7')!);
    await waitFor(() => expect(screen.getByTestId('review-comparison-button').textContent).toContain('#7'));
    await waitFor(() => expect(new URLSearchParams(window.location.hash.split('?')[1]).get('comparison')).toBe('7'));
    expect(screen.queryAllByTestId('review-expression-card')).toHaveLength(0);

    act(() => window.history.back());
    await waitFor(() => expect(screen.getByTestId('review-comparison-button').textContent).toContain('#8'));
    await waitFor(() => expect(subjectSection()?.dataset.selected).toBe('true'));
    expect(screen.getAllByTestId('review-expression-card')).toHaveLength(13);
    expect(new URLSearchParams(window.location.hash.split('?')[1]).get('comparison')).toBeNull();

    act(() => window.history.forward());
    await waitFor(() => expect(screen.getByTestId('review-comparison-button').textContent).toContain('#7'));
    await waitFor(() => expect(screen.queryAllByTestId('review-expression-card')).toHaveLength(0));
    expect(pageReads(asked).some((url) => url.searchParams.get('comparison') === '7')).toBe(true);
  });

  it.each([
    ['the Code without invariant row', undefined],
    ['a lane file row', LANE_PATH],
  ])('restores %s on reload', async (_name, path) => {
    stubIntersection();
    servePage();
    const first = renderPage();
    await opened();
    const row =
      path === undefined
        ? screen.getByTestId('review-tree-lane')
        : within(screen.getByTestId('review-tree-lane-files')).getAllByRole('button').find((node) => node.textContent?.includes('added.ts'))!;
    fireEvent.click(row);
    await waitFor(() => expect(new URLSearchParams(window.location.hash.split('?')[1]).get('sel')).not.toBeNull());
    first.unmount();
    servePage();
    renderPage();
    await opened();
    if (path === undefined) {
      await waitFor(() => expect(screen.getByTestId('review-tree-lane').getAttribute('aria-current')).toBe('true'));
      expect(screen.getByTestId('review-lane-section').dataset.selected).toBe('true');
    } else {
      await waitFor(() => expect(screen.getByTestId('review-lane-file').dataset.selected).toBe('true'));
      // The crumb names the lane file by its name (mockup app.js `updateCrumb`).
      expect(screen.getByTestId('review-crumb').textContent).toContain(` › ${path.split('/').pop()}`);
    }
    expect(screen.queryByTestId('review-selection-outside')).toBeNull();
  });

  it('opens unselected with one line when the selection names another comparison', async () => {
    stubIntersection();
    const block = blockIdentityOf(firstChanged(), l181Answer.comparison!);
    const foreign = { ...block, trees: { ...block.trees, codeCandidate: '0'.repeat(40) } };
    window.history.replaceState(null, '', `/${HASH}&sel=${encodeURIComponent(formatSelection({ kind: 'block', block: foreign }))}`);
    servePage();
    renderPage();
    await opened();
    await waitFor(() => expect(screen.getByTestId('review-selection-outside').textContent).toContain('comparison #8'));
    expect(screen.queryAllByTestId('review-expression-card').some((card) => card.dataset.selected === 'true')).toBe(false);
  });

  // Rule 13: a lane hunk is named by its span through the file's diff; the span is checked against the
  // file's classification, which the restore reads, so a forged span opens the page unselected.
  const laneHunk = (before: [number, number], after: [number, number]) =>
    formatSelection({
      kind: 'block',
      block: {
        kind: 'hunk',
        path: LANE_PATH,
        before: { start: before[0], count: before[1] },
        after: { start: after[0], count: after[1] },
        trees: { codeBase: l181Answer.comparison!.code_base.tree, codeCandidate: l181Answer.comparison!.code_candidate.tree },
      },
    });

  it('opens unselected with one line when a hunk names a span its file does not hold', async () => {
    stubIntersection();
    window.history.replaceState(null, '', `/${HASH}&sel=${encodeURIComponent(laneHunk([999999, 3], [999999, 3]))}`);
    servePage();
    renderPage();
    await opened();
    await waitFor(() => expect(screen.getByTestId('review-selection-outside').textContent).toContain('comparison #8'));
    expect(screen.getByTestId('review-crumb').textContent).not.toContain(LANE_PATH.split('/').pop());
  });

  it('restores a hunk its file holds on reload', async () => {
    stubIntersection();
    window.history.replaceState(null, '', `/${HASH}&sel=${encodeURIComponent(laneHunk([0, 0], [1, 45]))}`);
    servePage();
    renderPage();
    await opened();
    await waitFor(() => expect(screen.getByTestId('review-crumb').textContent).toContain(` › ${LANE_PATH.split('/').pop()}`));
    expect(screen.queryByTestId('review-selection-outside')).toBeNull();
  });
});

// Rule 9: a selection the reader makes while the link's restore still waits on its reads supersedes it.
// The late answer neither replaces the choice nor its URL, nor arms a second landing.
describe('a choice made while the link waits', () => {
  const foreignTrees = (block: ReturnType<typeof blockIdentityOf>) => ({ ...block, trees: { ...block.trees, codeCandidate: '0'.repeat(40) } });

  it.each([
    ['a block of this comparison', (block: ReturnType<typeof blockIdentityOf>) => block],
    ['a block of another comparison', foreignTrees],
  ])('keeps the choice, its URL and its one landing when the link names %s', async (_name, named) => {
    stubIntersection();
    const block = named(blockIdentityOf(firstChanged(), l181Answer.comparison!));
    window.history.replaceState(null, '', `/${HASH}&sel=${encodeURIComponent(formatSelection({ kind: 'block', block }))}`);
    const served = servePage((url) => url.searchParams.has('entry'));
    renderPage();
    await opened();
    const scroller = document.querySelector<HTMLElement>('[data-review-scroller]')!;
    let landings = 0;
    scroller.addEventListener('review-product-scroll', () => (landings += 1));
    fireEvent.click(screen.getByTestId('review-tree-lane'));
    await waitFor(() => expect(screen.getByTestId('review-tree-lane').getAttribute('aria-current')).toBe('true'));
    await waitFor(() => expect(landings).toBe(1));
    await act(async () => served.release());
    await act(async () => new Promise((resolve) => setTimeout(resolve, 0)));
    expect(screen.queryAllByTestId('review-expression-card')).toHaveLength(0);
    // The URL holds the reader's choice: a restore that still waited would hold it until it answered.
    await waitFor(() => expect(new URLSearchParams(window.location.hash.split('?')[1]).get('sel')).toBe('l:'));
    expect(screen.queryAllByTestId('review-expression-card').some((card) => card.dataset.selected === 'true')).toBe(false);
    expect(screen.getByTestId('review-tree-lane').getAttribute('aria-current')).toBe('true');
    expect(screen.queryByTestId('review-selection-outside')).toBeNull();
    expect(landings).toBe(1);
  });
});

describe('the keys', () => {
  it('walks the blocks with j and k', async () => {
    stubIntersection();
    servePage();
    renderPage();
    await openedInvariant();
    const scroller = document.querySelector<HTMLElement>('[data-review-scroller]')!;
    const cards = () => screen.getAllByTestId('review-expression-card');
    const selectedIndex = () => cards().findIndex((card) => card.dataset.selected === 'true');
    fireEvent.keyDown(scroller, { key: 'j', code: 'KeyJ' });
    await waitFor(() => expect(selectedIndex()).toBe(0));
    fireEvent.keyDown(scroller, { key: 'j', code: 'KeyJ' });
    await waitFor(() => expect(selectedIndex()).toBe(1));
    fireEvent.keyDown(scroller, { key: 'k', code: 'KeyK' });
    await waitFor(() => expect(selectedIndex()).toBe(0));
  });

  it('walks the selected invariant blocks with j when focus is on body, never in a text field', async () => {
    stubIntersection();
    servePage();
    renderPage();
    await openedInvariant();
    const cards = () => screen.getAllByTestId('review-expression-card');
    const selectedIndex = () => cards().findIndex((card) => card.dataset.selected === 'true');
    // The keys act in their keydown handler, and `fireEvent` flushes what they set: a key that acted
    // has acted when it returns, so a key that must not act is checked right after it.
    fireEvent.keyDown(screen.getByTestId('review-family-filter'), { key: 'j', code: 'KeyJ' });
    expect(selectedIndex()).toBe(-1);
    (document.activeElement as HTMLElement | null)?.blur();
    expect(document.activeElement).toBe(document.body);
    fireEvent.keyDown(document.body, { key: 'j', code: 'KeyJ' });
    await waitFor(() => expect(selectedIndex()).toBe(0));
    fireEvent.keyDown(document.body, { key: 'j', code: 'KeyJ', ctrlKey: true });
    expect(selectedIndex()).toBe(0);
  });

  it('toggles the tree with [ and the chat with ], never in a text field', async () => {
    stubIntersection();
    servePage();
    const toggle = vi.fn();
    renderPage({ chat: { open: false, toggle } });
    await openedInvariant();
    fireEvent.keyDown(screen.getByTestId('review-family-filter'), { key: '[' });
    expect(screen.getByTestId('review-tree-column').dataset.open).toBe('true');
    fireEvent.keyDown(document.body, { key: '[' });
    expect(screen.getByTestId('review-tree-column').dataset.open).toBe('false');
    fireEvent.keyDown(document.body, { key: ']' });
    expect(toggle).toHaveBeenCalledTimes(1);
    expect(screen.getByTestId('review-chat-toggle').getAttribute('aria-label')).toBe('Chat sidebar');
    expect(screen.getByTestId('review-tree-toggle').getAttribute('aria-label')).toBe('Tree sidebar');
    fireEvent.click(screen.getByTestId('review-tree-toggle'));
    expect(screen.getByTestId('review-tree-column').dataset.open).toBe('true');
  });
});

// Finding 2 (L181 #8 in the browser): the crumb named the subject by its selector id, and the selected
// view printed the family state's token `no_family_recorded`. The selection is named by its text id,
// and each state token by its words.
describe('what the selected view names', () => {
  const noFamily = captured<ReviewResult>('subjectReview.noFamily.captured.json').payload!.family_context!;

  it('names the subject by its text id and its family state in words, never as a token', async () => {
    stubIntersection();
    servePage();
    // Authored text may quote a code identifier: such a word is the author's, not a raw token.
    let authored = '';
    serveSelectedAnswer((payload) => {
      payload.family_context = structuredClone(noFamily);
      const { knowledge } = payload;
      authored = JSON.stringify([
        knowledge.field_changes.map((change) => [change.before_value, change.after_value]),
        knowledge.before_statement,
        knowledge.after_statement,
      ]);
    });
    renderPage();
    await openedInvariant();
    fireEvent.click(within(subjectSection()).getByTestId('review-subject-select'));
    await waitFor(() => expect(screen.getByTestId('review-subject-slot').dataset.slotState).toBe('answered'));
    const crumb = screen.getByTestId('review-crumb').textContent ?? '';
    expect(crumb).toContain('INV-1ZKP4R');
    expect(crumb).not.toContain(L181_SUBJECT);
    const slot = screen.getByTestId('review-subject-slot');
    const texts = [...slot.querySelectorAll('*')].flatMap((node) =>
      [...node.childNodes].filter((one) => one.nodeType === Node.TEXT_NODE).map((one) => one.textContent ?? ''),
    );
    const tokens = texts.flatMap((text) => text.match(/\b[a-z]+(?:_[a-z]+)+\b/g) ?? []);
    expect(tokens.filter((token) => !authored.includes(token))).toEqual([]);
    // A measured zero is the normal case: nothing names it (MIK-R89 v2 rule 1, amended 9dde2b09).
    expect(slot.textContent).not.toContain('No recorded family');
    expect(within(slot).getByTestId('review-subject-meta').textContent).not.toContain('family');
  });
});

// MIK-R89 v2 rules 1 and 6, amended 9dde2b09 (D396; mockup `selected`): the selected invariant reads
// its heading (id, short title, effect, change), one meta line, the statement's word diff under the
// header that holds the wording-layout switch, its recorded steps and judgment, then its code; the
// rest of its read follows its code, and its provenance is in the closed Technical details. Nothing
// stands for no family, and nothing names the writer of a change the reviewed task wrote.
describe('the selected invariant reads content first', () => {
  const noFamily = captured<ReviewResult>('subjectReview.noFamily.captured.json').payload!.family_context!;
  type Payload = NonNullable<ReviewResult['payload']>;
  const follows = (first: Node, second: Node) =>
    Boolean(first.compareDocumentPosition(second) & Node.DOCUMENT_POSITION_FOLLOWING);

  // The moved member answer made the L181 subject's own: its revision selection and its recorded
  // steps name the selected invariant, and its reviewed task is the page's leaf.
  async function selected(edit: (payload: Payload) => void = () => undefined) {
    stubIntersection();
    servePage();
    let answer: Payload | undefined;
    serveSelectedAnswer((payload) => {
      const own = payload.knowledge.revision_selection!.record_id;
      payload.knowledge.revision_selection!.record_id = L181_SUBJECT;
      for (const record of payload.meaning_effects?.records ?? []) if (record.record === own) record.record = L181_SUBJECT;
      payload.candidate.leaf_id = PLACE.leaf;
      edit(payload);
      answer = payload;
    });
    renderPage();
    await openedInvariant();
    fireEvent.click(within(subjectSection()).getByTestId('review-subject-select'));
    await waitFor(() => expect(screen.getByTestId('review-subject-slot').dataset.slotState).toBe('answered'));
    const section = subjectSection();
    return { section, slot: within(section).getByTestId('review-subject-slot'), answer: answer! };
  }

  // The mockup's heading (shots/selected-1920.png; MIK-R89 v2 rule 1, d9a1ec8f): the statement's
  // lead clause as a bold unboxed title, out of the page's shared control look, and a red change badge.
  it('draws its heading as the mockup does: the lead clause unboxed, the change badge in red', async () => {
    const { section } = await selected();
    const title = within(section).getByTestId('review-subject-select');
    expect(title.textContent).toBe('A silent role-agent turn end');
    expect(title.getAttribute('title')).toContain('A silent role-agent turn end');
    expect(title.hasAttribute('data-subject-title')).toBe(true);
    expect(within(section).getByTestId('review-subject-change').dataset.change).toBe('changed');
  });

  // Item 6 of d9a1ec8f: the selected subject's prose keeps the mockup's reading measure (92ch,
  // narrower than the code) while the wording is inline; side by side spreads its two columns.
  it('keeps its prose at the reading measure, which side by side lifts', async () => {
    const { slot } = await selected();
    const content = within(slot).getByTestId('review-center-member');
    expect(content.dataset.measure).toBe('reading');
    act(() => proseLayoutStore.getState().setLayout('side-by-side'));
    expect(content.dataset.measure).toBe('wide');
    act(() => proseLayoutStore.getState().setLayout('inline'));
    expect(content.dataset.measure).toBe('reading');
  });

  // Item 8 of d9a1ec8f (tree-hidden beside the mockup): hiding the tree changes the column's width,
  // not the selected subject's composition or its reading measure.
  it('keeps that composition and measure with the tree hidden', async () => {
    const { section, slot } = await selected();
    fireEvent.keyDown(document.body, { key: '[' });
    expect(screen.getByTestId('review-tree-column').dataset.open).toBe('false');
    expect(within(slot).getByTestId('review-center-member').dataset.measure).toBe('reading');
    const order = [
      within(section).getByTestId('review-subject-select'),
      within(slot).getByTestId('review-subject-meta'),
      within(slot).getByTestId('review-center-statement-label'),
      within(slot).getAllByTestId('intent-diff')[0],
      within(slot).getByTestId('review-center-judgment'),
      within(section).getAllByTestId('review-expression-card')[0],
    ];
    for (let index = 1; index < order.length; index += 1) expect(follows(order[index - 1], order[index])).toBe(true);
  });

  it('opens with its heading, one meta line, the word diff and its switch, the recorded judgment, then its code', async () => {
    const { section, slot, answer } = await selected();
    const title = within(section).getByTestId('review-subject-select');
    expect(title.textContent).not.toContain('INV-1ZKP4R');
    expect(title.getAttribute('title')).toContain(title.textContent!);
    expect(within(section).getAllByTestId('review-subject-effect').length).toBeGreaterThan(0);
    const meta = within(slot).getByTestId('review-subject-meta');
    const family = answer.family_context!.entries[0].display_label!;
    expect(meta.textContent).toMatch(
      new RegExp(`^invariant · ${family} · revision 2 → 4 · \\d+ locations: \\d+ changed, \\d+ unchanged, \\d+ not comparable · \\d+ proofs?(, all current|: )`),
    );
    expect(within(meta).queryByTestId('review-subject-family-chip')).toBeNull();
    expect(within(meta).queryByTestId('review-subject-carried')).toBeNull();
    const label = within(slot).getByTestId('review-center-statement-label');
    const control = within(slot).getByTestId('intent-diff-layout-control');
    expect(control.parentElement).toBe(label.parentElement);
    const order = [
      within(section).getByTestId('review-subject-select'),
      meta,
      label,
      within(slot).getAllByTestId('intent-diff')[0],
      within(slot).getByTestId('review-recorded-steps'),
      within(slot).getByTestId('review-center-judgment'),
      within(section).getAllByTestId('review-expression-card')[0],
      within(section).getByTestId('review-subject-after-code'),
    ];
    for (let index = 1; index < order.length; index += 1) expect(follows(order[index - 1], order[index])).toBe(true);
    // Before the code: no frame, origin, hand-off, revision id or family sentence.
    const selection = answer.knowledge.revision_selection!;
    for (const text of ['Selected invariant', 'Selected intent', 'origin', 'introduced by', 'Hand-off', 'family contexts read',
      selection.before_revision_id!, selection.after_revision_id!, answer.family_context!.detail])
      expect(slot.textContent).not.toContain(text);
  });

  it('shows nothing for no family and keeps the provenance in the closed Technical details', async () => {
    const { slot, answer } = await selected((payload) => {
      payload.family_context = structuredClone(noFamily);
      // The selection names each side's display version (9dde2b09), so a subject no family names
      // still reads "revision n → m" with no member rows to take it from.
      Object.assign(payload.knowledge.revision_selection!, { before_display_version: 'r2', after_display_version: 'r4' });
    });
    const meta = within(slot).getByTestId('review-subject-meta');
    expect(meta.textContent).toMatch(/^invariant · revision 2 → 4 · \d+ locations: /);
    for (const text of ['No recorded family', '0 of 0', 'no family', 'carried in']) expect(slot.textContent).not.toContain(text);
    const fold = screen.getByTestId('review-details') as HTMLDetailsElement;
    expect(fold.open).toBe(false);
    const provenance = within(fold).getByTestId('review-subject-provenance');
    const origin = answer.knowledge.after_origin!;
    expect(within(provenance).getByTestId('review-origin').textContent).toContain(`introduced by ${origin.leaf}`);
    expect(provenance.textContent).not.toMatch(/(before|after) origin/);
    for (const text of [...origin.handoff!.evidence!, answer.knowledge.revision_selection!.after_revision_id!, noFamily.detail])
      expect(provenance.textContent).toContain(text);
  });

  it('names an unread family as one short chip, its sentence in the fold', async () => {
    const { slot } = await selected((payload) => {
      payload.family_context = { ...structuredClone(noFamily), state: 'unavailable', detail: 'neither snapshot could be read' };
    });
    const chip = within(within(slot).getByTestId('review-subject-meta')).getByTestId('review-subject-family-chip');
    expect(chip.textContent).toBe('family context unavailable');
    expect(slot.textContent).not.toContain('neither snapshot could be read');
    expect(screen.getByTestId('review-family-context-detail').textContent).toContain('neither snapshot could be read');
  });

  // The chip names the after revision's writer as its tree's history files record it (MIK-R89 v2
  // rules 1 and 6, 9dde2b09), here a sync's crossing owner.
  it('names a change the reviewed task did not write with one chip that names its source', async () => {
    const { slot } = await selected((payload) => {
      payload.judgment = { ...payload.judgment, state: 'none', rows: [], detail: 'This leaf recorded no judgment about this record' };
      const steps = payload.meaning_effects!.records.find((record) => record.record === L181_SUBJECT)!;
      Object.assign(steps, { state: 'none', steps: [], detail: 'No meaning effect recorded by this leaf' });
      payload.knowledge.revision_selection!.after_writer = {
        state: 'recorded',
        owners: ['260928-MIK-crossing-2'],
        detail: 'revision 4 of INV-1ZKP4R is the changed row of 260928-MIK-crossing-2',
      };
    });
    const meta = within(slot).getByTestId('review-subject-meta');
    const chip = within(meta).getByTestId('review-subject-carried');
    expect(chip.textContent).toBe('carried in from 260928-MIK-crossing-2');
    expect(chip.getAttribute('title')).toBe('revision 4 of INV-1ZKP4R is the changed row of 260928-MIK-crossing-2');
    expect(meta.textContent).not.toContain('not written by');
  });
});

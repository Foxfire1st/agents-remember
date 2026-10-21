// MIK-R89 v2 rules 2, 5 and 7 on L181 #8 (reviewPage.test-utils.tsx): the page opens with nothing
// selected and makes exactly six reads; a lane file's two reads wait until the opening's six are done
// and the file is in view, with no gesture (amendment f43ae286), and a card's marks until the card is
// in view after the reader's first gesture; each subject shows its two closed groups; the one "All
// changes" link is the tree's.
import { act, cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { resetCardCachesForTests } from '../../../data/reviewTrees';
import { ReadGate, READ_BATCH } from './readGate';
import {
  engage,
  fileReads,
  LANE_PATH,
  L181_SUBJECT,
  pageReads,
  renderPage,
  serveLaneText,
  servePage,
  stubInViewAtFirstPaint,
  stubIntersection,
} from './reviewPage.test-utils';

beforeEach(() => {
  window.localStorage.clear();
  window.history.replaceState(null, '', '/');
  // Each case counts its own reads: no card read is carried over from an earlier case.
  resetCardCachesForTests();
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

async function opened() {
  await waitFor(() => expect(screen.getAllByTestId('review-subject-section').length).toBeGreaterThan(0));
  await waitFor(() => expect(screen.getByTestId('review-lane-section').dataset.laneState).toBe('ready'));
  await waitFor(() => expect(screen.queryByTestId('review-subject-cards-reading')).toBeNull());
}

function kindOf(url: URL): string {
  if (url.pathname !== '/api/review/trees') return url.pathname;
  if (url.searchParams.has('invariants')) return 'trees:cards';
  if (url.searchParams.has('lane')) return 'trees:lane';
  if (url.searchParams.has('file')) return 'trees:file';
  return 'trees:leaf';
}

const OPENING = [
  '/api/review/intent',
  '/api/review/intent/entries',
  '/api/review/intent/summary',
  'trees:cards',
  'trees:lane',
  'trees:leaf',
].sort();

// A read's kind and the file it names.
const fileOf = (url: URL) => [kindOf(url), url.searchParams.get('file') ?? url.searchParams.get('path')];

// Event-loop turns: the served reads answer at once, so whatever an answer starts has started after
// them (no wall-clock wait).
async function turns(count = 5) {
  for (let turn = 0; turn < count; turn += 1) await act(async () => new Promise((resolve) => setTimeout(resolve, 0)));
}

describe('the opening', () => {
  it('makes exactly the six opening reads and selects nothing', async () => {
    stubIntersection();
    const { asked } = servePage();
    renderPage();
    await opened();
    await turns();
    const reads = pageReads(asked).map(kindOf).sort();
    expect(reads).toEqual(OPENING);
    const cards = pageReads(asked).find((url) => kindOf(url) === 'trees:cards')!;
    expect(cards.searchParams.get('invariants')).toBe(L181_SUBJECT);
    expect(cards.searchParams.get('comparison')).toBe('8');
    expect(pageReads(asked).find((url) => url.pathname === '/api/review/intent')!.searchParams.has('selectorId')).toBe(false);
    expect(document.querySelector('[data-review-scroller] :is(article, section, li)[data-selected]')).toBeNull();
    expect(document.querySelector('[data-tree-node][aria-current]')).toBeNull();
    expect(screen.queryByTestId('review-subject-slot')).toBeNull();
    expect(screen.getByTestId('review-summary').textContent).toContain('1 invariant changed');
  });

  // A short page: every block is in view at the first paint and the reader has nothing to scroll. The
  // lane file is read once the opening's six reads are done (here the card read is held, so they are
  // not), with no gesture; until its read arrives it shows its skeleton, never "Read when in view…".
  // A card's marks still wait for the reader's first gesture.
  it('reads a lane file in view at the first paint once the six opening reads are done, with no gesture', async () => {
    stubInViewAtFirstPaint();
    const { asked, release } = servePage((url) => kindOf(url) === 'trees:cards');
    renderPage();
    await waitFor(() => expect(screen.getByTestId('review-lane-section').dataset.laneState).toBe('ready'));
    await waitFor(() => expect(pageReads(asked).map(kindOf).sort()).toEqual(OPENING));
    await turns();
    expect(fileReads(asked)).toEqual([]);
    const file = screen.getByTestId('review-lane-file');
    expect(file.dataset.laneRead).toBe('waiting');
    expect(document.body.textContent).not.toContain('Read when in view');

    await act(async () => release());
    await waitFor(() => expect(file.dataset.laneRead).toBe('ready'));
    const reads = pageReads(asked);
    expect(reads.slice(0, 6).map(kindOf).sort()).toEqual(OPENING);
    // Then the file's two reads, its classification and its text, counted by file.
    expect(reads.slice(6).map(fileOf).sort()).toEqual(
      [
        ['/api/changeset/file-diff', LANE_PATH],
        ['trees:file', LANE_PATH],
      ].sort(),
    );
    // The reader's first gesture starts the card marks' reads of what is in view, through the gate.
    engage();
    await waitFor(() => expect(fileReads(asked).length).toBeGreaterThan(2));
    expect(fileReads(asked).slice(2).every((url) => kindOf(url) === 'trees:file' && url.searchParams.get('file') !== LANE_PATH)).toBe(true);
  });

  // Below the fold: the opening makes its six reads and no other. The file scrolls into view (the
  // page's own scroll or the reader's) and is read then, with no gesture.
  it("reads a lane file's classification and text as it comes into view, with no gesture, and a card's marks once in view", async () => {
    const view = stubIntersection();
    const { asked } = servePage();
    renderPage();
    await opened();
    await turns();
    expect(pageReads(asked).map(kindOf).sort()).toEqual(OPENING);
    const laneFile = screen.getByTestId('review-lane-file');
    await act(async () => view.show(laneFile));
    await waitFor(() => expect(screen.getByTestId('review-lane-file').dataset.laneRead).toBe('ready'));
    expect(fileReads(asked).map(fileOf).sort()).toEqual(
      [
        ['/api/changeset/file-diff', LANE_PATH],
        ['trees:file', LANE_PATH],
      ].sort(),
    );
    engage();
    const card = screen.getAllByTestId('review-expression-card')[0];
    const before = fileReads(asked).length;
    await act(async () => view.show(card));
    await waitFor(() => expect(fileReads(asked).length).toBeGreaterThan(before));
    expect(fileReads(asked).slice(before).every((url) => url.searchParams.has('file'))).toBe(true);
  });

  it('lets at most READ_BATCH file reads run at a time', async () => {
    const gate = new ReadGate();
    const waits: (() => void)[] = [];
    const tasks = Array.from({ length: READ_BATCH + 4 }, () =>
      gate.run(() => new Promise<void>((resolve) => waits.push(resolve))),
    );
    await Promise.resolve();
    expect(gate.running).toBe(READ_BATCH);
    expect(waits).toHaveLength(READ_BATCH);
    waits.splice(0, 2).forEach((go) => go());
    await waitFor(() => expect(waits).toHaveLength(READ_BATCH - 2 + 2));
    expect(gate.running).toBe(READ_BATCH);
    waits.forEach((go) => go());
    await waitFor(() => expect(gate.running).toBeLessThanOrEqual(READ_BATCH));
    void tasks;
  });
});

describe('the two closed groups (rule 5)', () => {
  it('shows 31 unchanged and 28 not comparable, each with its first four lines then the rest', async () => {
    stubIntersection();
    servePage();
    renderPage();
    await opened();
    const section = screen.getAllByTestId('review-subject-section').find((node) => node.dataset.subject === L181_SUBJECT)!;
    const groups = within(section).getAllByTestId('review-location-group');
    expect(groups.map((group) => group.dataset.group)).toEqual(['unchanged', 'not-comparable']);
    const expected = { unchanged: [31, 27], 'not-comparable': [28, 24] } as const;
    for (const group of groups) {
      const [count, more] = expected[group.dataset.group as keyof typeof expected];
      const toggle = within(group).getByTestId('review-location-group-toggle');
      expect(toggle.textContent).toContain(String(count));
      expect(within(group).queryByTestId('review-location-group-rows')).toBeNull();
      fireEvent.click(toggle);
      expect(within(group).getAllByTestId('review-location-line')).toHaveLength(4);
      expect(within(group).getByTestId('review-location-group-more').textContent).toBe(`… ${more} more, one line each`);
    }
  });

  it('opens a group from its tree row and brings the column to it, leaving the tree as it is', async () => {
    stubIntersection();
    servePage();
    renderPage();
    await opened();
    const scroller = document.querySelector<HTMLElement>('[data-review-scroller]')!;
    const tree = document.querySelector<HTMLElement>('[data-review-tree-panel]')!;
    const sets: number[] = [];
    let top = 0;
    Object.defineProperty(scroller, 'scrollTop', {
      configurable: true,
      get: () => top,
      set: (value: number) => {
        top = value;
        sets.push(value);
      },
    });
    const treeTop = tree.scrollTop;
    const row = screen.getAllByTestId('review-tree-group').find((node) => node.dataset.group === 'undetermined')!;
    const section = screen.getAllByTestId('review-subject-section').find((node) => node.dataset.subject === L181_SUBJECT)!;
    const group = within(section).getAllByTestId('review-location-group').find((node) => node.dataset.group === 'not-comparable')!;
    vi.spyOn(group, 'getBoundingClientRect').mockReturnValue({ top: 640, bottom: 700, left: 0, right: 0, width: 0, height: 60, x: 0, y: 640, toJSON: () => ({}) });
    fireEvent.click(row);
    await waitFor(() => expect(within(group).queryByTestId('review-location-group-rows')).not.toBeNull());
    expect(sets).toHaveLength(1);
    expect(sets[0]).toBeGreaterThan(600);
    expect(tree.scrollTop).toBe(treeTop);
    expect(screen.getByTestId('review-tree-column').dataset.open).toBe('true');
  });
});

describe("the lane's added-file cap", () => {
  const lines = (count: number) => Array.from({ length: count }, (_, index) => `export const n${index + 1} = ${index + 1};`).join('\n');

  it.each([
    [30, null],
    [143, '113 more added lines'],
  ])('does not count the trailing newline of a %i-line file as a line (more: %s)', async (count, more) => {
    stubInViewAtFirstPaint();
    servePage();
    serveLaneText(`${lines(count)}\n`);
    renderPage();
    await opened();
    engage();
    await waitFor(() => expect(screen.getByTestId('review-lane-file').dataset.laneRead).toBe('ready'));
    const button = screen.queryByTestId('review-lane-more');
    if (more === null) expect(button).toBeNull();
    else expect(button?.textContent).toContain(more);
  });
});

describe('one way to all changes (rule 7)', () => {
  it("offers the tree's one All changes link and no per-card copy", async () => {
    stubIntersection();
    servePage();
    renderPage();
    await opened();
    const links = screen.getAllByRole('button').filter((node) => /All changes/.test(node.textContent ?? ''));
    expect(links).toHaveLength(1);
    expect(links[0].closest('[data-testid="review-tree"]')).not.toBeNull();
    expect(document.body.textContent).not.toMatch(/All changed files/);
    expect(screen.queryByTestId('review-worklist-button')).toBeNull();
  });

  // Rule 4: "Expand full file" goes on the review page; "Changes only | Full context" is the one control.
  it('offers no per-card Full file', async () => {
    stubIntersection();
    servePage();
    renderPage();
    await opened();
    expect(screen.getAllByTestId('review-expression-card').length).toBeGreaterThan(0);
    expect(screen.queryAllByTestId('review-card-full-file')).toHaveLength(0);
    for (const card of screen.getAllByTestId('review-expression-card')) expect(card.textContent).not.toMatch(/Full file/);
    expect(screen.getByTestId('review-surface').hasAttribute('data-full-file')).toBe(false);
  });
});

// Finding 4: the line counts invariants whose own record did not change while their code did (the
// tree's "code changed" rows). L181 #8's one invariant changed its record, so the count is 0 while its
// cards and the lane hold changed code; the line must not read as "no code changed".
describe("the tree's code-facts line", () => {
  it('states what it counts: invariants changed only in their code', async () => {
    stubIntersection();
    servePage();
    renderPage();
    await opened();
    const line = screen.getByTestId('review-code-facts-status');
    await waitFor(() => expect(line.textContent).not.toBe(''));
    expect(line.textContent).toBe('No invariant changed only in its code.');
    expect(screen.getAllByTestId('review-expression-card').length).toBeGreaterThan(0);
  });
});

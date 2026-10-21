// MIK-R89 v2 rule 9, "The page never moves under the reader" (amendment 10fb84cb), on L181 #8
// (reviewPage.test-utils.tsx), as the sandbox measured it (E7 on A2-F1, dev2 to dev5): the reader
// selects INV-1ZKP4R while its cards are still being read and scrolls on; the subject's slot then
// fills (or fails), its cards land, and lane files are read once in view. The reader's line is the
// first settled block at the top of the column's view, under its sticky head; the pointer plays no
// part. Reads that land above the line or at it, and the browser's clamp, leave the line within 1 px;
// corrections never add up to a drift (dev2 b); a clamp is no new place, within a gesture too (dev2 c,
// dev5 E7 at 1920); a header's ⊕ at the line stays clear of the head, and one brought into view with
// `block: 'start'` comes in below it (dev5 R188); a waiting lane file reserves the hunk rows its read
// draws, so that only its diff body grows. jsdom has no layout: readerLine.test-utils.tsx lays the
// column out. A reader's line inside an editor has its own cases (readerAnchor.editors.test.tsx).
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { useRef } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { css } from '../../../../styled-system/css';
import { resetCardCachesForTests } from '../../../data/reviewTrees';
import { l181Answer } from '../changeSetOrder.test-utils';
import { scrollColumnTo } from './blockDom';
import { useReaderAnchor } from './readerAnchor';
import { heldSkeleton } from './readingStyles';
import {
  column,
  COLUMN_TOP,
  gestureOver,
  installAnchorClock,
  mockLayout,
  nextFrame,
  scrollTo,
  settle,
  VIEWPORT,
} from './readerLine.test-utils';
import {
  isSelectionRead,
  L181_SUBJECT,
  LANE_PATH,
  renderPage,
  servePage,
  stubInViewAtFirstPaint,
  stubIntersection,
} from './reviewPage.test-utils';

// Holds the subject's read and the card read until released; a released read may be answered with
// a failure instead.
function holdReads() {
  const served = globalThis.fetch;
  const waiting = new Map<string, ((failure?: Response) => void)[]>();
  const kindOf = (url: URL) =>
    isSelectionRead(url) ? 'subject' : url.searchParams.has('invariants') ? 'cards' : null;
  vi.stubGlobal(
    'fetch',
    vi.fn(async (address: string | URL, init?: RequestInit) => {
      const kind = kindOf(new URL(String(address), 'http://localhost'));
      if (kind === null) return served(address, init);
      const failure = await new Promise<Response | undefined>((go) => waiting.set(kind, [...(waiting.get(kind) ?? []), go]));
      return failure ?? served(address, init);
    }),
  );
  return {
    release: (kind: 'subject' | 'cards', failure?: Response) => (waiting.get(kind) ?? []).splice(0).forEach((go) => go(failure)),
  };
}

beforeEach(() => {
  window.localStorage.clear();
  window.history.replaceState(null, '', '/');
  resetCardCachesForTests();
  installAnchorClock();
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

const subjectSection = () =>
  screen.getAllByTestId('review-subject-section').find((node) => node.dataset.subject === L181_SUBJECT)!;
const slotState = () => screen.getByTestId('review-subject-slot').dataset.slotState;
const topOf = (testid: string) => screen.getByTestId(testid).getBoundingClientRect().top;

// The page opened with its cards read and its lane listed, the column laid out.
async function openedPage(extra: [string, number][] = []) {
  stubIntersection();
  const layout = mockLayout(extra);
  servePage();
  renderPage();
  await waitFor(() => expect(within(subjectSection()).getAllByTestId('review-expression-card').length).toBeGreaterThan(0));
  await waitFor(() => expect(screen.getByTestId('review-lane-section').dataset.laneState).toBe('ready'));
  layout.attach(column());
  return layout;
}

// The lane lists these files in place of its one composed file (call after `servePage`).
function serveLaneFiles(paths: string[]) {
  const served = globalThis.fetch;
  vi.stubGlobal(
    'fetch',
    vi.fn(async (address: string | URL, init?: RequestInit) => {
      const response = await served(address, init);
      if (!new URL(String(address), 'http://localhost').searchParams.has('lane')) return response;
      const body = await response.json();
      const destination = body.lane.unexplained_changes;
      destination.files = paths.map((path) => ({ ...destination.files[0], path }));
      body.lane.paths = paths.map((path) => ({ path, bucket: 'unexplained' }));
      return new Response(JSON.stringify(body), { status: 200, headers: { 'content-type': 'application/json' } });
    }),
  );
}

// Each changed card's file classification (asked once the card is in view) gets one hunk on the first
// line of each side the card shows, under the card's blobs, so that its excerpt draws one mark: the
// mark's read lands and the excerpt's pane is built again with it (call after `servePage`).
function serveCardMarks() {
  const served = globalThis.fetch;
  const blobs = new Map((l181Answer.entries ?? []).map((entry) => [entry.after.path ?? entry.before.path, entry]));
  const start = (range: string | undefined) => Number(/^L(\d+)/.exec(range ?? '')?.[1] ?? 1);
  vi.stubGlobal(
    'fetch',
    vi.fn(async (address: string | URL, init?: RequestInit) => {
      const response = await served(address, init);
      const path = new URL(String(address), 'http://localhost').searchParams.get('file');
      const card = path ? document.querySelector<HTMLElement>(`[data-testid="review-expression-card"][data-path="${path}"]`) : null;
      const entry = path ? blobs.get(path) : undefined;
      if (!card || !entry || card.dataset.cardChange !== 'changed') return response;
      const body = await response.json();
      const file = body.file_classification;
      file.sides = file.sides.map((side: { side: 'before' | 'after' }) => ({ ...side, blob: entry[side.side].blob }));
      const hunk = { before: { start: start(card.dataset.beforeRange), count: 1 }, after: { start: start(card.dataset.afterRange), count: 1 } };
      file.hunks = [{ ...hunk, classification: 'unexplained', links: [], unknown: [] }];
      file.counts = { hunks: 1, linked: 0, unexplained: 1, attribution_unknown: 0 };
      return new Response(JSON.stringify(body), { status: 200, headers: { 'content-type': 'application/json' } });
    }),
  );
}

// A ResizeObserver that reports its element once it is observed and after every change inside it, as
// the browser does when an observation starts and after a layout that changed the element's size.
function stubResizeObserver() {
  class Observer {
    private readonly watchers: MutationObserver[] = [];
    constructor(private readonly callback: ResizeObserverCallback) {}
    observe(node: Element) {
      const report = () => this.callback([{ target: node } as unknown as ResizeObserverEntry], this as unknown as ResizeObserver);
      const watcher = new MutationObserver(report);
      watcher.observe(node, { childList: true, subtree: true, attributes: true, characterData: true });
      this.watchers.push(watcher);
      queueMicrotask(() => {
        if (this.watchers.includes(watcher)) report();
      });
    }
    unobserve() {}
    disconnect() {
      this.watchers.splice(0).forEach((watcher) => watcher.disconnect());
    }
  }
  vi.stubGlobal('ResizeObserver', Observer);
}


describe('the page never moves under the reader (rule 9, E7)', () => {
  it.each([
    ['fills', 'answered', undefined],
    ['fails', 'unavailable', () => new Response('upstream down', { status: 503 })],
  ] as const)(
    'keeps the line when the slot %s, the cards land and a lane file is read above it, once the reader scrolled',
    async (_outcome, state, failure) => {
      const intersection = stubIntersection();
      const layout = mockLayout();
      servePage();
      const reads = holdReads();
      renderPage();
      await waitFor(() => expect(within(subjectSection()).getByTestId('review-subject-cards-reading')).toBeTruthy());
      await waitFor(() => expect(screen.getByTestId('review-lane-section').dataset.laneState).toBe('ready'));
      layout.attach(column());
      fireEvent.click(within(subjectSection()).getByTestId('review-subject-select'));
      await waitFor(() => expect(slotState()).toBe('pending'));
      // The heading, the pending slot and the cards being read are above the reader's line; the lane
      // section is the first block below it.
      scrollTo(40);
      const lane = topOf('review-lane-section');
      expect(lane).toBe(158);

      await act(async () => reads.release('subject', failure?.()));
      await waitFor(() => expect(slotState()).toBe(state));
      expect(Math.abs(topOf('review-lane-section') - lane)).toBeLessThanOrEqual(1);

      await act(async () => reads.release('cards'));
      await waitFor(() => expect(within(subjectSection()).getAllByTestId('review-expression-card').length).toBeGreaterThan(0));
      expect(Math.abs(topOf('review-lane-section') - lane)).toBeLessThanOrEqual(1);

      // The lane file waits for its read above the reader's line; Technical details is the line.
      const file = screen.getByTestId('review-lane-file');
      scrollTo(column().scrollTop + file.getBoundingClientRect().bottom - COLUMN_TOP - 10);
      const details = topOf('review-details');
      expect(details).toBe(COLUMN_TOP + 10);
      act(() => intersection.show(file));
      await waitFor(() => expect(file.dataset.laneRead).toBe('ready'));
      expect(Math.abs(topOf('review-details') - details)).toBeLessThanOrEqual(1);
    },
  );

  // dev2 (b): every correction aims at the line's first target. 20 growths of 10.37 px above the line
  // each leave the whole-pixel scroll 0.37 px short; re-taking the line after each added them up.
  it('keeps the line within 1 px of its target through many fractional corrections', async () => {
    await openedPage();
    const file = screen.getByTestId('review-lane-file');
    // The lane file is the line, 10 px under the column's top.
    scrollTo(column().scrollTop + file.getBoundingClientRect().top - COLUMN_TOP - 10);
    const target = file.getBoundingClientRect().top;
    expect(target).toBe(COLUMN_TOP + 10);
    const card = screen.getAllByTestId('review-expression-card')[0];
    for (let step = 1; step <= 20; step += 1) {
      await act(async () => {
        card.style.height = `${400 + 10.37 * step}px`;
      });
      expect(Math.abs(file.getBoundingClientRect().top - target)).toBeLessThanOrEqual(1);
    }
  });

  // dev2 (c), held off since dev6: content below the line shrinks until the column's end is above its
  // scroll position, with no gesture. The browser would clamp the column and drop the line; before it
  // paints, the spacer at the column's end takes the room the shrink took, so the column and the line
  // stay. When the content grows again, the spacer goes.
  it('holds a clamp off when content below the line shrinks, and lets the spacer go as the content grows again', async () => {
    await openedPage();
    const file = screen.getByTestId('review-lane-file');
    const details = screen.getByTestId('review-details');
    scrollTo(column().scrollTop + file.getBoundingClientRect().top - COLUMN_TOP - 10);
    const target = file.getBoundingClientRect().top;
    expect(target).toBe(COLUMN_TOP + 10);
    await gestureOver();
    const at = column().scrollTop;
    const spacer = () => column().querySelector<HTMLElement>('[data-reading-spacer]')!;

    await act(async () => {
      details.style.height = '100px';
    });
    await settle();
    expect(Math.abs(file.getBoundingClientRect().top - target)).toBeLessThanOrEqual(1);
    expect(column().scrollTop).toBe(at);
    expect(column().scrollTop).toBe(column().scrollHeight - VIEWPORT);
    expect(parseFloat(spacer().style.height)).toBeGreaterThan(0);

    await act(async () => {
      details.style.height = '1000px';
    });
    await settle();
    expect(Math.abs(file.getBoundingClientRect().top - target)).toBeLessThanOrEqual(1);
    expect(spacer().style.height).toBe('');
  });

  // dev5 E7 at 1920 (…062402 run 1): 387 ms after a wheel the anchor corrected +1,033 for growth above
  // the line; then the content above it shrank while the column was 52 px from its end, the browser
  // clamped the column, and that clamp, still within the wheel's gesture, was taken as the reader's
  // scroll: the line was taken again 52 px up and never came back. Here the same sequence: the line
  // (Technical details, the block at the column's top) is corrected, a card above shrinks so that the
  // column is clamped, the card grows by 28 and shrinks by 28 again, then grows back.
  it("takes a clamp within a gesture for no new place: the line comes back to its target", async () => {
    const layout = await openedPage();
    const details = screen.getByTestId('review-details');
    const card = screen.getAllByTestId('review-expression-card')[0];
    // The column 52 px from its end: Technical details holds the column's top.
    scrollTo(column().scrollHeight - VIEWPORT - 52);
    expect(column().scrollHeight - VIEWPORT - column().scrollTop).toBe(52);
    const target = details.getBoundingClientRect().top;
    expect(target).toBeLessThan(COLUMN_TOP);
    const height = (px: number) =>
      act(async () => {
        card.style.height = `${px}px`;
      });

    await height(400 + 1033);
    expect(Math.abs(details.getBoundingClientRect().top - target)).toBeLessThanOrEqual(1);
    await height(400 + 1033 - 202);
    expect(layout.clamps()).toBe(1);
    await height(400 + 1033 - 202 + 28);
    await height(400 + 1033 - 202);
    await height(5000);
    await settle();
    expect(Math.abs(details.getBoundingClientRect().top - target)).toBeLessThanOrEqual(1);
  });

  // The column is scrolled so that a lane file starts just under the column's top: the file is the
  // line's block. Its body below its header is read and draws its diff: the growth stays below, and
  // the header and its ⊕ stay where they are.
  it("keeps a lane file at the column's top, and its ⊕, while its body below the header is read", async () => {
    const intersection = stubIntersection();
    const layout = mockLayout();
    servePage();
    renderPage();
    await waitFor(() => expect(within(subjectSection()).getAllByTestId('review-expression-card').length).toBeGreaterThan(0));
    await waitFor(() => expect(screen.getByTestId('review-lane-section').dataset.laneState).toBe('ready'));
    layout.attach(column());
    fireEvent.click(within(subjectSection()).getByTestId('review-subject-select'));
    await waitFor(() => expect(slotState()).toBe('answered'));
    const file = screen.getByTestId('review-lane-file');
    const header = file.firstElementChild!;
    const add = within(header as HTMLElement).getByRole('button', { name: /AR tray/ });
    scrollTo(column().scrollTop + header.getBoundingClientRect().top - COLUMN_TOP - 10);
    expect(header.getBoundingClientRect().top).toBe(COLUMN_TOP + 10);
    const plus = add.getBoundingClientRect().top;

    act(() => intersection.show(file));
    await waitFor(() => expect(file.dataset.laneRead).toBe('ready'));
    expect(file.getBoundingClientRect().height).toBe(900);
    expect(Math.abs(header.getBoundingClientRect().top - (COLUMN_TOP + 10))).toBeLessThanOrEqual(1);
    expect(Math.abs(add.getBoundingClientRect().top - plus)).toBeLessThanOrEqual(1);
  });

  // The column's top falls on a waiting lane file's "Read when in view…" line, which is pending: the
  // line's block is the next settled block, Technical details. The file's read lands above it, and
  // Technical details stays.
  it('keeps the next settled block when the column\'s top falls on a waiting lane file\'s "Read when in view…" line', async () => {
    const intersection = stubIntersection();
    const layout = mockLayout();
    servePage();
    renderPage();
    await waitFor(() => expect(within(subjectSection()).getAllByTestId('review-expression-card').length).toBeGreaterThan(0));
    await waitFor(() => expect(screen.getByTestId('review-lane-section').dataset.laneState).toBe('ready'));
    layout.attach(column());
    fireEvent.click(within(subjectSection()).getByTestId('review-subject-select'));
    await waitFor(() => expect(slotState()).toBe('answered'));
    const file = screen.getByTestId('review-lane-file');
    const waiting = within(file).getByTestId('review-lane-file-waiting');
    // The waiting line at 90–111, across the column's top; Technical details from 111.
    scrollTo(column().scrollTop + waiting.getBoundingClientRect().top - (COLUMN_TOP - 10));
    expect(waiting.getBoundingClientRect().top).toBe(COLUMN_TOP - 10);
    const details = topOf('review-details');
    expect(details).toBe(COLUMN_TOP + 11);

    act(() => intersection.show(file));
    await waitFor(() => expect(file.dataset.laneRead).toBe('ready'));
    expect(file.getBoundingClientRect().height).toBe(900);
    expect(Math.abs(topOf('review-details') - details)).toBeLessThanOrEqual(1);
  });

  // dev5 (probe-lane-add, R188 "Add file"): a product scroll puts a lane file just under the sticky
  // head, as a tree row's selection does; the pointer then crosses the column from below onto that
  // file's ⊕, one move a frame, while the file's own read lands (its body grows below its header) and
  // then the file above it is read (growth above the line, corrected). The ⊕ stays where it was, clear
  // of the head, at every step: the pointer takes no line, and a correction puts the line's block back
  // at its target, which is below the head. Taking the line under the moving pointer lifted the ⊕
  // 1,236 px out of view.
  it("keeps a lane ⊕ at the top line clear of the sticky head while the pointer crosses to it and reads land", async () => {
    const intersection = stubIntersection();
    const layout = mockLayout([['[data-reading-head]', 44]]);
    servePage();
    const above = LANE_PATH.replace('added.ts', 'above.ts');
    const below = LANE_PATH.replace('added.ts', 'below.ts');
    serveLaneFiles([above, LANE_PATH, below]);
    renderPage();
    await waitFor(() => expect(within(subjectSection()).getAllByTestId('review-expression-card').length).toBeGreaterThan(0));
    await waitFor(() => expect(screen.getAllByTestId('review-lane-file')).toHaveLength(3));
    layout.attach(column());
    const files = screen.getAllByTestId('review-lane-file');
    const head = column().querySelector('[data-reading-head]')!.getBoundingClientRect().bottom;
    expect(head).toBe(COLUMN_TOP + 44);
    act(() => {
      scrollColumnTo(column(), files[1]);
    });
    await settle();
    const add = within(files[1].firstElementChild as HTMLElement).getByRole('button', { name: /AR tray/ });
    const plus = add.getBoundingClientRect().top;
    expect(plus).toBe(head + 8);
    const stays = () => {
      expect(Math.abs(add.getBoundingClientRect().top - plus)).toBeLessThanOrEqual(1);
      expect(add.getBoundingClientRect().top).toBeGreaterThanOrEqual(head);
    };
    const point = async (y: number) => {
      column().dispatchEvent(new MouseEvent('pointermove', { clientX: 700, clientY: y, bubbles: true }));
      await nextFrame();
      stays();
    };

    for (const y of [520, 440, 360]) await point(y);
    act(() => intersection.show(files[1]));
    await waitFor(() => expect(files[1].dataset.laneRead).toBe('ready'));
    stays();
    for (const y of [280, 200, plus + 9]) await point(y);
    act(() => intersection.show(files[0]));
    await waitFor(() => expect(files[0].dataset.laneRead).toBe('ready'));
    await settle();
    stays();
  });

  // dev5 R188: Playwright's hover retries ended with its `scrollIntoView({ block: 'start' })` on the
  // lane ⊕, which left it under the sticky head; a keyboard user tabbing to the ⊕, or find-in-page, is
  // the same case. The column's scroll padding is the head's measured height, so the ⊕ comes in at the
  // head's bottom. The browser's scrollIntoView is modelled here: the block's top goes to the column's
  // top plus the column's scroll padding, which its class sets to `var(--reading-head-height)`.
  it("brings a lane ⊕ into view with block 'start' below the sticky head, not under it", async () => {
    const layout = mockLayout([['[data-reading-head]', 44]]);
    stubIntersection();
    servePage();
    renderPage();
    await waitFor(() => expect(within(subjectSection()).getAllByTestId('review-expression-card').length).toBeGreaterThan(0));
    await waitFor(() => expect(screen.getByTestId('review-lane-section').dataset.laneState).toBe('ready'));
    layout.attach(column());
    const padded = css({ scrollPaddingTop: 'var(--reading-head-height, 0px)' });
    vi.spyOn(Element.prototype, 'scrollIntoView').mockImplementation(function (this: Element) {
      const node = column();
      const padding = node.classList.contains(padded) ? parseFloat(node.style.getPropertyValue('--reading-head-height')) || 0 : 0;
      node.scrollTop = node.scrollTop + this.getBoundingClientRect().top - COLUMN_TOP - padding;
    });
    const head = column().querySelector('[data-reading-head]')!.getBoundingClientRect().bottom;
    expect(head).toBe(COLUMN_TOP + 44);
    const add = within(screen.getByTestId('review-lane-file').firstElementChild as HTMLElement).getByRole('button', {
      name: /AR tray/,
    });

    act(() => add.scrollIntoView({ block: 'start' }));
    await settle();
    expect(column().scrollTop).toBeGreaterThan(0);
    expect(add.getBoundingClientRect().top).toBeGreaterThanOrEqual(head);
    expect(add.getBoundingClientRect().top).toBe(head);
    expect(column().style.getPropertyValue('--reading-head-height')).toBe('44px');
  });

  // A waiting lane file draws its header, the hunk rows its read will draw (one per hunk the lane read
  // counts: the same list, each row with a label's line and a ⊕'s box, a neutral label) and its
  // "Read when in view…" line. Its read draws the rows in their place, in the same list, and only the
  // diff body below them grows: what follows the file moves by the diff body's growth alone.
  it("reserves a waiting lane file's hunk rows, so that its read grows only the diff body", async () => {
    const intersection = stubIntersection();
    const layout = mockLayout([['[data-testid="review-lane-hunks"]', 26]]);
    servePage();
    renderPage();
    await waitFor(() => expect(within(subjectSection()).getAllByTestId('review-expression-card').length).toBeGreaterThan(0));
    await waitFor(() => expect(screen.getByTestId('review-lane-section').dataset.laneState).toBe('ready'));
    layout.attach(column());
    const file = screen.getByTestId('review-lane-file');
    const list = within(file).getByTestId('review-lane-hunks');
    const reserved = within(list).getAllByTestId('review-lane-hunk-reserved');
    expect(reserved).toHaveLength(1);
    expect(reserved[0].textContent).toBe('hunk 1 ⊕');
    expect(list.getAttribute('aria-hidden')).toBe('true');
    const reservedLabel = reserved[0].querySelector('button:not([data-add-to-tray])')!;
    const reservedMark = reserved[0].querySelector('[data-add-to-tray]')!;
    const listTop = list.getBoundingClientRect().top;
    const waitingTop = within(file).getByTestId('review-lane-file-waiting').getBoundingClientRect().top;
    expect(waitingTop).toBe(listTop + 26);
    const details = topOf('review-details');

    // A wheel that does not scroll starts the in-view reads (rule 2).
    fireEvent.wheel(column(), { deltaY: 0 });
    act(() => intersection.show(file));
    await waitFor(() => expect(file.dataset.laneRead).toBe('ready'));
    expect(within(file).getByTestId('review-lane-hunks')).toBe(list);
    const rows = within(list).getAllByTestId('review-lane-hunk');
    expect(rows).toHaveLength(1);
    expect(within(list).queryAllByTestId('review-lane-hunk-reserved')).toHaveLength(0);
    expect(list.hasAttribute('aria-hidden')).toBe(false);
    // The same row: the read row's elements and classes (its label's button, its ⊕, hidden there).
    expect(reserved[0].className).toBe(rows[0].className);
    expect(reservedLabel.className).toBe(rows[0].querySelector('button:not([data-add-to-tray])')!.className);
    expect(reservedMark.className).toBe(within(rows[0]).getByRole('button', { name: /AR tray/ }).className);
    expect(reservedMark.parentElement!.className).toBe(css({ visibility: 'hidden' }));
    expect(list.getBoundingClientRect().top).toBe(listTop);
    expect(within(file).getByTestId('review-lane-diff').getBoundingClientRect().top).toBe(waitingTop);
    expect(topOf('review-details') - details).toBe(861 - 21);
  });

  // dev6 E7 at 1440 (…071506 r2, …074232 r1): a waiting lane file is 102.9 px; on its read 36 files
  // went to 88.1 px (its rows are drawn, the waiting line goes, and the diff pane draws from 0 px once
  // its language pack has loaded), and the shrink at the column's end forced a clamp. The part below
  // the header now keeps its waiting height until the diff has drawn. Heights here: a row 31, the
  // waiting line 21, the diff 0 until its editor is drawn, then 861.
  it('holds a lane file at its waiting height on its read until its diff has drawn, so that the read only grows it', async () => {
    const intersection = stubIntersection();
    const layout = mockLayout([
      ['[data-testid="review-lane-hunk"]', 31],
      ['[data-testid="review-lane-hunk-reserved"]', 31],
      ['[data-testid="review-lane-diff"]', (element) => (element.querySelector('.cm-editor') ? 861 : 0)],
    ]);
    servePage();
    renderPage();
    await waitFor(() => expect(within(subjectSection()).getAllByTestId('review-expression-card').length).toBeGreaterThan(0));
    await waitFor(() => expect(screen.getByTestId('review-lane-section').dataset.laneState).toBe('ready'));
    layout.attach(column());
    const file = screen.getByTestId('review-lane-file');
    const waiting = file.getBoundingClientRect().height;
    expect(waiting).toBe(39 + 31 + 21);
    const seen: { ready: boolean; drawn: boolean; height: number }[] = [];
    const watch = new MutationObserver(() =>
      seen.push({
        ready: file.dataset.laneRead === 'ready',
        drawn: file.querySelector('.cm-editor') !== null,
        height: file.getBoundingClientRect().height,
      }),
    );
    watch.observe(file, { subtree: true, childList: true, attributes: true, characterData: true });

    fireEvent.wheel(column(), { deltaY: 0 });
    act(() => intersection.show(file));
    await waitFor(() => expect(file.querySelector('.cm-editor')).not.toBeNull());
    await settle();
    watch.disconnect();
    expect(seen.some((record) => record.ready && !record.drawn)).toBe(true);
    expect(Math.min(...seen.map((record) => record.height))).toBeGreaterThanOrEqual(waiting);
    expect(file.getBoundingClientRect().height).toBe(39 + 31 + 861);
  });

  // dev7 (E7 …081538, …083734): with the reader at the column's end, lane files turning ready made the
  // browser clamp the column inside the read's commit (1920: 254 → 234): a layout of the file, shorter
  // for that moment, came before the layout effect that set its hold. The hold is now in the style the
  // ready commit writes, so every layout read from that commit on (here the mock's, at each read of a
  // box, the scroll position or the content's height) finds the file at its waiting height at least.
  it("draws a lane file's ready commit at its waiting height: no layout read inside the commit finds it shorter", async () => {
    const intersection = stubIntersection();
    const layout = mockLayout([
      ['[data-testid="review-lane-hunk"]', 31],
      ['[data-testid="review-lane-hunk-reserved"]', 31],
      ['[data-testid="review-lane-diff"]', (element) => (element.querySelector('.cm-editor') ? 861 : 0)],
    ]);
    servePage();
    renderPage();
    await waitFor(() => expect(within(subjectSection()).getAllByTestId('review-expression-card').length).toBeGreaterThan(0));
    await waitFor(() => expect(screen.getByTestId('review-lane-section').dataset.laneState).toBe('ready'));
    layout.attach(column());
    const file = screen.getByTestId('review-lane-file');
    const waiting = file.getBoundingClientRect().height;
    expect(waiting).toBe(39 + 31 + 21);
    const reads: { drawn: boolean; height: number; skeleton: boolean }[] = [];
    // The part below the header, held at its waiting height with skeleton lines until the diff draws.
    const held = file.children[1];
    // Every read from the ready commit on, up to the first one after the diff has drawn.
    layout.watch(() => {
      if (file.dataset.laneRead !== 'ready') return true;
      layout.fresh();
      const drawn = file.querySelector('.cm-editor') !== null;
      reads.push({ drawn, height: layout.rectOf(file).height, skeleton: held.classList.contains(heldSkeleton) });
      return !drawn;
    });

    fireEvent.wheel(column(), { deltaY: 0 });
    act(() => intersection.show(file));
    await waitFor(() => expect(file.querySelector('.cm-editor')).not.toBeNull());
    await settle();
    expect(reads.some((entry) => !entry.drawn)).toBe(true);
    expect(reads.some((entry) => entry.drawn)).toBe(true);
    expect(Math.min(...reads.map((entry) => entry.height))).toBeGreaterThanOrEqual(waiting);
    expect(reads.filter((entry) => !entry.drawn).every((entry) => entry.skeleton)).toBe(true);
    expect(file.getBoundingClientRect().height).toBe(39 + 31 + 861);
  });

  // D383 (c3794ebb): a lane file's diff body is drawn for the first time with no size from the
  // opening's reads (the lane read gives its hunk count, whose rows are reserved, and no line count),
  // so until its read arrives it shows skeleton lines at a default height, in view and while its read
  // is held (f43ae286: never "Read when in view…"). On its read the file keeps that height until its
  // diff has drawn, and then takes the diff's own height, here shorter than the default: one change of
  // height, never a shorter file in between. Heights: a row 31, the skeleton 80, the diff 0 until its
  // editor is drawn, then 40.
  it('shows a waiting lane file at a default height in skeleton lines, and holds it until its diff has drawn', async () => {
    stubResizeObserver();
    const intersection = stubIntersection();
    const layout = mockLayout([
      ['[data-testid="review-lane-hunk"]', 31],
      ['[data-testid="review-lane-hunk-reserved"]', 31],
      ['[data-testid="review-lane-file-waiting"]', 80],
      ['[data-testid="review-lane-diff"]', (element) => (element.querySelector('.cm-editor') ? 40 : 0)],
    ]);
    const { release } = servePage((url) => url.pathname === '/api/changeset/file-diff');
    renderPage();
    await waitFor(() => expect(within(subjectSection()).getAllByTestId('review-expression-card').length).toBeGreaterThan(0));
    await waitFor(() => expect(screen.getByTestId('review-lane-section').dataset.laneState).toBe('ready'));
    layout.attach(column());
    const file = screen.getByTestId('review-lane-file');
    const skeleton = within(file).getByTestId('review-lane-file-waiting');
    expect(skeleton.classList.contains(heldSkeleton)).toBe(true);
    expect(skeleton.classList.contains(css({ height: '5rem' }))).toBe(true);
    expect(file.textContent).not.toContain('Read when in view');
    expect(within(file).getAllByTestId('review-lane-hunk-reserved')).toHaveLength(1);
    const waiting = file.getBoundingClientRect().height;
    expect(waiting).toBe(39 + 31 + 80);
    const reads: { drawn: boolean; height: number }[] = [];
    // Every layout read from the file's coming into view to the first one after its diff has drawn.
    layout.watch(() => {
      layout.fresh();
      const drawn = file.querySelector('.cm-editor') !== null;
      reads.push({ drawn, height: layout.rectOf(file).height });
      return !drawn;
    });

    // In view after the opening: read with no gesture; its read is held, and the skeleton stays.
    act(() => intersection.show(file));
    await settle();
    expect(within(file).getByTestId('review-lane-file-waiting')).toBe(skeleton);
    await act(async () => release());
    await waitFor(() => expect(file.querySelector('.cm-editor')).not.toBeNull());
    await settle();
    expect(reads.some((entry) => !entry.drawn)).toBe(true);
    expect(Math.min(...reads.filter((entry) => !entry.drawn).map((entry) => entry.height))).toBeGreaterThanOrEqual(waiting);
    expect(file.getBoundingClientRect().height).toBe(39 + 31 + 40);
  });

  // dev6 E7 at 1440 again: at the column's end, a shrink above the reader's line is corrected (the
  // column goes up by it, so no spacer is held), and a shrink below the line would clamp the column
  // and drop the line; the spacer takes the room instead, and goes when the content grows back.
  it("keeps the line at the column's end when content above it and then below it shrinks, with no clamp", async () => {
    await openedPage([['[data-testid="review-details"]', 400]]);
    const details = screen.getByTestId('review-details');
    const card = screen.getAllByTestId('review-expression-card')[0];
    const held = () => column().querySelector<HTMLElement>('[data-reading-spacer]')?.style.height ?? '';
    scrollTo(column().scrollHeight - VIEWPORT);
    await gestureOver();
    // The lane section is the first block under the column's top: the line.
    const lane = topOf('review-lane-section');
    expect(lane).toBeGreaterThanOrEqual(COLUMN_TOP);
    expect(column().scrollTop).toBe(column().scrollHeight - VIEWPORT);

    await act(async () => {
      card.style.height = '300px';
    });
    await settle();
    expect(Math.abs(topOf('review-lane-section') - lane)).toBeLessThanOrEqual(1);
    expect(held()).toBe('');

    const at = column().scrollTop;
    await act(async () => {
      details.style.height = '385px';
    });
    await settle();
    expect(Math.abs(topOf('review-lane-section') - lane)).toBeLessThanOrEqual(1);
    expect(column().scrollTop).toBe(at);
    expect(held()).toBe('15px');

    await act(async () => {
      details.style.height = '1500px';
    });
    await settle();
    expect(Math.abs(topOf('review-lane-section') - lane)).toBeLessThanOrEqual(1);
    expect(held()).toBe('');
  });

  // dev6 (probe, point after the scroll): the line was a code line ("170") inside a card's excerpt.
  // About 3 s later the excerpt's pane was rebuilt (its line nodes replaced) while the cards above
  // shrank (by 400 and, above the line inside its own card, by 127); the line, taken again with no
  // correction, jumped 197 to 609 px. The search no longer enters an excerpt: with the column's top
  // inside one, the line is the next settled block below it. Here a card's excerpt pane holds 20 code
  // lines and the column's top falls on its 10th; the pane is rebuilt while the cards above shrink.
  it("keeps the line when a card excerpt across the column's top is rebuilt while the cards above shrink", async () => {
    const layout = mockLayout([
      ['[data-testid="review-expression-card"]', null],
      ['[data-testid="review-card-rationales"]', 400],
      ['[data-test-line]', 20],
    ]);
    stubIntersection();
    servePage();
    renderPage();
    await waitFor(() => expect(within(subjectSection()).getAllByTestId('review-expression-card').length).toBeGreaterThan(0));
    await waitFor(() => expect(screen.getByTestId('review-lane-section').dataset.laneState).toBe('ready'));
    layout.attach(column());
    const cards = screen.getAllByTestId('review-expression-card');
    const at = cards.findIndex((card, index) => index > 0 && card.querySelector('[data-testid="review-card-excerpt"]'));
    expect(at).toBeGreaterThan(0);
    const [above, card] = [cards[at - 1], cards[at]];
    const pane = card.querySelector<HTMLElement>('[data-testid="review-card-excerpt"] [data-testid$="-pane"]')!;
    const draw = () =>
      Array.from({ length: 20 }, (_, index) => {
        const line = document.createElement('div');
        line.dataset.testLine = String(161 + index);
        return line;
      });
    act(() => pane.replaceChildren(...draw()));
    const lines = () => [...pane.querySelectorAll<HTMLElement>('[data-test-line]')];
    scrollTo(column().scrollTop + lines()[9].getBoundingClientRect().top - COLUMN_TOP);
    expect(lines()[9].getBoundingClientRect().top).toBe(COLUMN_TOP);
    const next = within(card.parentElement!).queryAllByTestId('review-expression-card')[at + 1] ?? screen.getByTestId('review-lane-section');
    const nextTop = next.getBoundingClientRect().top;
    await gestureOver();

    act(() => {
      pane.replaceChildren(...draw());
      above.querySelector<HTMLElement>('[data-testid="review-card-rationales"]')!.style.height = '0px';
      card.querySelector<HTMLElement>('[data-testid="review-card-rationales"]')!.style.height = '273px';
    });
    await settle();
    expect(Math.abs(lines()[9].getBoundingClientRect().top - COLUMN_TOP)).toBeLessThanOrEqual(1);
    expect(Math.abs(next.getBoundingClientRect().top - nextTop)).toBeLessThanOrEqual(1);
  });

  // When the kept block itself is gone, the column is corrected before the line is taken again: by
  // the block drawn again at its place (the same tag and identity), else by its nearest connected
  // ancestor's own move. A bare column: a block above, a group of three rows, a tail.
  it('corrects by the block drawn again in its place, else by its nearest ancestor, before taking the line again', async () => {
    const layout = mockLayout([
      ['[data-testid="t-above"]', 400],
      ['[data-testid="t-row"]', 100],
      ['[data-testid="t-tail"]', 2000],
    ]);
    function Column() {
      const ref = useRef<HTMLDivElement>(null);
      useReaderAnchor(ref, () => false);
      return <div ref={ref} data-review-scroller />;
    }
    render(<Column />);
    layout.attach(column());
    const row = (path: string) => {
      const element = document.createElement('div');
      element.dataset.testid = 't-row';
      element.dataset.path = path;
      return element;
    };
    const block = (testid: string) => {
      const element = document.createElement('div');
      element.dataset.testid = testid;
      return element;
    };
    const blockAbove = block('t-above');
    const group = document.createElement('section');
    group.append(row('a'), row('b'), row('c'));
    act(() => column().append(blockAbove, group, block('t-tail')));
    // The second row 10 px under the column's top: the line.
    scrollTo(490);
    const second = () => group.children[1].getBoundingClientRect().top;
    expect(second()).toBe(COLUMN_TOP + 10);
    await gestureOver();

    act(() => {
      blockAbove.style.height = '200px';
      group.children[1].replaceWith(row('b'));
    });
    await settle();
    expect(Math.abs(second() - (COLUMN_TOP + 10))).toBeLessThanOrEqual(1);

    act(() => {
      blockAbove.style.height = '100px';
      group.children[1].replaceWith(row('other'));
    });
    await settle();
    expect(Math.abs(second() - (COLUMN_TOP + 10))).toBeLessThanOrEqual(1);
  });

  // When All goes to Changed, the Worklist replaces the change set or another comparison is shown, the
  // reader's block can go with most of the content below it. An actual-module probe: 100 sections of
  // 400 px, the reader at 30,000, the last 90 sections removed. The spacer held the old place, 26,540 px
  // of blank and no section in view, and the reader's scrolls only gave back what they passed. A line
  // that is gone holds no room: the column shows the content's end. A line that survives a shrink at
  // the column's end stays within 1 px, and the room its spacer holds goes as the reader scrolls back.
  describe('a shrink at the column end', () => {
    const sections = (count: number) =>
      Array.from({ length: count }, (_, index) => {
        const element = document.createElement('section');
        element.dataset.testid = 't-row';
        element.dataset.path = `s${index}`;
        return element;
      });
    const spacer = () => column().querySelector<HTMLElement>('[data-reading-spacer]')!;
    // The sections that show between the column's top and its bottom.
    const shown = () =>
      [...column().querySelectorAll('[data-testid="t-row"]')].filter((element) => {
        const box = element.getBoundingClientRect();
        return box.bottom > COLUMN_TOP && box.top < COLUMN_TOP + VIEWPORT;
      });
    async function sectionsColumn() {
      const layout = mockLayout([['[data-testid="t-row"]', 400]]);
      function Column() {
        const ref = useRef<HTMLDivElement>(null);
        useReaderAnchor(ref, () => false);
        return (
          <div ref={ref} data-review-scroller>
            <div data-reading-spacer />
          </div>
        );
      }
      render(<Column />);
      layout.attach(column());
      const all = sections(100);
      act(() => spacer().before(...all));
      return { layout, all };
    }

    it('shows the content where the line went with the shrink, never a blank spacer', async () => {
      const { all } = await sectionsColumn();
      scrollTo(30000);
      expect(all[75].getBoundingClientRect().top).toBe(COLUMN_TOP);
      await gestureOver();

      act(() => all.slice(10).forEach((element) => element.remove()));
      await settle();
      expect(spacer().style.height).toBe('');
      expect(column().scrollTop).toBe(10 * 400 - VIEWPORT);
      expect(shown().length).toBeGreaterThan(0);

      // The reader scrolls back up by 600 px: still content, no room held.
      scrollTo(column().scrollTop - 600);
      await settle();
      expect(spacer().style.height).toBe('');
      expect(shown().length).toBeGreaterThan(0);
    });

    it('keeps a surviving line within 1 px through a shrink that would clamp, and gives the room back as the reader scrolls', async () => {
      const { all } = await sectionsColumn();
      scrollTo(20000);
      expect(all[50].getBoundingClientRect().top).toBe(COLUMN_TOP);
      await gestureOver();

      act(() => all.slice(51).forEach((element) => element.remove()));
      await settle();
      expect(Math.abs(all[50].getBoundingClientRect().top - COLUMN_TOP)).toBeLessThanOrEqual(1);
      expect(column().scrollTop).toBe(20000);
      expect(spacer().style.height).toBe('200px');
      expect(shown()).toEqual([all[50]]);

      scrollTo(column().scrollTop - 300);
      await settle();
      expect(spacer().style.height).toBe('');
      expect(all[50].getBoundingClientRect().top).toBe(COLUMN_TOP + 300);
      expect(shown()).toEqual([all[49], all[50]]);
    });
  });

  // Ruling fb5d04f6 (dev7 probe …081755): a block that shows settled content at the column's top stays
  // the reader's line while its content is rebuilt. The `recordLabelOf` excerpt's diff pane went
  // 148.6 → 127 → 0 → 196.8 px while the reads landed: its marks' read landed, and the pane destroyed
  // its editor and built the next one once its language pack resolved, empty in between (WP8 on A4-F4:
  // still so at 2–3 s, its kept drawing an empty shell). A review page diff pane now draws its marks
  // into the same editor. Here a changed card's excerpt is drawn (its pane 200 px once its editor
  // exists) and starts at the column's top; the card comes into view and its marks' read lands: the
  // editor never leaves the pane, nothing stands in for it, and the excerpt never gets shorter.
  it("keeps a shown card excerpt's content and drawn height while a landing read draws its pane's marks", async () => {
    stubResizeObserver();
    const intersection = stubIntersection();
    const layout = mockLayout([
      ['[data-testid="review-expression-card"]', null],
      ['[data-testid="review-card-rationales"]', 100],
      ['[data-testid="diff-pane"]', (element) => (element.querySelector('.cm-editor') ? 200 : 0)],
    ]);
    servePage();
    serveCardMarks();
    renderPage();
    await waitFor(() => expect(within(subjectSection()).getAllByTestId('review-expression-card').length).toBeGreaterThan(0));
    await waitFor(() => expect(screen.getByTestId('review-lane-section').dataset.laneState).toBe('ready'));
    layout.attach(column());
    const card = screen
      .getAllByTestId('review-expression-card')
      .find((one) => one.dataset.cardChange === 'changed' && one.querySelector('[data-testid="diff-pane"]'))!;
    const excerpt = within(card).getByTestId('review-card-excerpt');
    const pane = excerpt.querySelector<HTMLElement>('[data-testid="diff-pane"]')!;
    await waitFor(() => expect(pane.querySelector('.cm-editor')).not.toBeNull());
    const first = pane.querySelector('.cm-editor');
    scrollTo(column().scrollTop + excerpt.getBoundingClientRect().top - COLUMN_TOP);
    await gestureOver();
    expect(excerpt.getBoundingClientRect()).toMatchObject({ top: COLUMN_TOP, height: 200 });
    const below = card.nextElementSibling ?? screen.getByTestId('review-lane-section');
    const belowTop = below.getBoundingClientRect().top;
    const reads: { held: boolean; top: number; height: number; below: number }[] = [];
    // Every layout read from the card's coming into view until its pane has drawn its mark.
    layout.watch(() => {
      layout.fresh();
      const box = layout.rectOf(excerpt);
      const held = pane.querySelector('[data-held-snapshot]') !== null;
      reads.push({ held, top: box.top, height: box.height, below: layout.rectOf(below).top });
      return pane.querySelector('[data-hunk]') === null;
    });

    // What each frame shows (the browser paints between tasks, after the observers' microtasks).
    const frames: boolean[] = [];
    let painting = true;
    const paint = () => {
      if (!painting) return;
      frames.push(pane.querySelector('.cm-editor') !== null);
      requestAnimationFrame(paint);
    };
    requestAnimationFrame(paint);

    act(() => intersection.show(card));
    await waitFor(() => expect(pane.querySelector('[data-hunk]')).not.toBeNull());
    await settle();
    painting = false;
    expect(reads.length).toBeGreaterThan(0);
    expect(Math.min(...reads.map((entry) => entry.height))).toBeGreaterThanOrEqual(200);
    expect(Math.max(...reads.map((entry) => Math.abs(entry.top - COLUMN_TOP)))).toBeLessThanOrEqual(1);
    expect(Math.max(...reads.map((entry) => Math.abs(entry.below - belowTop)))).toBeLessThanOrEqual(1);
    // The mark was drawn into the same editor: it never left the pane, and nothing stood in for it.
    expect(pane.querySelector('.cm-editor')).toBe(first);
    expect(reads.some((entry) => entry.held)).toBe(false);
    expect(frames.length).toBeGreaterThan(0);
    expect(frames.every(Boolean)).toBe(true);
    expect(pane.querySelectorAll('[data-held-snapshot]')).toHaveLength(0);
  });

  // Ruling fb5d04f6 and D382 (final2 …100732): the `recordLabelOf` excerpt went 148.6 → 127 px when its
  // marks' read landed: its "intent marks loading…" line left at once, a frame before the pane was
  // destroyed and held. The whole excerpt keeps its last height from the marks' landing until the
  // pane's next editor is drawn; then the new content stands as drawn. Here the pane is 200 px with
  // its editor, the loading line 22 px; the card's marks read is held, then answered.
  it('keeps the whole excerpt at its last height from its marks landing until the pane draws them', async () => {
    stubResizeObserver();
    const intersection = stubIntersection();
    const layout = mockLayout([
      ['[data-testid="review-expression-card"]', null],
      ['[data-testid="review-card-rationales"]', 100],
      ['[data-testid="diff-pane"]', (element) => (element.querySelector('.cm-editor') ? 200 : 0)],
      ['[data-testid="review-marks-state"]', 22],
    ]);
    servePage();
    serveCardMarks();
    const marked = globalThis.fetch;
    const held: (() => void)[] = [];
    vi.stubGlobal(
      'fetch',
      vi.fn(async (address: string | URL, init?: RequestInit) => {
        const response = await marked(address, init);
        if (!new URL(String(address), 'http://localhost').searchParams.has('file')) return response;
        return new Promise<Response>((resolve) => held.push(() => resolve(response)));
      }),
    );
    renderPage();
    await waitFor(() => expect(within(subjectSection()).getAllByTestId('review-expression-card').length).toBeGreaterThan(0));
    await waitFor(() => expect(screen.getByTestId('review-lane-section').dataset.laneState).toBe('ready'));
    layout.attach(column());
    const card = screen
      .getAllByTestId('review-expression-card')
      .find((one) => one.dataset.cardChange === 'changed' && one.querySelector('[data-testid="diff-pane"]'))!;
    const excerpt = within(card).getByTestId('review-card-excerpt');
    const pane = excerpt.querySelector<HTMLElement>('[data-testid="diff-pane"]')!;
    await waitFor(() => expect(pane.querySelector('.cm-editor')).not.toBeNull());
    const first = pane.querySelector('.cm-editor');
    scrollTo(column().scrollTop + excerpt.getBoundingClientRect().top - COLUMN_TOP);
    await gestureOver();
    act(() => intersection.show(card));
    await waitFor(() => expect(within(excerpt).getByTestId('review-marks-state').dataset.marksState).toBe('loading'));
    await settle();
    const last = excerpt.getBoundingClientRect().height;
    expect(last).toBe(200 + 22);
    const reads: number[] = [];
    // Every layout read while the excerpt holds its height: from the marks' landing until its pane
    // has drawn them (a review page diff pane draws them in place).
    const holder = excerpt.firstElementChild as HTMLElement;
    let holdSeen = false;
    layout.watch(() => {
      layout.fresh();
      const holding = holder.style.minHeight !== '';
      holdSeen ||= holding;
      if (holdSeen && !holding) return false;
      if (holding) reads.push(layout.rectOf(excerpt).height);
      return true;
    });

    await act(async () => held.splice(0).forEach((go) => go()));
    await waitFor(() => expect(within(excerpt).queryByTestId('review-marks-state')).toBeNull());
    await waitFor(() => expect(pane.querySelector('[data-hunk]')).not.toBeNull());
    await settle();
    expect(holdSeen).toBe(true);
    expect(holder.style.minHeight).toBe('');
    expect(reads.length).toBeGreaterThan(0);
    expect(Math.min(...reads)).toBeGreaterThanOrEqual(last);
    // Its marks drawn, the excerpt is its new content: the pane alone, in the same editor.
    expect(pane.querySelector('.cm-editor')).toBe(first);
    expect(excerpt.getBoundingClientRect().height).toBe(200);
  });

  // Ruling fb5d04f6, the probe's point-after-scroll case on A2-F2 (final2 …100732): when the line was
  // taken, the `recordLabelOf` excerpt held the column's top under the sticky head, so the line was the
  // block below it, its card's currentness line. The excerpt then started 1 px under the head (145.1
  // against 144.06), the first settled block at the top, while the line stayed on the block below; as
  // its marks landed it went 148.6 → 127 → 196.8 → 166.6 px and its top moved +25, −70 and +30 px. Once
  // a box re-rendered as a whole starts under the head, it is the line. Here a head of 44 px, a card
  // whose excerpt pane holds the column's top 2 px under the head, its currentness line below it, and
  // the column's end 4 px below the view: the pane shrinks by 3 px (the excerpt then starts 1 px under
  // the head), then goes 149 → 127 → 197 → 167 px; the shrinks would clamp the column.
  it("keeps a card excerpt's top once it starts under the head, through its rebuild's heights and a clamp", async () => {
    const layout = mockLayout([
      ['[data-reading-head]', 44],
      ['[data-testid="review-expression-card"]', null],
      ['[data-testid="t-above"]', 1000],
      ['[data-testid="t-heading"]', 100],
      ['[data-testid="t-pane"]', 152],
      ['[data-testid="t-currentness"]', 30],
      ['[data-testid="t-tail"]', 380],
    ]);
    function Column() {
      const ref = useRef<HTMLDivElement>(null);
      useReaderAnchor(ref, () => false);
      return (
        <div ref={ref} data-review-scroller>
          <div data-reading-head />
          <div data-reading-spacer />
        </div>
      );
    }
    render(<Column />);
    layout.attach(column());
    const block = (testid: string, tag = 'div') => {
      const element = document.createElement(tag);
      element.dataset.testid = testid;
      return element;
    };
    const card = block('review-expression-card', 'article');
    const excerpt = block('review-card-excerpt');
    const pane = block('t-pane');
    const currentness = block('t-currentness', 'p');
    excerpt.append(pane);
    card.append(block('t-heading'), excerpt, currentness);
    act(() => column().querySelector('[data-reading-spacer]')!.before(block('t-above'), card, block('t-tail')));
    const head = COLUMN_TOP + 44;
    // The excerpt from 2 px above the head's bottom; the currentness line, below it, is the line.
    scrollTo(44 + 1000 + 100 - (head - 2 - COLUMN_TOP));
    expect(excerpt.getBoundingClientRect().top).toBe(head - 2);
    await gestureOver();
    const height = async (px: number) => {
      await act(async () => {
        pane.style.height = `${px}px`;
      });
      await settle();
    };

    await height(149);
    const top = excerpt.getBoundingClientRect().top;
    expect(top).toBe(head + 1);
    const seen: number[] = [];
    for (const px of [127, 197, 167]) {
      await height(px);
      seen.push(excerpt.getBoundingClientRect().top);
    }
    expect(layout.clamps()).toBeGreaterThan(0);
    expect(Math.max(...seen.map((at) => Math.abs(at - top)))).toBeLessThanOrEqual(1);
  });

  // Ruling 8450399b part 1 (the trace on A3-F1: +173.6 px at 1920, +148.8 px at 1440): the page's first
  // scroll is the reader's first move, so it opens the read gate (readGate.ts, the window's capture
  // listener), and the cards seen at the opening, now far above the view, start their marks reads. Each
  // excerpt's marks state holds one row from its first render on, so the "intent marks loading…" line,
  // and what the read resolves to, change no card's height, and the reader's line holds within 1 px. A
  // browser runs the microtasks between one listener of an event it dispatches and the next (React's
  // commit, the observers' callbacks); a script's dispatch does not. So the case dispatches the window's
  // part of the first scroll (its capture listener, the gate's), lets the microtasks run, then the
  // column's part. Here a card's rationale lines are 100 px, its pane 200, its marks state 22.
  it("keeps the line through the first scroll's marks reads above it: no card grows while they wait or land", async () => {
    stubResizeObserver();
    stubInViewAtFirstPaint();
    const layout = mockLayout([
      ['[data-testid="review-expression-card"]', null],
      ['[data-testid="review-card-rationales"]', 100],
      ['[data-testid="diff-pane"]', 200],
      ['[data-testid="review-marks-row"]', 22],
      ['[data-testid="review-marks-state"]', 22],
    ]);
    servePage();
    serveCardMarks();
    // The cards' marks reads wait until the case answers them.
    const cardPaths = new Set((l181Answer.entries ?? []).map((entry) => entry.after.path ?? entry.before.path));
    const marked = globalThis.fetch;
    const held: (() => void)[] = [];
    vi.stubGlobal(
      'fetch',
      vi.fn(async (address: string | URL, init?: RequestInit) => {
        const path = new URL(String(address), 'http://localhost').searchParams.get('file');
        const response = await marked(address, init);
        if (!path || !cardPaths.has(path)) return response;
        return new Promise<Response>((resolve) => held.push(() => resolve(response)));
      }),
    );
    renderPage();
    await waitFor(() => expect(within(subjectSection()).getAllByTestId('review-expression-card').length).toBeGreaterThan(0));
    await waitFor(() => expect(screen.getByTestId('review-lane-section').dataset.laneState).toBe('ready'));
    layout.attach(column());
    await settle();
    const cards = screen.getAllByTestId('review-expression-card');
    const heights = () => cards.map((card) => card.getBoundingClientRect().height);
    // Seen at the opening, no card has asked for its marks: they wait for the reader's first move.
    expect(cards.every((card) => card.dataset.inView === 'false')).toBe(true);
    expect(held).toHaveLength(0);
    const opened = heights();

    // The reader's first scroll puts the lane's heading at the column's top, every card above it.
    const heading = screen.getByTestId('review-lane-section').querySelector('h3')!;
    column().scrollTop = column().scrollTop + heading.getBoundingClientRect().top - COLUMN_TOP;
    const target = heading.getBoundingClientRect().top;
    expect(target).toBe(COLUMN_TOP);
    await act(async () => {
      document.dispatchEvent(new Event('scroll'));
    });
    fireEvent.scroll(column());
    await settle();
    // The cards above the line ask for their marks, and the reads wait.
    for (let turn = 0; turn < 50 && held.length === 0; turn += 1) await settle();
    expect(held.length).toBeGreaterThan(0);
    expect(screen.getAllByTestId('review-marks-state').some((note) => note.dataset.marksState === 'loading')).toBe(true);
    const pending = { heights: heights(), top: heading.getBoundingClientRect().top };
    // The reads land, a batch at a time (at most READ_BATCH files read at once): each turn answers the
    // reads asked so far, until no card's marks are loading. No turn waits on the clock.
    const loading = () => screen.queryAllByTestId('review-marks-state').some((note) => note.dataset.marksState === 'loading');
    for (let turn = 0; turn < 50 && loading(); turn += 1) {
      await act(async () => held.splice(0).forEach((go) => go()));
      await settle();
    }
    expect(loading()).toBe(false);
    expect(screen.queryAllByTestId('review-marks-state')).toHaveLength(0);
    await settle();
    const landed = { heights: heights(), top: heading.getBoundingClientRect().top };
    // Rule 9's tolerance (ruling 8450399b): the line moves at most 1 px, while the reads wait and once they land.
    const moved = { pending: pending.top - target, landed: landed.top - target };
    expect(Math.max(Math.abs(moved.pending), Math.abs(moved.landed)), `the line moved ${JSON.stringify(moved)} px`).toBeLessThanOrEqual(1);
    expect(pending.heights).toEqual(opened);
    expect(landed.heights).toEqual(opened);
    // The row's own height is fixed and its text stays on one line, so no state of the read moves it.
    for (const row of screen.getAllByTestId('review-marks-row'))
      expect([...row.classList]).toEqual(expect.arrayContaining(['h_1.5rem', 'ov_hidden']));
  });

  // D383: a block drawn for the first time, with no size from the opening's reads, shows skeleton
  // lines at a default height. The selected subject's answer before its first read: the slot holds
  // its reading status and skeleton lines 12rem tall; the answer replaces both.
  it("shows skeleton lines at a default height in the selected subject's slot until its first read lands", async () => {
    stubIntersection();
    const layout = mockLayout();
    servePage();
    const reads = holdReads();
    renderPage();
    await waitFor(() => expect(screen.getByTestId('review-lane-section').dataset.laneState).toBe('ready'));
    layout.attach(column());
    fireEvent.click(within(subjectSection()).getByTestId('review-subject-select'));
    await waitFor(() => expect(slotState()).toBe('pending'));
    const skeleton = within(screen.getByTestId('review-subject-slot')).getByTestId('review-subject-slot-skeleton');
    expect(skeleton.classList.contains(heldSkeleton)).toBe(true);
    expect(skeleton.classList.contains(css({ height: '12rem' }))).toBe(true);
    expect(skeleton.getAttribute('aria-hidden')).toBe('true');

    await act(async () => reads.release('subject'));
    await waitFor(() => expect(slotState()).toBe('answered'));
    expect(screen.queryByTestId('review-subject-slot-skeleton')).toBeNull();
    await act(async () => reads.release('cards'));
  });
});

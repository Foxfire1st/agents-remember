// OR-R042: real mounted review reads over the preserved captured world. The refused answer is
// captured from the actual route with its receipt; viewport/scroll APIs use jsdom's test shims.
// A selection's failed or refused read is shown in its subject's slot, and the page scrolls once to
// the subject's heading after it (MIK-R89 v2 rule 9): the reading column scrolls itself (scrollTop),
// and only the stacked layout (40rem and narrower), where the page around it scrolls, brings the
// heading into view. A reader who scrolled while it was pending is not moved, and focus the reader
// moved elsewhere stays there. The selection is made from the tree or from the column (`j`/`k`
// select blocks since rule 11, and these answers carry none).
import { act, fireEvent, within } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import type { ReviewResult } from '../../data/review';
import {
  R6R_ID,
  HBJ_ID,
  SHARED,
  bodyOf,
  captured,
  clickedSecondFamily,
  families,
  familyBlock,
  installWorld,
  open,
  reviewCount,
  selectedNode,
  subjectOf,
  subjectOfLabel,
  world,
} from './walk.test-utils';

installWorld();
const TARGET = bodyOf('INV-2E8MG43K');
const REFUSED = captured<ReviewResult>('walkUnavailable.refused.captured.json');
const targetRow = (view: ReturnType<typeof open>) =>
  within(familyBlock(view, R6R_ID))
    .getAllByTestId('review-family-member-open')
    .find((row) => row.dataset.subjectId === subjectOfLabel('INV-2E8MG43K'))!;
const targetSection = (view: ReturnType<typeof open>) =>
  view.getAllByTestId('review-subject-section').find((node) => node.dataset.subject === subjectOfLabel('INV-2E8MG43K'))!;

async function viewport(stacked: boolean, run: (scrolled: Element[]) => Promise<void>) {
  const media = window.matchMedia;
  const scroll = Element.prototype.scrollIntoView;
  const scrolled: Element[] = [];
  window.matchMedia = (query) => ({
    ...media(query),
    matches: stacked && query === '(max-width: 40rem)',
  });
  Element.prototype.scrollIntoView = function () {
    scrolled.push(this);
  };
  try {
    await run(scrolled);
  } finally {
    window.matchMedia = media;
    Element.prototype.scrollIntoView = scroll;
  }
}

const cases = [true, false].flatMap((stacked) =>
  ['failed', 'refused'].flatMap((outcome) =>
    ['tree', 'column'].map((activation) => ({ stacked, outcome, activation })),
  ),
);

it.each(cases)(
  'brings the subject heading into view after a $outcome read from the $activation only when stacked=$stacked',
  async ({ stacked, outcome, activation }) => {
    const view = open(SHARED);
    await clickedSecondFamily(view);
    const previous = world.answer;
    world.answer = (url, asked) =>
      asked === subjectOf(TARGET)
        ? outcome === 'failed'
          ? new TypeError('the selected read failed')
          : REFUSED
        : previous(url, asked);
    const workspace = view.getByTestId('review-workspace');
    const row = targetRow(view);
    const reads = reviewCount();
    // A pointer moves focus to the control it activates.
    const control = activation === 'column' ? within(targetSection(view)).getByTestId('review-subject-select') : row;
    await viewport(stacked, async (scrolled) => {
      control.focus();
      fireEvent.click(control);
      const status = await view.findByTestId('review-reading-problem', undefined);
      await act(async () => {});
      expect(status.textContent).toContain('INV-2E8MG43K');
      expect(targetSection(view).contains(status)).toBe(true);
      expect(scrolled).toEqual(stacked ? [targetSection(view)] : []);
      // The tree marks the requested row, and the failed or refused answer lands focus on it, from
      // the column's control as from the row itself.
      expect(selectedNode(view)).toBe(row);
      expect(document.activeElement).toBe(row);
      expect(families(view)).toEqual([R6R_ID, HBJ_ID]);
      expect(view.getByTestId('review-workspace')).toBe(workspace);
      expect(reviewCount() - reads).toBe(1);
    });
  },
);

it.each(['failed', 'refused'])(
  'does not move a reader who scrolled and moved focus while the $outcome read was pending',
  async (outcome) => {
    const view = open(SHARED);
    await clickedSecondFamily(view);
    const row = targetRow(view);
    const previous = world.answer;
    world.answer = (url, asked) =>
      asked === subjectOf(TARGET)
        ? outcome === 'failed'
          ? new TypeError('the selected read failed')
          : REFUSED
        : previous(url, asked);
    let release: (() => void) | undefined;
    const held = new Promise<void>((resolve) => {
      release = resolve;
    });
    const served = globalThis.fetch;
    vi.stubGlobal('fetch', async (address: string, init?: RequestInit) => {
      if (
        String(address).includes(
          `selectorId=${TARGET.payload!.knowledge.revision_selection!.record_id}`,
        )
      )
        await held;
      return served(address, init);
    });
    try {
      await viewport(true, async (scrolled) => {
        row.focus();
        fireEvent.click(row);
        await view.findByTestId('review-reading-pending', undefined);
        fireEvent.wheel(view.getByTestId('review-center-column'), { deltaY: 120 });
        const elsewhere = view.getByTestId('review-family-filter');
        elsewhere.focus();
        release?.();
        await view.findByTestId('review-reading-problem', undefined);
        await act(async () => {});
        expect(document.activeElement).toBe(elsewhere);
        expect(scrolled).toEqual([]);
        expect(selectedNode(view)).toBe(row);
        expect(families(view)).toEqual([R6R_ID, HBJ_ID]);
      });
    } finally {
      release?.();
    }
  },
);

// OR-R042: real mounted review reads over the preserved captured world. The refused answer is
// captured from the actual route with its receipt; viewport/scroll APIs use jsdom's test shims.
import { act, fireEvent, waitFor, within } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import type { ReviewResult } from '../../data/review';
import {
  J,
  R6R_ID,
  HBJ_ID,
  SHARED,
  WAIT,
  bodyOf,
  captured,
  clickedSecondFamily,
  families,
  familyBlock,
  installWorld,
  open,
  reviewCount,
  revisionOf,
  selectedNode,
  step,
  subjectOf,
  world,
} from './walk.test-utils';

installWorld();
const TARGET = bodyOf('INV-2E8MG43K');
const REFUSED = captured<ReviewResult>('walkUnavailable.refused.captured.json');
const targetRow = (view: ReturnType<typeof open>) =>
  within(familyBlock(view, R6R_ID))
    .getAllByTestId('review-family-member-open')
    .find((row) => row.dataset.revision === revisionOf('INV-2E8MG43K'))!;

async function viewport(stacked: boolean, run: (scrolled: Element[]) => Promise<void>) {
  const media = window.matchMedia;
  const scroll = Element.prototype.scrollIntoView;
  const scrolled: Element[] = [];
  window.matchMedia = (query) => ({
    ...media(query),
    matches: stacked && query === '(max-width: 60rem)',
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
    ['click', 'key'].map((activation) => ({ stacked, outcome, activation })),
  ),
);

it.each(cases)(
  'reveals $outcome after $activation only when stacked=$stacked',
  async ({ stacked, outcome, activation }) => {
    const view = open(SHARED);
    await clickedSecondFamily(view);
    if (activation === 'key') {
      fireEvent.click(within(familyBlock(view, R6R_ID)).getByTestId('review-family-open'));
      await waitFor(
        () => expect(view.getByTestId('review-center-family').dataset.family).toBe(R6R_ID),
        WAIT,
      );
      await waitFor(() => expect(document.activeElement).toBe(selectedNode(view)), WAIT);
    }
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
    await viewport(stacked, async (scrolled) => {
      if (activation === 'key') await step(view, J, 1);
      else {
        row.focus();
        fireEvent.click(row);
      }
      const status = await view.findByTestId('review-reading-problem', undefined, WAIT);
      await act(async () => {});
      expect(status.textContent).toContain('INV-2E8MG43K');
      expect(scrolled).toEqual(stacked ? [view.getByTestId('review-center-column')] : []);
      expect(selectedNode(view)).toBe(row);
      expect(document.activeElement).toBe(row);
      expect(families(view)).toEqual([R6R_ID, HBJ_ID]);
      expect(view.getByTestId('review-workspace')).toBe(workspace);
      expect(reviewCount() - reads).toBe(1);
    });
  },
);

it.each(['failed', 'refused'])(
  'does not reveal $outcome after the reader moved focus while pending',
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
        await view.findByTestId('review-reading-pending', undefined, WAIT);
        const elsewhere = view.getByTestId('review-family-filter');
        elsewhere.focus();
        release?.();
        await view.findByTestId('review-reading-problem', undefined, WAIT);
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

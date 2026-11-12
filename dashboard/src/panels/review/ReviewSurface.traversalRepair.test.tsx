// A selection made outside the tree (here from the reading column; `j`/`k` select by block since
// MIK-R89 v2 rule 11 and use the same path) is revealed in the tree by its scroller's `scrollTop`
// alone, before its delayed uncached answer, and nothing else scrolls for it: no ancestor
// `scrollIntoView` of the tree row, no window scroll, and the page root does not move. At either end
// of the blocks a key keeps the selection, the focus and the tree where they are. jsdom geometry is
// explicit; native captured answers (walkReal.capture-provenance.json).
import { act, fireEvent, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { resetCardCachesForTests } from '../../data/reviewTrees';
import {
  J, K, R6R, bodyOf, installWorld, open, overviewMember, selectedNode, settled, subjectOf,
} from './walk.test-utils';

installWorld();
afterEach(() => { resetCardCachesForTests(); vi.restoreAllMocks(); });

const rect = (top: number, bottom: number) => ({ top, bottom, left: 0, right: 0, width: 0, height: bottom - top, x: 0, y: top, toJSON: () => ({}) }) as DOMRect;

function geometry(view: ReturnType<typeof open>, row: HTMLElement) {
  const surface = view.getByTestId('review-surface');
  const panel = row.closest<HTMLElement>('[data-review-tree-panel]')!;
  const pinned = panel.querySelector<HTMLElement>('[data-review-tree-pinned]')!;
  surface.scrollTop = 137;
  panel.scrollTop = 0;
  vi.spyOn(surface, 'getBoundingClientRect').mockReturnValue(rect(0, 600));
  vi.spyOn(panel, 'getBoundingClientRect').mockReturnValue(rect(100, 300));
  vi.spyOn(pinned, 'getBoundingClientRect').mockReturnValue(rect(300, 360));
  vi.spyOn(row, 'getBoundingClientRect').mockImplementation(() => rect(500 - panel.scrollTop, 520 - panel.scrollTop));
  return { surface, panel };
}

it.each([390, 1280])(
  'reveals a selection made in the column in the tree before its delayed uncached answer, by scrollTop only, at %s px',
  async (width) => {
    const media = window.matchMedia;
    vi.stubGlobal('innerWidth', width);
    vi.stubGlobal('matchMedia', (query: string) => ({ ...media(query), matches: width <= 640 }));
    const view = open(R6R);
    // The family opens its members' overview in the column (MIK-R211 v4 rule 10).
    await view.findByTestId('review-family-overview');
    // The family's own read has answered.
    await view.findByTestId('review-subject-provenance');
    const target = bodyOf('INV-2E8MG43K');
    const id = target.payload!.knowledge.revision_selection!.record_id;
    const row = view.getAllByTestId('review-family-member-open').find((node) => node.dataset.subjectId === id)!;
    expect(row).toBeTruthy();
    const { surface, panel } = geometry(view, row);
    const beforeWindow = window.scrollY;
    let release!: () => void;
    const held = new Promise<void>((resolve) => { release = resolve; });
    const served = globalThis.fetch;
    const requests: URL[] = [];
    vi.stubGlobal('fetch', async (address: string, init?: RequestInit) => {
      const url = new URL(address, 'http://localhost');
      if (url.pathname === '/api/review/intent' && url.searchParams.get('selectorId') === id) {
        requests.push(url);
        await held;
      }
      return served(address, init);
    });
    const focus = vi.spyOn(row, 'focus');
    const ancestorScroll = vi.fn();
    row.scrollIntoView = ancestorScroll;
    try {
      fireEvent.click(overviewMember(view, id));
      await view.findByTestId('review-reading-pending');
      await waitFor(() => expect(requests).toHaveLength(1));
      expect(view.getByTestId('review-surface').dataset.reviewPending).toBe(subjectOf(target));
      // Revealed in the tree before the answer: above the pinned rows, by the scroller's own scrollTop.
      expect(panel.scrollTop).toBeGreaterThan(0);
      expect(row.getBoundingClientRect().bottom).toBeLessThanOrEqual(300);
      expect(surface.scrollTop).toBe(137);
      expect(window.scrollY).toBe(beforeWindow);
      release();
      await view.findByTestId('review-center-member');
      await settled(view);
      // The answer moves focus to the selected row, without native scrolling.
      expect(document.activeElement).toBe(row);
      expect(focus.mock.calls.every(([options]) => options?.preventScroll === true)).toBe(true);
      expect(surface.scrollTop).toBe(137);
      expect(window.scrollY).toBe(beforeWindow);
      expect(ancestorScroll).not.toHaveBeenCalled();
    } finally {
      release();
      await act(async () => {});
    }
  },
);

it.each([{ key: J, edge: 'later' }, { key: K, edge: 'earlier' }])(
  'keeps the selection, its focus and the tree in place at the $edge end of the blocks', async ({ key, edge }) => {
    const media = window.matchMedia;
    vi.stubGlobal('matchMedia', (query: string) => ({ ...media(query), matches: true }));
    const view = open(R6R);
    await view.findByTestId('review-family-overview');
    const stops = [...view.getByTestId('review-family-list').querySelectorAll<HTMLElement>('[data-tree-node="member"]')]
      .filter((node) => node.dataset.changePrimary !== undefined && !['unknown', 'unchanged'].includes(node.dataset.changePrimary));
    const row = edge === 'later' ? stops.at(-1)! : stops[0];
    row.focus({ preventScroll: true });
    fireEvent.click(row);
    await settled(view);
    expect(selectedNode(view)).toBe(row);
    const { surface, panel } = geometry(view, row);
    const ancestorScroll = vi.fn();
    row.scrollIntoView = ancestorScroll;
    await waitFor(() => {
      if (!view.queryByTestId('review-change-status')) fireEvent.keyDown(row, key);
      expect(view.getByTestId('review-change-status').textContent).toBe(`No ${edge} block; the selection stays.`);
    });
    expect(ancestorScroll).not.toHaveBeenCalled();
    expect(panel.scrollTop).toBe(0);
    expect(surface.scrollTop).toBe(137);
    expect(selectedNode(view)).toBe(row);
    expect(document.activeElement).toBe(row);
  },
);

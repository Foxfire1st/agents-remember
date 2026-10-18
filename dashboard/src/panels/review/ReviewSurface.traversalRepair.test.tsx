// Actual Surface j/k path with native captured answers. jsdom geometry is explicit; browser
// ancestor-scroll verification remains separate. Focus spies delegate to the real DOM focus.
import { act, fireEvent, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { resetCardCachesForTests } from '../../data/reviewTrees';
import {
  J, K, R6R, bodyOf, installWorld, open, selectedNode, settled, subjectOf,
} from './walk.test-utils';

installWorld();
afterEach(() => { resetCardCachesForTests(); vi.restoreAllMocks(); });

function geometry(view: ReturnType<typeof open>, row: HTMLElement) {
  const surface = view.getByTestId('review-surface');
  const panel = row.closest<HTMLElement>('[data-review-tree-panel]')!;
  surface.scrollTop = 137;
  panel.scrollTop = 0;
  vi.spyOn(surface, 'getBoundingClientRect').mockReturnValue({ top: 0, bottom: 600 } as DOMRect);
  const panelTop = () => surface.dataset.reviewPending ? 500 : 100;
  vi.spyOn(panel, 'getBoundingClientRect').mockImplementation(() => ({
    top: panelTop(),
    bottom: panelTop() + Math.min(200, Number.parseFloat(panel.style.maxHeight) || 200),
  } as DOMRect));
  vi.spyOn(row, 'getBoundingClientRect').mockImplementation(() => ({
    top: panelTop() + 400 - panel.scrollTop, bottom: panelTop() + 420 - panel.scrollTop,
  } as DOMRect));
  return { surface, panel };
}

it.each([390, 1280].flatMap((width) => [J, K].map((key) => ({ width, key }))))(
  'reveals the actual $key.key target locally before its delayed uncached answer at $width px', async ({ width, key }) => {
  const media = window.matchMedia;
  vi.stubGlobal('innerWidth', width);
  vi.stubGlobal('matchMedia', (query: string) => ({ ...media(query), matches: width <= 640 }));
  const view = open(R6R);
  await view.findByTestId('review-center-family');
  if (key === K) {
    const startId = bodyOf('INV-ZS9ZS878').payload!.knowledge.revision_selection!.record_id;
    const start = view.getAllByTestId('review-family-member-open').find((node) => node.dataset.subjectId === startId)!;
    start.focus({ preventScroll: true });
    fireEvent.click(start);
    await settled(view);
  }
  const first = selectedNode(view)!;
  first.focus({ preventScroll: true });
  const target = bodyOf('INV-2E8MG43K');
  const id = target.payload!.knowledge.revision_selection!.record_id;
  const row = view.getAllByTestId('review-family-member-open').find((node) => node.dataset.subjectId === id)!;
  expect(row).toBeTruthy();
  const { surface, panel } = geometry(view, row);
  // Existing desktop selection deliberately restores the reading position (workspaceState).
  // Nonzero surface preservation belongs to <=40rem, not that separate desktop policy.
  const expectedSurfaceTop = width <= 640 ? 137 : 0;
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
    // Like walk.test-utils.step, await keymap readiness without repeating an accepted press.
    await waitFor(() => {
      if (focus.mock.calls.length === 0) fireEvent.keyDown(first, key);
      // The answer is held: a later successful-answer focus cannot hide early plain focus.
      expect(focus).toHaveBeenCalledWith({ preventScroll: true });
    });
    expect(panel.scrollTop).toBeGreaterThan(0);
    expect(row.getBoundingClientRect().bottom).toBeLessThanOrEqual(panel.getBoundingClientRect().bottom);
    expect(surface.scrollTop).toBe(expectedSurfaceTop);
    expect(window.scrollY).toBe(beforeWindow);
    await view.findByTestId('review-reading-pending');
    await waitFor(() => expect(requests).toHaveLength(1));
    expect(view.getByTestId('review-surface').dataset.reviewPending).toBe(subjectOf(target));
    expect(row.getBoundingClientRect().bottom).toBeLessThanOrEqual(surface.getBoundingClientRect().bottom);
    expect(document.activeElement).toBe(row);
    release();
    await view.findByTestId('review-center-member');
    await settled(view);
    expect(focus.mock.calls.every(([options]) => options?.preventScroll === true)).toBe(true);
    expect(surface.scrollTop).toBe(expectedSurfaceTop);
    expect(window.scrollY).toBe(beforeWindow);
    expect(ancestorScroll).not.toHaveBeenCalled();
  } finally {
    release();
    await act(async () => {});
  }
});

it.each([{ key: J, edge: 'later' }, { key: K, edge: 'earlier' }])(
  'reveals only the tree panel beyond the $edge endpoint', async ({ key, edge }) => {
    const media = window.matchMedia;
    vi.stubGlobal('matchMedia', (query: string) => ({ ...media(query), matches: true }));
    const view = open(R6R);
    await view.findByTestId('review-center-family');
    const stops = [...view.getByTestId('review-family-list').querySelectorAll<HTMLElement>('[data-tree-node]')]
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
      expect(view.getByTestId('review-change-status').textContent).toContain(`No ${edge} change`);
    });
    expect(ancestorScroll).not.toHaveBeenCalled();
    expect(panel.scrollTop).toBeGreaterThan(0);
    expect(surface.scrollTop).toBe(137);
    expect(selectedNode(view)).toBe(row);
    expect(document.activeElement).toBe(row);
  },
);

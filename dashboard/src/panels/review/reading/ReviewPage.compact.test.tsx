import { cleanup, fireEvent, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { resetCardCachesForTests, type ReviewTreesResult } from '../../../data/reviewTrees';
import overview from '../compact-l181.fixture.json';
import { renderPage, selectInvariant, servePage, stubIntersection, waitForOpening } from './reviewPage.test-utils';

beforeEach(() => {
  window.localStorage.clear();
  window.history.replaceState(null, '', '/');
  resetCardCachesForTests();
  stubIntersection();
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

function serveCompact() {
  const { asked } = servePage();
  const original = globalThis.fetch;
  vi.stubGlobal('fetch', vi.fn(async (address: string | URL, init?: RequestInit) => {
    const response = await original(address, init);
    const url = new URL(String(address), 'http://localhost');
    if (url.pathname !== '/api/review/trees' || url.searchParams.get('view') !== 'overview') return response;
    const body = await response.json() as ReviewTreesResult;
    delete body.entry_inventory;
    delete body.worklist;
    return new Response(JSON.stringify({ ...body, overview, worklist_summary: { open_count: 0, planning: {}, file_open_counts: {} } }), { status: 200 });
  }));
  return asked;
}

it('opens compact complete counts without rows, then reads only the selected invariant', async () => {
  const asked = serveCompact();
  renderPage();
  await waitForOpening();
  expect(screen.getByTestId('review-summary').textContent).toContain('15 changed locations');
  expect(screen.queryAllByTestId('review-tree-location')).toHaveLength(0);
  expect(screen.queryAllByTestId('review-expression-card')).toHaveLength(0);
  expect(asked.filter((url) => url.pathname === '/api/review/trees' && !url.searchParams.has('lane')).map((url) => url.searchParams.get('view'))).toEqual(['overview']);
  await selectInvariant();
  await waitFor(() => expect(screen.getAllByTestId('review-tree-location')).toHaveLength(13));
  expect(asked.filter((url) => url.searchParams.has('invariants'))).toHaveLength(1);
});

it('loads complete search metadata on demand to find an unvisited location', async () => {
  const asked = serveCompact();
  renderPage();
  await waitForOpening();
  fireEvent.change(screen.getByTestId('review-family-filter'), { target: { value: 'turn_ended.ts' } });
  await waitFor(() => expect(screen.getAllByTestId('review-tree-location').length).toBeGreaterThan(0));
  const full = asked.filter((url) => url.pathname === '/api/review/trees' && !['lane', 'view', 'invariants', 'entry'].some((key) => url.searchParams.has(key)));
  expect(full).toHaveLength(1);
  expect(screen.queryAllByTestId('review-expression-card')).toHaveLength(0);
});

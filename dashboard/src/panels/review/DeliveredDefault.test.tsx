// The landed leaf's reviewed page opens what the leaf delivered, and a read that fails to say so
// says that instead (MIK-R195 F06, F07). The task entry's frame, the delivered link and the failed
// notice read, on the real page over the repository's captured page fixture.
//
// #7 is the exact delivered record and #8 the later history: the lower-delivered case the packet
// names, so a page that opens the highest number opens the wrong pair.
import { cleanup, fireEvent, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';

import { PLACE, renderPage, servePage, stubIntersection } from './reading/reviewPage.test-utils';

beforeEach(() => {
  window.localStorage.clear();
  window.history.replaceState(null, '', '/#review?' + new URLSearchParams({ repo: PLACE.repo, master: PLACE.master, leaf: PLACE.leaf }));
  stubIntersection();
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

// The page's own answers, with the numbers this case needs: the task context names #7 by default and
// #8 when the read asks for the recorded comparison, and the leaf-wide trees read names both.
function fixture(failNotice = false) {
  const router = servePage();
  const base = globalThis.fetch;
  vi.stubGlobal(
    'fetch',
    vi.fn(async (address: string | URL, init?: RequestInit) => {
      const url = new URL(String(address), 'http://localhost');
      const response = await base(address, init);
      const body = await response.json();
      if (url.pathname === '/api/review/intent' && body.payload) {
        const number = url.searchParams.get('history') === 'recorded' ? 8 : 7;
        body.payload.limitations = body.payload.limitations.map((token: string) =>
          token.startsWith('review:trees:') ? `review:trees:${number}` : token,
        );
      }
      const leafTrees =
        url.pathname === '/api/review/trees' &&
        !url.searchParams.has('invariants') &&
        !url.searchParams.has('lane') &&
        !url.searchParams.has('file');
      if (leafTrees) {
        if (failNotice)
          return new Response(
            JSON.stringify({
              ...body,
              state: 'refused',
              comparison: null,
              refusal: {
                code: 'candidate_unresolved',
                detail: 'Notice-bearing read unavailable',
                next_action: 'retry exact record',
                offending_input: 'memory candidate',
              },
            }),
            { status: 200, headers: { 'content-type': 'application/json' } },
          );
        const number = Number(url.searchParams.get('comparison') ?? 7);
        body.latest = 8;
        body.delivered = {
          state: number === 7 ? 'delivered' : 'differs',
          statement: 'Comparison #8 is not the delivered pair.',
          comparison: 7,
          shown: number,
        };
      }
      return new Response(JSON.stringify(body), { status: 200, headers: { 'content-type': 'application/json' } });
    }),
  );
  return router;
}

it('a default frame with no recorded history opens the older delivered #7', async () => {
  fixture();
  renderPage();
  await waitFor(() => expect(screen.getByTestId('review-comparison-button').textContent).toContain('#7'));
  expect(screen.queryByTestId('review-delivered-line')).toBeNull();
});

it('the delivered link leaves the recorded frame the task entry opened', async () => {
  fixture();
  renderPage({ history: 'recorded' });
  const open = await screen.findByTestId('review-delivered-open');
  expect(screen.getByTestId('review-comparison-button').textContent).toContain('#8');
  fireEvent.click(open);
  await waitFor(() => expect(screen.getByTestId('review-comparison-button').textContent).toContain('#7'));
});

it('a refused notice-bearing read stays explicitly unavailable on the page', async () => {
  fixture(true);
  renderPage();
  await waitFor(() => expect(screen.getByTestId('review-comparison-button').textContent).toContain('#7'));
  await waitFor(() =>
    expect(screen.getByTestId('review-delivered-line').textContent).toMatch(/unavailable|cannot/i),
  );
});

// The menu half of the same criterion (F06): the sealed acceptance says "delivered link/menu changes
// the real target/frame coherently", and A2 closed only the link. In an explicit recorded frame the
// delivered row names #7 while the page stays on #8 unless the chooser itself leaves the frame.
it('the delivered menu row leaves the recorded frame', async () => {
  fixture();
  renderPage({ history: 'recorded' });
  await screen.findByTestId('review-delivered-open');
  expect(screen.getByTestId('review-comparison-button').textContent).toContain('#8');
  fireEvent.click(screen.getByTestId('review-comparison-button'));
  const row = await screen.findByTestId('review-comparison-delivered');
  expect(row.textContent).toContain('#7');
  fireEvent.click(row);
  await waitFor(() => expect(screen.getByTestId('review-comparison-button').textContent).toContain('#7'));
});

// The delivered row and a numbered row are different requests: the row opens the delivered pair and
// leaves the frame, while a number keeps the frame it was opened in. Pinning both here keeps a future
// "one chooser for everything" from silently putting the menu back on the recorded frame.
it('a numbered row keeps the frame the delivered row leaves', async () => {
  fixture();
  renderPage({ history: 'recorded' });
  await screen.findByTestId('review-delivered-open');
  fireEvent.click(screen.getByTestId('review-comparison-button'));
  const numbered = (await screen.findAllByTestId('review-comparison-option')).find(
    (row) => row.dataset.number === '6',
  );
  expect(numbered).toBeDefined();
  fireEvent.click(numbered as HTMLElement);
  await waitFor(() => expect(screen.getByTestId('review-comparison-button').textContent).toContain('#6'));
});

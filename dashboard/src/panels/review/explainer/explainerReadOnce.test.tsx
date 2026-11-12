// D569: each explainer read once per open. Mounted under StrictMode (the dev build), the release and
// re-acquire of the read's effect must join the request in flight, and the comparison's reference
// arriving after its number must not ask the same answer again.
import { StrictMode, type ReactNode } from 'react';
import { act, cleanup, renderHook, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';

import { publishReviewPublication } from '../../../data/reviewMemoryWatch';
import { useExplainer, useExplainerBadge } from './explainerRead';

const target = { repository: 'repo', master: 'master', leaf: 'leaf' };
const scope = { repo: 'repo', master: 'master', leaf: 'leaf' };
const strict = ({ children }: { children: ReactNode }) => <StrictMode>{children}</StrictMode>;

interface Asked {
  path: string;
  search: URLSearchParams;
  // The request was cancelled before it answered (an abort after the answer cancels nothing).
  abortedInFlight: boolean;
}

function serve(): Asked[] {
  const asked: Asked[] = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (address: string | URL, init?: RequestInit) => {
      const url = new URL(String(address), 'http://localhost');
      const record: Asked = {
        path: url.pathname.split('/').pop()!,
        search: url.searchParams,
        abortedInFlight: false,
      };
      asked.push(record);
      let answered = false;
      init?.signal?.addEventListener('abort', () => {
        if (!answered) record.abortedInFlight = true;
      });
      await Promise.resolve(); // on the wire for at least one tick, as any request is
      answered = true;
      const body = url.pathname.endsWith('/revisions')
        ? { state: 'ok', revisions: [] }
        : url.pathname.endsWith('/stale')
          ? {
              state: 'ok',
              stale: { parts: [], total: 2 },
              parts: [],
              total: 2,
              explanation: { revision: 3 },
              movedNotice: null,
            }
          : {
              state: 'ok',
              seat: { state: 'live' },
              explanation: { revision: 3, at: 'now', entrypoint: 'index.html' },
            };
      return new Response(JSON.stringify(body));
    }),
  );
  return asked;
}

const reads = (asked: readonly Asked[], path: string) => asked.filter((read) => read.path === path);

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it('reads the full state and the revisions once under StrictMode, with nothing aborted', async () => {
  const asked = serve();
  const hook = renderHook(
    () => useExplainer(target, { reference: 'ref-8', number: 8, owner: 'recorded' }),
    {
      wrapper: strict,
    },
  );
  await waitFor(() => expect(hook.result.current.state?.explanation?.revision).toBe(3));
  await act(async () => {});
  expect(reads(asked, 'state')).toHaveLength(1);
  expect(reads(asked, 'revisions')).toHaveLength(1);
  expect(asked.some((read) => read.abortedInFlight)).toBe(false);
});

it('reads the badge once under StrictMode and asks no seat state or revision list for it', async () => {
  const asked = serve();
  const hook = renderHook(
    () => useExplainerBadge(target, { reference: 'ref-8', number: 8, owner: 'recorded' }, true),
    {
      wrapper: strict,
    },
  );
  await waitFor(() => expect(hook.result.current.badge?.stale?.total).toBe(2));
  await act(async () => {});
  expect(asked.map((read) => read.path)).toEqual(['stale']);
  expect(reads(asked, 'stale')[0].search.get('comparisonNumber')).toBe('8');
  expect(hook.result.current.badge?.explanation?.revision).toBe(3);
  expect(asked.some((read) => read.abortedInFlight)).toBe(false);
  // A publication of this leaf renews the badge (F17), still without the seat.
  act(() => publishReviewPublication(scope));
  await waitFor(() => expect(reads(asked, 'stale')).toHaveLength(2));
  expect(reads(asked, 'state')).toHaveLength(0);
});

it('does not ask again when the comparison on screen gains its reference after its number', async () => {
  const asked = serve();
  const hook = renderHook(
    ({ reference }: { reference: string }) =>
      useExplainer(target, { reference, number: 8, owner: 'recorded' }),
    { initialProps: { reference: 'review-comparison:master/leaf/8' } },
  );
  await waitFor(() => expect(hook.result.current.phase).toBe('idle'));
  hook.rerender({ reference: 'c3384cf805dd42b4' });
  await act(async () => {});
  expect(reads(asked, 'state')).toHaveLength(1);
  expect(reads(asked, 'revisions')).toHaveLength(1);
  expect(reads(asked, 'state')[0].search.get('comparisonNumber')).toBe('8');
});

it('stays silent on a publication while its own read is off and the page follows it elsewhere', async () => {
  const asked = serve();
  renderHook(() =>
    useExplainer(target, undefined, false, undefined, { watchWhileDisabled: false }),
  );
  act(() => publishReviewPublication(scope));
  await act(async () => {});
  expect(asked).toHaveLength(0);
});

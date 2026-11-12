import { act, renderHook, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';

import { resetCardCachesForTests, useChangeSetCards } from './reviewTrees';

const requests: URL[] = [];
function serve() {
  vi.stubGlobal('fetch', vi.fn(async (url: string) => {
    const request = new URL(url, 'http://localhost');
    requests.push(request);
    const id = request.searchParams.get('invariants') ?? 'owner';
    return { ok: true, status: 200, json: async () => ({
      state: 'trees', repository_id: 'r', master: 'm', leaf_id: 'l', knowledge_sides: [],
      comparison: { number: Number(request.searchParams.get('comparison')) },
      entries: [{ id: request.searchParams.get('entry') ?? `entry-${id}`, invariant_key: id }],
    }) } as Response;
  }));
}
afterEach(() => {
  resetCardCachesForTests();
  vi.unstubAllGlobals();
  requests.length = 0;
});

it('opens without cards, loads only the selected invariant, and retains visited sidebar locations', async () => {
  serve();
  const hook = renderHook(({ ids, comparison }: { ids: string[]; comparison: number }) =>
    useChangeSetCards('r', 'm', 'l', comparison, ids, true, {}),
  { initialProps: { ids: [] as string[], comparison: 1 } });
  expect(requests).toHaveLength(0);
  expect(hook.result.current).toMatchObject({ phase: 'cards', coverage: { loaded: [], pending: [] } });
  hook.rerender({ ids: ['A'], comparison: 1 });
  await waitFor(() => expect(hook.result.current).toMatchObject({ coverage: { loaded: ['A'] } }));
  hook.rerender({ ids: ['B'], comparison: 1 });
  expect(hook.result.current).toMatchObject({ coverage: { loaded: ['A'], pending: ['B'] } });
  await waitFor(() => expect(hook.result.current).toMatchObject({ coverage: { loaded: ['A', 'B'] } }));
  expect(requests.map((url) => url.searchParams.get('invariants'))).toEqual(['A', 'B']);
  hook.rerender({ ids: [] as string[], comparison: 1 });
  hook.rerender({ ids: ['A'], comparison: 1 });
  expect(requests).toHaveLength(2);
  hook.rerender({ ids: ['A'], comparison: 2 });
  expect(hook.result.current).toMatchObject({ entries: [], coverage: { loaded: [], pending: ['A'] } });
  await waitFor(() => expect(requests).toHaveLength(3));
  await act(async () => {});
});

it('resolves an entry link on its exact side and reuses the owning invariant answer', async () => {
  serve();
  const hook = renderHook(({ ids, entry }: { ids: string[]; entry?: string }) =>
    useChangeSetCards('r', 'm', 'l', 1, ids, true, entry ? { entry, side: 'before' } : {}),
  { initialProps: { ids: [] as string[], entry: 'saved-entry' as string | undefined } });
  await waitFor(() => expect(hook.result.current).toMatchObject({ coverage: { loaded: ['owner'] } }));
  expect(requests[0].searchParams.get('entry_side')).toBe('before');
  expect(requests[0].searchParams.has('invariants')).toBe(false);
  hook.rerender({ ids: ['owner'], entry: undefined });
  await act(async () => {});
  expect(requests).toHaveLength(1);
  expect(hook.result.current).toMatchObject({ coverage: { loaded: ['owner'], pending: [] } });
});

it('reports selected read failures without treating an unread invariant as empty', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => ({ ok: false, status: 500, json: async () => ({}) } as Response)));
  const hook = renderHook(() => useChangeSetCards('r', 'm', 'l', 1, ['A'], true, {}));
  await waitFor(() => expect(hook.result.current).toMatchObject({ coverage: { loaded: [], pending: [], failures: { A: expect.any(String) } } }));
});

it('shares identical reads until the last reader leaves, then aborts on the wire', async () => {
  const signals: AbortSignal[] = [];
  vi.stubGlobal('fetch', vi.fn((_url: string, init: RequestInit) => new Promise((_resolve, reject) => {
    const signal = init.signal as AbortSignal;
    signals.push(signal);
    signal.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')));
  })));
  const one = renderHook(() => useChangeSetCards('r', 'm', 'l', 1, ['A'], true, {}));
  const two = renderHook(() => useChangeSetCards('r', 'm', 'l', 1, ['A'], true, {}));
  await act(async () => {});
  expect(signals).toHaveLength(1);
  one.unmount();
  await act(async () => {});
  expect(signals[0].aborted).toBe(false);
  two.unmount();
  await act(async () => {});
  expect(signals[0].aborted).toBe(true);
});

it('aborts a superseded invariant read and does not retain its late response', async () => {
  const signals: AbortSignal[] = [];
  const replies: ((value: Response) => void)[] = [];
  vi.stubGlobal('fetch', vi.fn((_url: string, init: RequestInit) => new Promise<Response>((resolve) => {
    signals.push(init.signal as AbortSignal);
    replies.push(resolve);
  })));
  const hook = renderHook(({ id }) => useChangeSetCards('r', 'm', 'l', 1, [id], true, {}), { initialProps: { id: 'A' } });
  await act(async () => {});
  hook.rerender({ id: 'B' });
  await act(async () => {});
  expect(signals[0].aborted).toBe(true);
  expect(signals[1].aborted).toBe(false);
  const body = (id: string) => ({ ok: true, status: 200, json: async () => ({
    state: 'trees', knowledge_sides: [], comparison: { number: 1 }, entries: [{ id, invariant_key: id }],
  }) }) as Response;
  await act(async () => { replies[0](body('A')); replies[1](body('B')); });
  expect(hook.result.current).toMatchObject({ entries: [{ id: 'B' }], coverage: { loaded: ['B'] } });
  hook.rerender({ id: 'A' });
  await act(async () => {});
  expect(signals).toHaveLength(3);
  hook.unmount();
});

it('does not reuse a kept answer when the admitted tree identity changes', async () => {
  serve();
  const hook = renderHook(({ identity }) => useChangeSetCards('r', 'm', 'l', 1, ['A'], true, { identity }),
    { initialProps: { identity: 'four-trees-1' } });
  await waitFor(() => expect(hook.result.current).toMatchObject({ coverage: { loaded: ['A'] } }));
  hook.rerender({ identity: 'four-trees-2' });
  expect(hook.result.current).toMatchObject({ entries: [], coverage: { loaded: [], pending: ['A'] } });
  await waitFor(() => expect(requests).toHaveLength(2));
  await act(async () => {});
});

// L81-R1-F18 at the client: the cards read survives the loading unmount and starts early.
//
// The loading switch unmounts the cards while a newly selected subject resolves; the kept cards
// answer used to die with the hook and refetch on remount, and a changed set waited for the
// subject before its cards request even started. These cases mount the hook with deferred fetch
// answers: an unchanged set sends no second request through pending or answer, a prefetched
// changed set is already in flight when its hook mounts, and an old answer never renders under a
// new question.

import { act, renderHook, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { prefetchCardEntries, useReviewTreeEntries } from './reviewTrees';
import { resetCardCachesForTests } from './reviewTrees';

const treesBody = (marker: string) => ({
  state: 'trees',
  repository_id: 'r',
  master: 'm',
  leaf_id: 'l',
  knowledge_sides: [],
  comparison: {
    schema: 'ar-review-tree-comparison/v1',
    task_id: 't',
    leaf_id: 'l',
    number: 1,
    marker,
  },
});

afterEach(() => {
  resetCardCachesForTests();
  vi.unstubAllGlobals();
});

function serveDeferred(): { calls: string[]; resolve: (body: unknown) => void } {
  const calls: string[] = [];
  let release!: (body: unknown) => void;
  const gate = new Promise<unknown>((resolve) => {
    release = resolve;
  });
  vi.stubGlobal(
    'fetch',
    vi.fn((address: string) => {
      calls.push(new URL(address, 'http://localhost').searchParams.get('invariants') ?? '');
      return gate.then(
        (body) => ({ ok: true, status: 200, json: async () => body }) as Response,
      );
    }),
  );
  return { calls, resolve: release };
}

describe("a selection's cards read", () => {
  it('sends no second request when the same set remounts through pending and answer', async () => {
    const { calls, resolve } = serveDeferred();
    const props = { invariants: ['INV-A', 'INV-B'] };
    const first = renderHook(
      ({ invariants }: { invariants: string[] }) => useReviewTreeEntries('r', 'm', 'l', 1, invariants),
      { initialProps: props },
    );
    expect(first.result.current).toEqual({ phase: 'loading' });
    expect(calls).toEqual(['INV-A,INV-B']);
    // The loading switch unmounts the cards while the request is still pending.
    first.unmount();
    const second = renderHook(
      ({ invariants }: { invariants: string[] }) => useReviewTreeEntries('r', 'm', 'l', 1, invariants),
      { initialProps: props },
    );
    expect(second.result.current).toEqual({ phase: 'loading' });
    expect(calls).toEqual(['INV-A,INV-B']);
    // The shared answer lands once and shows on the remount with no second request.
    await act(async () => {
      resolve(treesBody('first'));
    });
    await waitFor(() =>
      expect(second.result.current).toEqual({
        phase: 'trees',
        trees: expect.objectContaining({ comparison: expect.objectContaining({ marker: 'first' }) }),
      }),
    );
    expect(calls).toEqual(['INV-A,INV-B']);
  });

  it('starts a changed set before its hook mounts, beside the subject request', async () => {
    const { calls, resolve } = serveDeferred();
    // The member click prefetches from the displayed family before the subject resolves.
    prefetchCardEntries('r', 'm', 'l', 1, ['INV-C']);
    expect(calls).toEqual(['INV-C']);
    // The hook mounts later, after the subject answer, and reuses the flight.
    const mounted = renderHook(() => useReviewTreeEntries('r', 'm', 'l', 1, ['INV-C']));
    expect(mounted.result.current).toEqual({ phase: 'loading' });
    expect(calls).toEqual(['INV-C']);
    await act(async () => {
      resolve(treesBody('prefetched'));
    });
    await waitFor(() =>
      expect(mounted.result.current).toEqual({
        phase: 'trees',
        trees: expect.objectContaining({
          comparison: expect.objectContaining({ marker: 'prefetched' }),
        }),
      }),
    );
    expect(calls).toEqual(['INV-C']);
  });

  it('never shows an old answer under a new question', async () => {
    const { calls, resolve } = serveDeferred();
    const rendered = renderHook(
      ({ invariants }: { invariants: string[] }) => useReviewTreeEntries('r', 'm', 'l', 1, invariants),
      { initialProps: { invariants: ['INV-A'] } },
    );
    await act(async () => {
      resolve(treesBody('old'));
    });
    await waitFor(() => expect(rendered.result.current).toEqual(expect.objectContaining({ phase: 'trees' })));
    // A changed set is a new question: loading, never the old subject's answer.
    rendered.rerender({ invariants: ['INV-B'] });
    expect(rendered.result.current).toEqual({ phase: 'loading' });
    expect(calls).toEqual(['INV-A', 'INV-B']);
  });
});

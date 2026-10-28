// The review page's read 5 at the client (MIK-R89 v2 rule 2; L81-R1-F18): the cards of the shown
// subjects, ⌈i/500⌉ chunk reads kept outside the hook. A remount with an unchanged set sends no second
// request through pending or answer; a changed set is a new question and never shows the old answer;
// a failed chunk is this mount's to show and is asked again on a remount.

import { act, renderHook, waitFor } from '@testing-library/react';
import { createElement, type ReactNode } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { IntentEntryRevalidation, useRevalidateIntentEntry } from './intentEntryRevalidation';
import { CARD_CHUNK, resetCardCachesForTests, useChangeSetCards } from './reviewTrees';

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

const cardsOf = (invariants: string[]) =>
  renderHook(({ wanted }: { wanted: string[] }) => useChangeSetCards('r', 'm', 'l', 1, wanted, true), {
    initialProps: { wanted: invariants },
  });

describe("the page's card read", () => {
  it('sends no second request when the same set remounts through pending and answer', async () => {
    const { calls, resolve } = serveDeferred();
    const first = cardsOf(['INV-B', 'INV-A']);
    await act(async () => {});
    expect(first.result.current).toMatchObject({ phase: 'cards', coverage: { loaded: [], pending: ['INV-B', 'INV-A'] } });
    expect(calls).toEqual(['INV-A,INV-B']);
    first.unmount();
    const second = cardsOf(['INV-A', 'INV-B']);
    await act(async () => {});
    expect(second.result.current).toMatchObject({ phase: 'cards', coverage: { loaded: [], pending: ['INV-A', 'INV-B'] } });
    expect(calls).toEqual(['INV-A,INV-B']);
    await act(async () => {
      resolve(treesBody('first'));
    });
    await waitFor(() =>
      expect(second.result.current).toEqual(
        expect.objectContaining({ phase: 'cards', comparison: expect.objectContaining({ marker: 'first' }) }),
      ),
    );
    expect(calls).toEqual(['INV-A,INV-B']);
  });

  it('reads at most CARD_CHUNK invariants at a time', async () => {
    const { calls, resolve } = serveDeferred();
    const wanted = Array.from({ length: CARD_CHUNK * 2 + 1 }, (_, index) => `INV-${String(index).padStart(4, '0')}`);
    const mounted = cardsOf(wanted);
    await act(async () => {});
    expect(calls.map((call) => call.split(',').length)).toEqual([CARD_CHUNK, CARD_CHUNK, 1]);
    await act(async () => {
      resolve(treesBody('chunks'));
    });
    await waitFor(() => expect(mounted.result.current).toEqual(expect.objectContaining({ phase: 'cards' })));
  });

  it('keeps visited locations without calling a new selection answered', async () => {
    const { calls, resolve } = serveDeferred();
    const rendered = cardsOf(['INV-A']);
    await act(async () => {
      resolve(treesBody('old'));
    });
    await waitFor(() => expect(rendered.result.current).toEqual(expect.objectContaining({ phase: 'cards' })));
    rendered.rerender({ wanted: ['INV-B'] });
    expect(rendered.result.current).toMatchObject({ coverage: { loaded: ['INV-A'], pending: ['INV-B'] } });
    await act(async () => {});
    expect(calls).toEqual(['INV-A', 'INV-B']);
  });

  it('shows a failed chunk on this mount and asks again on a remount', async () => {
    const calls: string[] = [];
    vi.stubGlobal(
      'fetch',
      vi.fn(async (address: string) => {
        calls.push(new URL(address, 'http://localhost').searchParams.get('invariants') ?? '');
        return { ok: false, status: 500, json: async () => ({}) } as Response;
      }),
    );
    const first = cardsOf(['INV-A']);
    await waitFor(() => expect(first.result.current).toMatchObject({ coverage: { failures: { 'INV-A': expect.any(String) } } }));
    first.unmount();
    cardsOf(['INV-A']);
    await waitFor(() => expect(calls).toEqual(['INV-A', 'INV-A']));
  });

  // MIK-R89 v2 rule 3, Failure and Recovery: values that failed on a readable after side are this
  // mount's to show and are never kept; the reader's Refresh reads them again, and an answer with
  // its values, or one whose after side is the record's own unconverted state, is kept and reused.
  it('keeps no answer whose memory values failed, and reads it again on a refresh', async () => {
    const sides = (after: string) => [
      { side: 'before', state: 'available', tree: 'a'.repeat(40), problems: [] },
      { side: 'after', state: after, tree: 'b'.repeat(40), problems: [] },
    ];
    const memory = (state: string) => ({
      state,
      tree: 'b'.repeat(40),
      ...(state === 'available' ? {} : { reason: 'the after index could not be built' }),
    });
    const bodies: object[] = [
      {
        ...treesBody('failed'),
        knowledge_sides: sides('available'),
        entry_memory: memory('unavailable'),
      },
      {
        ...treesBody('recovered'),
        knowledge_sides: sides('available'),
        entry_memory: memory('available'),
      },
    ];
    const calls: string[] = [];
    vi.stubGlobal(
      'fetch',
      vi.fn(async (address: string) => {
        calls.push(new URL(address, 'http://localhost').searchParams.get('invariants') ?? '');
        const body = bodies.length > 1 ? bodies.shift() : bodies[0];
        return { ok: true, status: 200, json: async () => body } as Response;
      }),
    );
    const wrapper = ({ children }: { children: ReactNode }) =>
      createElement(IntentEntryRevalidation, { shown: false, children });
    const page = renderHook(
      () => ({
        cards: useChangeSetCards('r', 'm', 'l', 1, ['INV-A'], true),
        refresh: useRevalidateIntentEntry(),
      }),
      { wrapper },
    );
    const shownMemory = () => {
      const cards = page.result.current.cards;
      return cards?.phase === 'cards' ? cards.memory?.state : undefined;
    };
    await waitFor(() => expect(shownMemory()).toBe('unavailable'));
    act(() => page.result.current.refresh());
    await waitFor(() => expect(shownMemory()).toBe('available'));
    expect(calls).toEqual(['INV-A', 'INV-A']);
    act(() => page.result.current.refresh());
    page.unmount();
    const again = cardsOf(['INV-A']);
    expect(again.result.current).toEqual(
      expect.objectContaining({
        phase: 'cards',
        memory: expect.objectContaining({ state: 'available' }),
      }),
    );
    expect(calls).toEqual(['INV-A', 'INV-A']);
    bodies.splice(0, bodies.length, {
      ...treesBody('unconverted'),
      knowledge_sides: sides('legacy-unavailable'),
      entry_memory: memory('unavailable'),
    });
    const unconverted = cardsOf(['INV-B']);
    await waitFor(() =>
      expect(unconverted.result.current).toEqual(expect.objectContaining({ phase: 'cards' })),
    );
    unconverted.unmount();
    cardsOf(['INV-B']);
    expect(calls).toEqual(['INV-A', 'INV-A', 'INV-B']);
  });
});

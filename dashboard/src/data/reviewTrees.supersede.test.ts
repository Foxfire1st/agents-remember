// MIK-R42 at the client: a leaf-wide read that a newer selection supersedes is aborted (so the
// server stops computing it), and an overload answer is retried a bounded number of times, a moment
// apart, before the reader shows "unavailable".

import { act, renderHook, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { BUSY_DELAYS_MS, BUSY_RETRIES, useReviewTrees } from './reviewTrees';

const busy = {
  state: 'refused',
  repository_id: 'r',
  master: 'm',
  leaf_id: 'l',
  knowledge_sides: [],
  refusal: {
    code: 'reviewer_busy',
    detail: 'the reviewer is computing other worklists',
    next_action: 'the reviewer is computing other worklists; retry',
  },
};
const changing = {
  ...busy,
  refusal: { ...busy.refusal, code: 'inputs_changing', detail: 'inputs changed again' },
};
const notConverted = {
  state: 'not-converted',
  repository_id: 'r',
  master: 'm',
  leaf_id: 'l',
  knowledge_sides: [],
};

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe('a leaf-wide read that a newer selection supersedes', () => {
  it('aborts the superseded request and shows only the newer answer', async () => {
    const signals: AbortSignal[] = [];
    const leaves: string[] = [];
    vi.stubGlobal(
      'fetch',
      vi.fn(
        (address: string, init?: RequestInit) =>
          new Promise<Response>((resolve, reject) => {
            const signal = init?.signal as AbortSignal;
            signals.push(signal);
            leaves.push(new URL(address, 'http://localhost').searchParams.get('leaf') ?? '');
            signal.addEventListener('abort', () => reject(new DOMException('x', 'AbortError')));
            if (leaves.length === 2)
              resolve({ ok: true, status: 200, json: async () => notConverted } as Response);
          }),
      ),
    );
    const { result, rerender } = renderHook(
      ({ leaf }: { leaf: string }) => useReviewTrees('r', 'm', leaf, { comparison: 1 }),
      { initialProps: { leaf: 'first' } },
    );
    expect(result.current).toEqual({ phase: 'loading' });
    rerender({ leaf: 'second' });
    await waitFor(() => expect(result.current).toEqual({ phase: 'not-converted' }));
    expect(leaves).toEqual(['first', 'second']);
    expect(signals[0].aborted).toBe(true);
    expect(signals[1].aborted).toBe(false);
  });

  it('aborts the request when the view goes away', () => {
    const signals: AbortSignal[] = [];
    vi.stubGlobal(
      'fetch',
      vi.fn((_address: string, init?: RequestInit) => {
        signals.push(init?.signal as AbortSignal);
        return new Promise<Response>(() => undefined);
      }),
    );
    const { unmount } = renderHook(() => useReviewTrees('r', 'm', 'l', { comparison: 1 }));
    expect(signals[0].aborted).toBe(false);
    unmount();
    expect(signals[0].aborted).toBe(true);
  });
});

describe('an overload answer', () => {
  function serveBusy(times: number, refusal: unknown = busy): { calls: () => number } {
    let calls = 0;
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        calls += 1;
        const body = calls <= times ? refusal : notConverted;
        return { ok: true, status: 200, json: async () => body } as Response;
      }),
    );
    return { calls: () => calls };
  }

  beforeEach(() => {
    vi.useFakeTimers();
  });

  it('is asked again a moment later and the reader never sees it when a retry answers', async () => {
    const served = serveBusy(2);
    const { result } = renderHook(() => useReviewTrees('r', 'm', 'l', { comparison: 1 }));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(BUSY_DELAYS_MS[0] + BUSY_DELAYS_MS[1] + 50);
    });
    expect(result.current).toEqual({ phase: 'not-converted' });
    expect(served.calls()).toBe(3);
  });

  it('keeps showing "computing" while it retries, and its horizon covers a draining queue', async () => {
    const served = serveBusy(BUSY_RETRIES);
    const { result } = renderHook(() => useReviewTrees('r', 'm', 'l', { comparison: 1 }));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(BUSY_DELAYS_MS[0] + BUSY_DELAYS_MS[1] + 100);
    });
    expect(served.calls()).toBe(3);
    expect(result.current).toEqual({ phase: 'loading' });
    expect(BUSY_DELAYS_MS).toEqual([1500, 2000, 3000, 4000, 5000, 6000, 8000]);
    expect(BUSY_RETRIES).toBe(7);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(BUSY_DELAYS_MS.reduce((sum, ms) => sum + ms, 0));
    });
    expect(result.current).toEqual({ phase: 'not-converted' });
    expect(served.calls()).toBe(8);
  });

  it('treats "the inputs keep changing" the same way', async () => {
    const served = serveBusy(1, changing);
    const { result } = renderHook(() => useReviewTrees('r', 'm', 'l', { comparison: 1 }));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(BUSY_DELAYS_MS[0] + 50);
    });
    expect(result.current).toEqual({ phase: 'not-converted' });
    expect(served.calls()).toBe(2);
  });

  it('is shown as unavailable only after the bounded number of retries', async () => {
    const served = serveBusy(100);
    const { result } = renderHook(() => useReviewTrees('r', 'm', 'l', { comparison: 1 }));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(BUSY_DELAYS_MS.reduce((sum, ms) => sum + ms, 0) + 50);
    });
    expect(result.current?.phase).toBe('unavailable');
    expect(served.calls()).toBe(8);
    const read = result.current;
    expect(read?.phase === 'unavailable' && read.problem.code).toBe('reviewer_busy');
  });

  it('stops retrying when the selection moves on', async () => {
    const leaves: string[] = [];
    const signals: AbortSignal[] = [];
    vi.stubGlobal(
      'fetch',
      vi.fn(async (address: string, init?: RequestInit) => {
        leaves.push(new URL(address, 'http://localhost').searchParams.get('leaf') ?? '');
        signals.push(init?.signal as AbortSignal);
        return { ok: true, status: 200, json: async () => busy } as Response;
      }),
    );
    const { rerender, unmount } = renderHook(
      ({ leaf }: { leaf: string }) => useReviewTrees('r', 'm', leaf, { comparison: 1 }),
      { initialProps: { leaf: 'first' } },
    );
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10);
    });
    expect(leaves).toEqual(['first']);
    expect(vi.getTimerCount()).toBe(1);
    rerender({ leaf: 'second' });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10);
    });
    expect(signals[0].aborted).toBe(true);
    expect(vi.getTimerCount()).toBe(1);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1600);
    });
    expect(leaves).toEqual(['first', 'second', 'second']);
    unmount();
    expect(vi.getTimerCount()).toBe(0);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(60000);
    });
    expect(leaves).toEqual(['first', 'second', 'second']);
  });
});

import { act, renderHook, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';

import { useIntentReviewSummary } from './reviewIntentSummary';
import { useReviewLane } from './reviewLane';
import { useReviewTrees } from './reviewTrees';
import { dashboardStore } from './store';
import { useReviewCatalogue } from './useReviewCatalogue';

afterEach(() => {
  vi.unstubAllGlobals();
  dashboardStore.getState().reset();
});

const reads = [
  { name: 'catalogue', useRead: (leaf: string) => useReviewCatalogue({ repo: 'r', master: 'm', leaf }) },
  { name: 'lane', useRead: (leaf: string) => useReviewLane('r', 'm', leaf, 1) },
  { name: 'trees', useRead: (leaf: string) => useReviewTrees('r', 'm', leaf, { comparison: 1 }) },
  { name: 'summary', useRead: (leaf: string) => useIntentReviewSummary('r', 'm', leaf, 'facts', 1) },
];

it.each(reads)('starts one $name request during StrictMode effect replay', async ({ useRead }) => {
  const fetch = vi.fn(async () => new Response(JSON.stringify({ state: 'not-converted', knowledge_sides: [] })));
  vi.stubGlobal('fetch', fetch);
  const view = renderHook(() => useRead('leaf'), {
    reactStrictMode: true,
  });
  await act(async () => {});
  expect(fetch).toHaveBeenCalledOnce();
  view.unmount();
});

it.each(reads)('cancels obsolete $name requests when the leaf changes or unmounts', async ({ useRead }) => {
  const signals: AbortSignal[] = [];
  vi.stubGlobal('fetch', vi.fn((_url: string, init?: RequestInit) => {
    if (init?.signal) signals.push(init.signal);
    return new Promise<Response>(() => undefined);
  }));
  const view = renderHook(({ leaf }) => useRead(leaf), { initialProps: { leaf: 'first' } });
  await waitFor(() => expect(signals).toHaveLength(1));
  view.rerender({ leaf: 'second' });
  await waitFor(() => expect(signals).toHaveLength(2));
  expect(signals[0].aborted).toBe(true);
  expect(signals[1].aborted).toBe(false);
  view.unmount();
  await act(async () => {});
  expect(signals[1].aborted).toBe(true);
});

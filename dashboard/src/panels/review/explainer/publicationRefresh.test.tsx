import { act, cleanup, render, renderHook, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { useExplainer } from './explainerRead';
import { ExplanationView } from './ExplanationView';
import { publishReviewPublication, publishReviewExplainerMoved, reviewPublicationScope, reviewMemoryScope } from '../../../data/reviewMemoryWatch';
import { reviewPageId } from '../../../data/reviewOpen';
import { connectState } from '../../../data/stream';

class Source {
  static latest: Source;
  closed = false;
  listeners = new Map<string, (event: MessageEvent) => void>();
  constructor(public url: string) { Source.latest = this; }
  addEventListener(name: string, callback: (event: MessageEvent) => void) { this.listeners.set(name, callback); }
  close() { this.closed = true; }
  emit(name: string, data: unknown) { this.listeners.get(name)?.(new MessageEvent(name, { data: JSON.stringify(data) })); }
}
const target = { repository: 'repo', master: 'master', leaf: 'leaf' };
const scope = { repo: 'repo', master: 'master', leaf: 'leaf' };
let dispose: (() => void) | undefined;
afterEach(() => { cleanup(); dispose?.(); dispose = undefined; vi.unstubAllGlobals(); vi.restoreAllMocks(); });

it('refreshes saved state and revisions on a same-leaf publication for a landed leaf, without reload or mutations', async () => {
  let revision: number | null = null;
  const reads: string[] = [];
  const fetch = vi.fn(async (address: string | URL, init?: RequestInit) => {
    expect(init?.method ?? 'GET').toBe('GET');
    const url = new URL(String(address), 'http://localhost');
    reads.push(url.pathname);
    return new Response(JSON.stringify(url.pathname.endsWith('/revisions')
      ? { state: 'ok', revisions: revision ? [{ revision, at: '2026-10-25', current: true }] : [] }
      : { state: 'ok', seat: { state: 'absent' }, explanation: revision ? { revision, at: '2026-10-25', entrypoint: 'index.html' } : null }));
  });
  vi.stubGlobal('fetch', fetch);
  vi.stubGlobal('EventSource', Source);
  dispose = connectState();
  const hook = renderHook(() => useExplainer(target));
  await waitFor(() => expect(hook.result.current.phase).toBe('idle'));
  expect(hook.result.current.state?.explanation).toBeNull();
  expect(reviewMemoryScope()).toBeUndefined();
  expect(reviewPublicationScope()).toEqual(scope);
  const source = Source.latest;
  const query = new URL(source.url, 'http://localhost').searchParams;
  expect(query.get('publication')).toBe('repo/master/leaf');
  expect(query.get('page')).toBe(reviewPageId()); // its opens are released when this page leaves (D585)
  expect(query.has('reviewLeaf')).toBe(false);
  const initialReads = fetch.mock.calls.length;
  act(() => source.emit('review.explainer.changed', { ...scope, leaf: 'foreign', revision: '1' }));
  expect(fetch).toHaveBeenCalledTimes(initialReads);
  revision = 1;
  act(() => source.emit('review.explainer.changed', { ...scope, revision: '2' }));
  await waitFor(() => expect(hook.result.current.state?.explanation?.revision).toBe(1));
  expect(reads.filter((path) => path.endsWith('/revisions'))).toHaveLength(2);
  hook.unmount();
  expect(source.closed).toBe(true);
  const count = fetch.mock.calls.length;
  act(() => source.emit('review.explainer.changed', { ...scope, revision: '3' }));
  expect(fetch).toHaveBeenCalledTimes(count);
});

it('refreshes on a same-leaf publication even while its own read is disabled (F17)', async () => {
  // Measured cause of F17: the review page's six /api/stream connections between 16:41Z and 17:17Z
  // carried no `publication` parameter, so the server never opened its publication watcher and the
  // page never heard `review.explainer.changed`. A column whose own read is off must still watch.
  let revision = 3;
  const fetch = vi.fn(async (address: string | URL) => {
    const url = new URL(String(address), 'http://localhost');
    return new Response(JSON.stringify(url.pathname.endsWith('/revisions')
      ? { state: 'ok', revisions: [{ revision, at: '2026-10-25', current: true }] }
      : { state: 'ok', seat: { state: 'absent' }, explanation: { revision, at: '2026-10-25', entrypoint: 'index.html' } }));
  });
  vi.stubGlobal('fetch', fetch);
  vi.stubGlobal('EventSource', Source);
  dispose = connectState();
  const hook = renderHook(() => useExplainer(target, undefined, false));
  expect(reviewPublicationScope()).toEqual(scope);
  expect(new URL(Source.latest.url, 'http://localhost').searchParams.get('publication')).toBe('repo/master/leaf');
  revision = 4;
  act(() => Source.latest.emit('review.explainer.changed', { ...scope, revision: '2' }));
  await waitFor(() => expect(hook.result.current.state?.explanation?.revision).toBe(4));
});

it('coalesces publications during an initial read and preserves an explicit revision', async () => {
  const queries: string[] = [];
  let release!: () => void;
  const held = new Promise<void>((resolve) => { release = resolve; });
  let stateCalls = 0;
  vi.stubGlobal('fetch', vi.fn(async (address: string | URL) => {
    const url = new URL(String(address), 'http://localhost');
    if (url.pathname.endsWith('/state')) {
      queries.push(url.search);
      stateCalls += 1;
      if (stateCalls === 1) await held;
    }
    return new Response(JSON.stringify(url.pathname.endsWith('/revisions')
      ? { state: 'ok', revisions: [] }
      : { state: 'ok', seat: { state: 'absent' }, explanation: { revision: 4, at: 'now', entrypoint: 'index.html' } }));
  }));
  const hook = renderHook(() => useExplainer(target, undefined, true, 4));
  act(() => { publishReviewPublication(scope); publishReviewPublication(scope); publishReviewPublication(scope); });
  expect(stateCalls).toBe(1);
  await act(async () => { release(); await held; });
  await waitFor(() => expect(hook.result.current.phase).toBe('idle'));
  expect(stateCalls).toBe(2);
  expect(queries.every((query) => new URLSearchParams(query).get('revision') === '4')).toBe(true);
});

it('drops a superseded leaf response and its queued publication refresh', async () => {
  let release!: () => void;
  const held = new Promise<void>((resolve) => { release = resolve; });
  let oldReads = 0;
  const oldSignals: AbortSignal[] = [];
  vi.stubGlobal('fetch', vi.fn(async (address: string | URL, init?: RequestInit) => {
    const url = new URL(String(address), 'http://localhost');
    const oldLeaf = url.pathname.includes('/leaf/');
    if (oldLeaf && url.pathname.endsWith('/state')) { oldReads += 1; if (init?.signal) oldSignals.push(init.signal); await held; }
    return new Response(JSON.stringify(url.pathname.endsWith('/revisions')
      ? { state: 'ok', revisions: [] }
      : { state: 'ok', seat: { state: 'absent' }, explanation: { revision: oldLeaf ? 1 : 2, at: 'now', entrypoint: 'index.html' } }));
  }));
  const hook = renderHook(({ leaf }) => useExplainer({ ...target, leaf }), { initialProps: { leaf: 'leaf' } });
  act(() => publishReviewPublication(scope));
  hook.rerender({ leaf: 'new-leaf' });
  // The old request is cancelled on the wire once no mounted reader wants it: one microtask after
  // its last reader left, which is what lets a StrictMode remount re-join it instead.
  await act(async () => { await Promise.resolve(); });
  expect(oldSignals[0]?.aborted).toBe(true);
  await waitFor(() => expect(hook.result.current.state?.explanation?.revision).toBe(2));
  await act(async () => { release(); await held; });
  expect(hook.result.current.state?.explanation?.revision).toBe(2);
  expect(oldReads).toBe(1);
});

it('refreshes on a completed build cleared notice while preserving the moved path', async () => {
  let revision: number | null = null;
  const fetch = vi.fn(async (address: string | URL) => {
    const url = new URL(String(address), 'http://localhost');
    return new Response(JSON.stringify(url.pathname.endsWith('/revisions')
      ? { state: 'ok', revisions: [] }
      : { state: 'ok', seat: { state: 'absent' }, explanation: revision ? { revision, at: 'now', entrypoint: 'index.html' } : null }));
  });
  vi.stubGlobal('fetch', fetch);
  const hook = renderHook(() => useExplainer(target));
  await waitFor(() => expect(hook.result.current.phase).toBe('idle'));
  revision = 2;
  act(() => publishReviewExplainerMoved({ ...scope, cleared: true }));
  await waitFor(() => expect(hook.result.current.state?.explanation?.revision).toBe(2));
  expect(fetch).toHaveBeenCalledTimes(4);
});


it('shows an external terminal build failure after an environment event without remounting or sending', async () => {
  const reason = 'the comparison memory repository is unavailable';
  let failed = false;
  const fetch = vi.fn(async (address: string | URL, init?: RequestInit) => {
    expect(init?.method ?? 'GET').toBe('GET');
    const url = new URL(String(address), 'http://localhost');
    return new Response(JSON.stringify(url.pathname.endsWith('/revisions')
      ? { state: 'ok', revisions: [] }
      : {
        state: 'ok', seat: { state: 'retired' }, retired: true,
        retiredAt: '2026-10-25T12:00:00Z', explanation: null,
        build: failed ? {
          state: 'failed', requestId: 'external-failed', step: 'comparison',
          failedStep: 'comparison', error: reason, reason: 'evidence-source-unavailable', percent: 0,
        } : null,
      }));
  });
  vi.stubGlobal('fetch', fetch);
  vi.stubGlobal('EventSource', Source);
  dispose = connectState();
  const onExplain = vi.fn();
  render(<ExplanationView target={target} onExplain={onExplain} />);
  await screen.findByTestId('explanation-empty');
  const view = screen.getByTestId('explanation-view');
  const source = Source.latest;
  const before = fetch.mock.calls.length;
  failed = true;
  act(() => source.emit('review.explainer.changed', {
    ...scope, revision: 'external-change:1', kind: 'environment', pageStamp: null,
  }));
  const failure = await screen.findByTestId('explainer-build-failure');
  expect(failure.getAttribute('data-step')).toBe('comparison');
  expect(failure.textContent).toContain(reason);
  expect(failure.textContent).not.toContain('no reason was given');
  expect(screen.getByTestId('explanation-view')).toBe(view);
  expect(Source.latest).toBe(source);
  expect(onExplain).not.toHaveBeenCalled();
  expect(fetch.mock.calls.length).toBe(before + 2);
});

it('keeps one stream while the selected comparison changes: the review scope never moves for it', async () => {
  vi.stubGlobal('fetch', vi.fn(async (address: string | URL) => {
    const url = new URL(String(address), 'http://localhost');
    return new Response(JSON.stringify(url.pathname.endsWith('/revisions')
      ? { state: 'ok', revisions: [] }
      : { state: 'ok', seat: { state: 'absent' }, explanation: null }));
  }));
  const opened: string[] = [];
  class CountingSource extends Source {
    constructor(url: string) { super(url); opened.push(url); }
  }
  vi.stubGlobal('EventSource', CountingSource);
  dispose = connectState();
  const hook = renderHook(
    ({ number }: { number: number | undefined }) =>
      useExplainer(target, number === undefined ? undefined : { reference: `review-comparison:master/leaf/${number}`, number }),
    { initialProps: { number: undefined as number | undefined } },
  );
  await waitFor(() => expect(hook.result.current.phase).toBe('idle'));
  const streams = opened.length;
  hook.rerender({ number: 3 });
  hook.rerender({ number: undefined });
  hook.rerender({ number: 4 });
  await waitFor(() => expect(hook.result.current.phase).toBe('idle'));
  expect(opened).toHaveLength(streams);
  expect(reviewPublicationScope()).toEqual(scope);
});

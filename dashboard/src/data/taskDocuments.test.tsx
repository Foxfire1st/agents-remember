import { act, cleanup, renderHook, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { taskEntry, taskSummary } from '../test/fixtures/wire';
import { dashboardStore } from './store';
import { BoundedCache } from './boundedCache';
import { TaskDocumentResources, taskBodyKey } from './taskDocuments';
import { useTaskDocumentBody } from './useTaskDocumentBody';

const reference = { repository: 'repo', path: 'master/01.json' };
const body = (revision: string, objective = revision) =>
  taskSummary({ bodyRevision: revision, objective });
const entry = (revision: string) => taskEntry({ reference, summary: body(revision) });
const answer = (revision: string, objective?: string) =>
  new Response(JSON.stringify(body(revision, objective)), { headers: { etag: `W/"${revision}"` } });

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

beforeEach(() => dashboardStore.getState().reset());
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe('task bodies belong to the store and exact document revision', () => {
  it('deduplicates concurrent reads and reuses the parsed body after navigation', async () => {
    const pending = deferred<Response>();
    const fetch = vi.fn<(url: string, options?: RequestInit) => Promise<Response>>(
      () => pending.promise,
    );
    vi.stubGlobal('fetch', fetch);
    const resources = new TaskDocumentResources();
    const first = resources.load(reference, 'one');
    expect(resources.load(reference, 'one')).toBe(first);
    pending.resolve(answer('one', 'Whole document'));
    const complete = await first;
    expect(await resources.load(reference, 'one')).toBe(complete);
    expect(complete.body.objective).toBe('Whole document');
    expect(fetch).toHaveBeenCalledOnce();
    expect(String(fetch.mock.calls[0]?.[0])).toContain(encodeURIComponent('repo/master/01.json'));
  });

  it('keeps a late old answer under its own revision without replacing the new body', async () => {
    const old = deferred<Response>();
    const fetch = vi.fn().mockReturnValueOnce(old.promise).mockResolvedValueOnce(answer('new'));
    vi.stubGlobal('fetch', fetch);
    const resources = new TaskDocumentResources();
    const oldRead = resources.load(reference, 'old');
    const newer = await resources.load(reference, 'new');
    old.resolve(answer('old'));
    await oldRead;
    expect(resources.bodies.get(taskBodyKey(reference, 'new'))).toBe(newer);
    expect(await resources.load(reference, 'new')).toBe(newer);
    expect(fetch).toHaveBeenCalledTimes(2);
  });

  it('stores a response crossing a source edit only under the returned revision', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(answer('actual')));
    const resources = new TaskDocumentResources();
    const loaded = await resources.load(reference, 'requested');
    expect(resources.bodies.get(taskBodyKey(reference, 'requested'))).toBeUndefined();
    expect(resources.bodies.get(taskBodyKey(reference, 'actual'))).toBe(loaded);
  });

  it('retains the full body across reader unmount and uses current summary facts on return', async () => {
    const fetch = vi.fn().mockResolvedValue(answer('one', 'Whole objective'));
    vi.stubGlobal('fetch', fetch);
    const first = renderHook(() => useTaskDocumentBody(entry('one')));
    await waitFor(() => expect(first.result.current.state).toBe('available'));
    first.unmount();
    const current = entry('one');
    current.summary = { ...body('one'), type: 'updated type', statusNote: 'updated note' };
    const second = renderHook(() => useTaskDocumentBody(current));
    expect(second.result.current.state).toBe('available');
    expect(second.result.current.document?.objective).toBe('Whole objective');
    expect(second.result.current.document?.type).toBe('updated type');
    expect(second.result.current.document?.statusNote).toBe('updated note');
    expect(fetch).toHaveBeenCalledOnce();
  });

  it('ignores the selected old reader completion and renders only the current revision', async () => {
    const old = deferred<Response>();
    vi.stubGlobal(
      'fetch',
      vi.fn().mockReturnValueOnce(old.promise).mockResolvedValueOnce(answer('new')),
    );
    const view = renderHook(({ revision }) => useTaskDocumentBody(entry(revision)), {
      initialProps: { revision: 'old' },
    });
    expect(view.result.current.document?.objective).toBe('old');
    view.rerender({ revision: 'new' });
    await waitFor(() => expect(view.result.current.state).toBe('available'));
    await act(async () => {
      old.resolve(answer('old', 'Obsolete objective'));
      await old.promise;
    });
    expect(view.result.current.document?.objective).toBe('new');
  });

  it('shows a summary during loading and failure; an unauthored entry requests nothing', async () => {
    const pending = deferred<Response>();
    const fetch = vi.fn<(url: string, options?: RequestInit) => Promise<Response>>(
      () => pending.promise,
    );
    vi.stubGlobal('fetch', fetch);
    const view = renderHook(
      ({ authored }) => useTaskDocumentBody(authored ? entry('one') : taskEntry({ reference })),
      {
        initialProps: { authored: true },
      },
    );
    expect(view.result.current.document?.objective).toBe('one');
    expect(view.result.current.state).toBe('loading');
    pending.resolve(new Response('', { status: 503 }));
    await waitFor(() => expect(view.result.current.state).toBe('unavailable'));
    expect(view.result.current.document?.objective).toBe('one');
    view.rerender({ authored: false });
    expect(view.result.current.document).toBeUndefined();
    expect(view.result.current.state).toBeUndefined();
    expect(fetch).toHaveBeenCalledOnce();
  });

  it('settles a revision crossing as unavailable until its own summary arrives', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(answer('actual', 'Actual objective')));
    const view = renderHook(({ revision }) => useTaskDocumentBody(entry(revision)), {
      initialProps: { revision: 'requested' },
    });
    await waitFor(() => expect(view.result.current.state).toBe('unavailable'));
    expect(view.result.current.document?.objective).toBe('requested');
    view.rerender({ revision: 'actual' });
    expect(view.result.current.state).toBe('available');
    expect(view.result.current.document?.objective).toBe('Actual objective');
  });

  it('renders an oversized current body without retaining it across navigation', async () => {
    const objective = 'x'.repeat(17 * 1024 * 1024);
    const fetch = vi.fn().mockImplementation(() => Promise.resolve(answer('one', objective)));
    vi.stubGlobal('fetch', fetch);
    const first = renderHook(() => useTaskDocumentBody(entry('one')));
    await waitFor(() => expect(first.result.current.state).toBe('available'));
    expect(first.result.current.document?.objective).toHaveLength(objective.length);
    expect(dashboardStore.getState().taskDocuments.bodies.retained).toEqual({
      entries: 0,
      bytes: 0,
    });
    first.unmount();
    const second = renderHook(() => useTaskDocumentBody(entry('one')));
    await waitFor(() => expect(second.result.current.state).toBe('available'));
    expect(fetch).toHaveBeenCalledTimes(2);
  });
});

describe('retained resource bounds', () => {
  it('evicts the least recently used item and oversized bytes without exceeding either limit', () => {
    const cache = new BoundedCache<string, string>(2, 6);
    cache.set('a', 'aaa', 3);
    cache.set('b', 'bbb', 3);
    cache.get('a');
    cache.set('c', 'ccc', 3);
    expect(cache.get('b')).toBeUndefined();
    expect(cache.retained).toEqual({ entries: 2, bytes: 6 });
    cache.set('a', 'too large', 9);
    expect(cache.get('a')).toBeUndefined();
    expect(cache.retained).toEqual({ entries: 1, bytes: 3 });
  });

  it('bounds retained bodies and pure preparation across a long navigation history', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation(() => Promise.resolve(answer('one'))),
    );
    const resources = new TaskDocumentResources();
    for (let index = 0; index < 100; index += 1) {
      await resources.load({ repository: 'repo', path: `master/${index}.json` }, 'one');
      resources.prepare(`view-${index}`, () => ({ content: String(index) }));
    }
    expect(resources.bodies.retained.entries).toBe(64);
    expect(resources.retainedViews.entries).toBe(64);
    expect(resources.bodies.retained.bytes).toBeLessThanOrEqual(16 * 1024 * 1024);
    expect(resources.retainedViews.bytes).toBeLessThanOrEqual(16 * 1024 * 1024);
    expect(
      resources.bodies.get(taskBodyKey({ repository: 'repo', path: 'master/0.json' }, 'one')),
    ).toBeUndefined();
    const make = vi.fn(() => ({ content: 'recomputed after eviction' }));
    resources.prepare('view-0', make);
    resources.prepare('view-0', make);
    expect(make).toHaveBeenCalledOnce();
  });
});

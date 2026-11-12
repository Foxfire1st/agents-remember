import { afterEach, describe, expect, it, vi } from 'vitest';

import { ConditionalReads } from './conditionalReads';
import { TaskDocumentResources } from './taskDocuments';

/** L174-R1-01: a shared read belongs to its store, never to the first reader alone. */

const decode = (response: Response): Promise<{ n: number }> => response.json();

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((settle) => {
    resolve = settle;
  });
  return { promise, resolve };
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('conditional reads: owner abort leaves a live joiner alone', () => {
  it('settles the joiner from the same fetch and keeps the fetch alive', async () => {
    const gate = deferred<Response>();
    const fetch = vi.fn().mockReturnValue(gate.promise);
    vi.stubGlobal('fetch', fetch);
    const store = new ConditionalReads(4);
    const owner = new AbortController();

    const owned = store.load('/x', decode, '/x', owner.signal);
    const joined = store.load('/x', decode, '/x', new AbortController().signal);
    owner.abort();

    await expect(owned).rejects.toMatchObject({ name: 'AbortError' });
    expect((fetch.mock.calls[0][1] as RequestInit).signal?.aborted).toBe(false);

    gate.resolve(new Response('{"n":1}', { headers: { ETag: 'W/"1"' } }));
    await expect(joined).resolves.toEqual({ n: 1 });
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(store.peek('/x')).toEqual({ n: 1 });
  });

  it('ends the request with its last waiter and never rejoins an aborted one', async () => {
    const first = deferred<Response>();
    const second = deferred<Response>();
    const fetch = vi
      .fn()
      .mockReturnValueOnce(first.promise)
      .mockReturnValueOnce(second.promise);
    vi.stubGlobal('fetch', fetch);
    const store = new ConditionalReads(4);
    const owner = new AbortController();

    const read = store.load('/y', decode, '/y', owner.signal);
    owner.abort();
    await expect(read).rejects.toMatchObject({ name: 'AbortError' });
    expect((fetch.mock.calls[0][1] as RequestInit).signal?.aborted).toBe(true);

    const replacement = store.load('/y', decode, '/y');
    expect(fetch).toHaveBeenCalledTimes(2);
    second.resolve(new Response('{"n":2}', { headers: { ETag: 'W/"2"' } }));
    await expect(replacement).resolves.toEqual({ n: 2 });
  });
});

describe('task bodies: owner abort leaves a live joiner alone', () => {
  const reference = { repository: 'agents-remember', path: 'leaf.json' };

  it('settles the joiner from the same body fetch and keeps it alive', async () => {
    const gate = deferred<Response>();
    const fetch = vi.fn().mockReturnValue(gate.promise);
    vi.stubGlobal('fetch', fetch);
    const store = new TaskDocumentResources();
    const owner = new AbortController();

    const owned = store.load(reference, 'rev-1', '', owner.signal);
    const joined = store.load(reference, 'rev-1', '', new AbortController().signal);
    owner.abort();

    await expect(owned).rejects.toMatchObject({ name: 'AbortError' });
    expect((fetch.mock.calls[0][1] as RequestInit).signal?.aborted).toBe(false);

    gate.resolve(
      new Response('{"bodyRevision":"rev-1","title":"one"}', {
        headers: { ETag: 'W/"b1"' },
      }),
    );
    const record = await joined;
    expect(record.body.bodyRevision).toBe('rev-1');
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it('starts a new body fetch for an immediate replacement after an abort', async () => {
    const first = deferred<Response>();
    const second = deferred<Response>();
    const fetch = vi
      .fn()
      .mockReturnValueOnce(first.promise)
      .mockReturnValueOnce(second.promise);
    vi.stubGlobal('fetch', fetch);
    const store = new TaskDocumentResources();
    const owner = new AbortController();

    const read = store.load(reference, 'rev-2', '', owner.signal);
    owner.abort();
    await expect(read).rejects.toMatchObject({ name: 'AbortError' });
    expect((fetch.mock.calls[0][1] as RequestInit).signal?.aborted).toBe(true);

    const replacement = store.load(reference, 'rev-2');
    expect(fetch).toHaveBeenCalledTimes(2);
    second.resolve(
      new Response('{"bodyRevision":"rev-2","title":"two"}', {
        headers: { ETag: 'W/"b2"' },
      }),
    );
    await expect(replacement).resolves.toMatchObject({ body: { bodyRevision: 'rev-2' } });
  });
});

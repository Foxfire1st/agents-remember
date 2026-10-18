import { afterEach, describe, expect, it, vi } from 'vitest';

import { ConditionalReads } from './conditionalReads';
import { dashboardStore } from './store';

afterEach(() => {
  vi.unstubAllGlobals();
  dashboardStore.getState().reset();
});
const decode = (response: Response): Promise<{ n: number }> => response.json();

describe('bounded conditional page results', () => {
  it('retains parsed identity on 304 without reading a body, and replaces it on a new ETag', async () => {
    const cache = new ConditionalReads(2);
    const first = new Response('{"n":1}', { headers: { ETag: 'W/"one"' } });
    const unchanged = new Response(null, { status: 304 });
    const json = vi.spyOn(unchanged, 'json');
    const fetch = vi
      .fn()
      .mockResolvedValueOnce(first)
      .mockResolvedValueOnce(unchanged)
      .mockResolvedValueOnce(new Response('{"n":2}', { headers: { ETag: 'W/"two"' } }));
    vi.stubGlobal('fetch', fetch);
    const value = await cache.load('/one', decode, 'before');
    expect(cache.peek('/one', 'before')).toBe(value);
    expect(cache.peek('/one', 'after')).toBeUndefined();
    expect(await cache.load('/one', decode, 'after')).toBe(value);
    expect(cache.peek('/one', 'after')).toBe(value);
    expect(cache.peek('/one', 'before')).toBeUndefined();
    expect(json).not.toHaveBeenCalled();
    expect(fetch.mock.calls[1][1].headers).toEqual({ 'If-None-Match': 'W/"one"' });
    expect(await cache.load('/one', decode, 'after')).toEqual({ n: 2 });
    expect(cache.peek('/one', 'after')).not.toBe(value);
  });

  it.each([
    { name: 'changeSets' as const, capacity: 256 },
    { name: 'changeSetFiles' as const, capacity: 64 },
    { name: 'intentSummaries' as const, capacity: 128 },
  ])(
    'the actual $name store stays at $capacity over two growing histories and evicts the LRU',
    async ({ name, capacity }) => {
      vi.stubGlobal(
        'fetch',
        vi.fn(async () => new Response('{"n":1}')),
      );
      for (const multiple of [2, 3]) {
        dashboardStore.getState().reset();
        const cache = dashboardStore.getState()[name];
        const size = capacity * multiple;
        for (let i = 0; i < size; i++) await cache.load(`/task/${i}`, decode);
        expect(cache.retained.entries).toBe(capacity);
        expect(cache.peek('/task/0')).toBeUndefined();
        const reused = `/task/${size - capacity}`;
        cache.peek(reused);
        for (let i = size; i < size + capacity - 1; i++) await cache.load(`/task/${i}`, decode);
        expect(cache.retained.entries).toBe(capacity);
        expect(cache.peek(reused)).toEqual({ n: 1 });
        expect(cache.peek(`/task/${size - capacity + 1}`)).toBeUndefined();
      }
    },
  );

  it('the actual file-response store reaches its 16 MiB byte bound over two growing histories', async () => {
    const bytes = 1024 * 1024;
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string) => {
        const overhead = new TextEncoder().encode(url + JSON.stringify({ n: '' })).byteLength;
        return new Response(JSON.stringify({ n: 'x'.repeat(bytes - overhead) }));
      }),
    );
    for (const size of [32, 80]) {
      dashboardStore.getState().reset();
      const cache = dashboardStore.getState().changeSetFiles;
      for (let i = 0; i < size; i++) await cache.load(`/large/${i}`, (response) => response.json());
      expect(cache.retained).toEqual({ entries: 16, bytes: 16 * bytes, pending: 0 });
      expect(cache.peek('/large/0')).toBeUndefined();
      const reused = `/large/${size - 16}`;
      expect(cache.peek(reused)).toBeTruthy();
      for (let i = size; i < size + 15; i++)
        await cache.load(`/large/${i}`, (response) => response.json());
      expect(cache.peek(reused)).toBeTruthy();
      expect(cache.peek(`/large/${size - 15}`)).toBeUndefined();
      expect(cache.retained).toEqual({ entries: 16, bytes: 16 * bytes, pending: 0 });
    }
  });

  it('bounds retained bytes and keeps an oversized active result out of history', async () => {
    const cache = new ConditionalReads(64, 64);
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response(JSON.stringify({ n: 'x'.repeat(128) }))),
    );
    expect(await cache.load('/large', (response) => response.json())).toHaveProperty('n');
    expect(cache.peek('/large')).toBeUndefined();
    expect(cache.retained).toEqual({ entries: 0, bytes: 0, pending: 0 });
  });

  it('deduplicates in flight, bounds pending reads and rejects late aborted retention', async () => {
    const cache = new ConditionalReads(2);
    const completions: Array<(response: Response) => void> = [];
    vi.stubGlobal(
      'fetch',
      vi.fn(() => new Promise<Response>((resolve) => completions.push(resolve))),
    );
    const one = cache.load('/one', decode, 'same facts');
    const refusal = one.catch((cause: unknown) => cause);
    // One fetch serves both readers; an owner's wait is its own (L174-R1-01).
    const joined = cache.load('/one', decode, 'same facts');
    expect(joined).not.toBe(one);
    expect(cache.retained.pending).toBe(1);
    const two = cache.load('/two', decode);
    const three = cache.load('/three', decode);
    expect(cache.retained.pending).toBe(2);
    completions.forEach((resolve) => resolve(new Response('{"n":1}')));
    expect(await refusal).toHaveProperty('name', 'AbortError');
    await Promise.all([two, three]);
    expect(cache.peek('/one', 'same facts')).toBeUndefined();
    expect(cache.retained.pending).toBe(0);
  });

  it('does not serve retained success after a current failure or an unsolicited 304', async () => {
    const cache = new ConditionalReads(2);
    const fetch = vi
      .fn()
      .mockResolvedValueOnce(new Response('{"n":1}'))
      .mockRejectedValueOnce(new Error('offline'))
      .mockResolvedValueOnce(new Response(null, { status: 304 }));
    vi.stubGlobal('fetch', fetch);
    await cache.load('/one', decode);
    await expect(cache.load('/one', decode)).rejects.toThrow('offline');
    expect(cache.peek('/one')).toBeUndefined();
    await expect(cache.load('/one', decode)).rejects.toThrow('without retained bytes');
  });
});

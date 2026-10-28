import { act, cleanup, renderHook, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';

import { type MasterCounters, loadChangeSet, loadMasterCounters, peekMasterCounters } from './changeset';
import { dashboardStore } from './store';
import { useMasterCounters } from './useMasterCounters';

const generation = { codeBase: 'cb', codeTip: 'ct', memoryBase: 'mb', memoryTip: 'mt', digest: 'g' };
const counters = { code: { files: 2, insertions: 17, deletions: 3 }, memory: { files: 1, insertions: 5, deletions: 1 } };
const net = (master = 'M'): MasterCounters => ({ master, generation, currentness: 'current', counters, leaves: [] });
const leaves: MasterCounters['leaves'] = [{ leafId: 'L', state: 'working', counters }];
interface Pending { url: string; signal: AbortSignal | null | undefined; answer: (body: unknown, status?: number) => void }
function requests() {
  const pending: Pending[] = [];
  vi.stubGlobal('fetch', vi.fn((url: string, init?: RequestInit) => new Promise<Response>((resolve) => {
    pending.push({ url, signal: init?.signal, answer: (body, status = 200) => resolve(new Response(JSON.stringify(body), { status })) });
  })));
  return pending;
}
afterEach(() => { cleanup(); dashboardStore.getState().reset(); vi.unstubAllGlobals(); });

it('publishes net before attribution and pins the separate leaf read', async () => {
  const pending = requests();
  const view = renderHook(() => useMasterCounters({ repo: 'r', master: 'M' }));
  await waitFor(() => expect(pending).toHaveLength(1));
  expect(pending[0].url).toContain('/master/counters?');
  expect(pending[0].url).toContain('includeLeaves=false');
  await act(async () => pending[0].answer(net()));
  await waitFor(() => expect(view.result.current.data?.counters).toEqual(counters));
  expect(view.result.current.loading).toBe(false);
  expect(view.result.current.attribution.phase).toBe('loading');
  await waitFor(() => expect(pending).toHaveLength(2));
  const params = new URLSearchParams(pending[1].url.split('?')[1]);
  expect(params.get('includeLeaves')).toBe('true');
  for (const key of ['codeBase', 'codeTip', 'memoryBase', 'memoryTip'] as const)
    expect(params.get(key)).toBe(generation[key]);
  await act(async () => pending[1].answer({ ...net(), leaves }));
  await waitFor(() => expect(view.result.current.attribution).toEqual({ phase: 'ready', leaves }));
});

it('keeps a measured net when leaf attribution refuses', async () => {
  const pending = requests();
  const view = renderHook(() => useMasterCounters({ repo: 'r', master: 'M' }));
  await waitFor(() => expect(pending).toHaveLength(1));
  await act(async () => pending[0].answer(net()));
  await waitFor(() => expect(pending).toHaveLength(2));
  await act(async () => pending[1].answer({ status: 'not-found', path: 'leaf object missing' }, 404));
  await waitFor(() => expect(view.result.current.attribution.phase).toBe('failed'));
  expect(view.result.current.problem).toBeNull();
  expect(view.result.current.data?.counters).toEqual(counters);
});

it('aborts superseded attribution on wire and rejects its late answer', async () => {
  const pending = requests();
  const view = renderHook(({ master }) => useMasterCounters({ repo: 'r', master }), { initialProps: { master: 'M' } });
  await waitFor(() => expect(pending).toHaveLength(1));
  await act(async () => pending[0].answer(net()));
  await waitFor(() => expect(pending).toHaveLength(2));
  view.rerender({ master: 'N' });
  await waitFor(() => expect(pending[1].signal?.aborted).toBe(true));
  expect(view.result.current.data).toBeUndefined();
  await waitFor(() => expect(pending).toHaveLength(3));
  await act(async () => { pending[1].answer({ ...net(), leaves }); pending[2].answer(net('N')); });
  await waitFor(() => expect(view.result.current.data?.master).toBe('N'));
  expect(view.result.current.attribution.phase).toBe('loading');
});

it('rejects a mismatched attribution generation without replacing the net', async () => {
  const pending = requests();
  const view = renderHook(() => useMasterCounters({ repo: 'r', master: 'M' }));
  await waitFor(() => expect(pending).toHaveLength(1));
  await act(async () => pending[0].answer(net()));
  await waitFor(() => expect(pending).toHaveLength(2));
  await act(async () => pending[1].answer({ ...net(), generation: { ...generation, codeTip: 'other' }, leaves }));
  await waitFor(() => expect(view.result.current.attribution.phase).toBe('failed'));
  expect(view.result.current.data?.generation).toEqual(generation);
  expect(view.result.current.attribution.leaves).toEqual([]);
});

it('does not mistake an unmeasured range for pending or measured leaf rows', async () => {
  const pending = requests();
  const view = renderHook(() => useMasterCounters({ repo: 'r', master: 'M' }));
  await waitFor(() => expect(pending).toHaveLength(1));
  await act(async () => pending[0].answer({ ...net(), generation: null, currentness: 'unmeasured' }));
  await waitFor(() => expect(view.result.current.loading).toBe(false));
  expect(view.result.current.attribution).toEqual({ phase: 'idle', leaves: [] });
  expect(pending).toHaveLength(1);
});

it('keeps lean and full inspection cache keys distinct and preserves generic defaults', async () => {
  const pending = requests();
  const lean = loadMasterCounters('r', 'M', { includeLeaves: false });
  await waitFor(() => expect(pending).toHaveLength(1));
  await act(async () => pending[0].answer(net()));
  await lean;
  expect(peekMasterCounters('r', 'M', { includeLeaves: false })?.counters).toEqual(counters);
  expect(peekMasterCounters('r', 'M', { includeLeaves: true })).toBeUndefined();
  const full = loadChangeSet({ repo: 'r', master: 'M' });
  await waitFor(() => expect(pending).toHaveLength(2));
  expect(pending[1].url).toContain('/api/changeset/master?');
  expect(pending[1].url).toContain('includeLeaves=true');
  await act(async () => pending[1].answer({ ...net(), code: [], memory: [], leaves }));
  await full;
});

it('rejects a returned memory range when the chosen generation explicitly has none', async () => {
  const pending = requests();
  const view = renderHook(() => useMasterCounters({ repo: 'r', master: 'M', pins: { ...generation, memoryBase: '', memoryTip: '' } }));
  await waitFor(() => expect(pending).toHaveLength(1));
  await act(async () => pending[0].answer(net()));
  await waitFor(() => expect(view.result.current.problem?.code).toBe('master-counter-binding-mismatch'));
  expect(view.result.current.data).toBeUndefined();
  expect(pending).toHaveLength(1);
});

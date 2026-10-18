// Transport/lifetime consumer tests; native filesystem timing is proved separately in Python.
import { act, cleanup, render } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { useReviewMemoryWatch } from './reviewMemoryWatch';
import { connectState } from './stream';
import { enclosure, seedProjection } from '../panels/detail-panel/test-utils';

class Source {
  static instances: Source[] = [];
  listeners = new Map<string, (event: MessageEvent) => void>();
  closed = false;
  constructor(public url: string) { Source.instances.push(this); }
  addEventListener(name: string, callback: (event: MessageEvent) => void) { this.listeners.set(name, callback); }
  close() { this.closed = true; }
  emit(name: string, data: unknown = {}) { this.listeners.get(name)?.(new MessageEvent(name, { data: JSON.stringify(data) })); }
}
const target = { repo: 'agents-remember', master: 'watch', leaf: 'watched-leaf' };
const changed = vi.fn(), failed = vi.fn();
function Reader({ master = target.master, history }: { master?: string; history?: string }) {
  useReviewMemoryWatch({ ...target, master, history }, { refresh: vi.fn(), memoryChanged: changed, memoryWatchFailed: failed });
  return null;
}
let dispose: (() => void) | undefined;
afterEach(() => { cleanup(); dispose?.(); dispose = undefined; vi.unstubAllGlobals(); vi.clearAllMocks(); vi.useRealTimers(); });
function start() {
  seedProjection({ enclosures: ['watch', 'other'].map((master) => enclosure({ enclosure: `/contracts/${master}`, lifecycleId: master, repoName: target.repo, taskName: master, leafId: target.leaf, worktreeGroup: `/worktrees/${master}-ar` })), activeWorktreeGroups: ['watch-ar', 'other-ar'] });
  Source.instances = [];
  vi.stubGlobal('EventSource', Source);
  dispose = connectState();
}
const latest = () => Source.instances[Source.instances.length - 1];

it('owns one existing SSE scope only while a live review is mounted', () => {
  vi.useFakeTimers();
  start();
  const unscoped = latest();
  expect(unscoped.url).toBe('/api/stream');
  const view = render(<Reader />);
  const scoped = latest();
  expect(unscoped.closed).toBe(true);
  const url = new URL(scoped.url, 'http://localhost');
  expect(Object.fromEntries(url.searchParams)).toEqual({ reviewRepo: target.repo, reviewMaster: target.master, reviewLeaf: target.leaf });
  act(() => scoped.emit('open'));
  act(() => {
    scoped.emit('review.memory', { ...target, leaf: 'another-leaf', revision: 'unrelated-leaf:0' });
    scoped.emit('review.memory', { ...target, repo: 'another-repo', revision: 'unrelated-repo:0' });
  });
  expect(changed).not.toHaveBeenCalled();
  act(() => { scoped.emit('review.memory', { ...target, revision: 'initial' }); scoped.emit('review.memory', { ...target, revision: 'initial' }); });
  expect(changed).toHaveBeenCalledTimes(1); // one readiness reconciliation, duplicate stays quiet
  act(() => vi.advanceTimersByTime(60_000));
  expect(changed).toHaveBeenCalledTimes(1);
  expect(Source.instances).toHaveLength(2); // no idle polling or extra channel
  act(() => scoped.emit('review.memory', { ...target, revision: 'next' }));
  expect(changed).toHaveBeenCalledTimes(2);
  view.unmount();
  expect(scoped.closed).toBe(true);
  expect(latest().url).toBe('/api/stream');
  act(() => scoped.emit('review.memory', { ...target, revision: 'late' }));
  expect(changed).toHaveBeenCalledTimes(2);
});

it('reconciles after native EventSource reconnect without accepting an old connection', () => {
  start();
  const view = render(<Reader />);
  const old = latest();
  act(() => old.emit('review.memory', { ...target, revision: 'baseline' }));
  expect(changed).toHaveBeenCalledTimes(1);
  act(() => { old.emit('open'); old.emit('review.memory', { ...target, revision: 'baseline' }); });
  expect(changed).toHaveBeenCalledTimes(2);
  view.rerender(<Reader master="other" />);
  const current = latest();
  expect(old.closed).toBe(true);
  act(() => old.emit('review.memory', { ...target, revision: 'late' }));
  expect(changed).toHaveBeenCalledTimes(2);
  act(() => { current.emit('review.memory', { ...target, master: 'other', revision: 'first' }); current.emit('review.memory', { ...target, revision: 'wrong-master' }); });
  expect(changed).toHaveBeenCalledTimes(3);
  act(() => current.emit('review.memory', { ...target, master: 'other', revision: 'second' }));
  expect(changed).toHaveBeenCalledTimes(4);
  dispose?.();
  expect(current.closed).toBe(true);
  act(() => current.emit('review.memory', { ...target, master: 'other', revision: 'disconnected' }));
  expect(changed).toHaveBeenCalledTimes(4);
});

it('does not watch a recorded review or keep a watch after its live enclosure retires', () => {
  start();
  const view = render(<Reader history="recorded" />);
  expect(Source.instances).toHaveLength(1);
  view.rerender(<Reader />);
  const scoped = latest();
  expect(scoped.url).toContain('reviewLeaf=watched-leaf');
  view.rerender(<Reader history="recorded" />);
  expect(scoped.closed).toBe(true);
  act(() => scoped.emit('review.memory', { ...target, revision: 'late-readiness' }));
  expect(changed).not.toHaveBeenCalled();
  view.rerender(<Reader />);
  const reopened = latest();
  act(() => reopened.emit('review.memory', { ...target, revision: 'reopened-readiness' }));
  expect(changed).toHaveBeenCalledTimes(1);
  act(() => seedProjection({ enclosures: [], activeWorktreeGroups: [] }));
  expect(reopened.closed).toBe(true);
  expect(latest().url).toBe('/api/stream');
  act(() => reopened.emit('review.memory', { ...target, revision: 'retired' }));
  expect(changed).toHaveBeenCalledTimes(1);
});

it('reports a same-target watch failure, not an unrelated target or empty success', () => {
  start();
  render(<Reader />);
  act(() => latest().emit('review.memory.error', { ...target, master: 'other', detail: 'Ignore another master' }));
  expect(failed).not.toHaveBeenCalled();
  act(() => latest().emit('review.memory.error', { ...target, detail: 'Native watch unavailable: reopen this review.' }));
  expect(failed).toHaveBeenCalledWith('Native watch unavailable: reopen this review.');
  expect(changed).not.toHaveBeenCalled();
});

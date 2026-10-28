// Transport/lifetime consumer tests; native filesystem timing is proved separately in Python.
import { act, cleanup, render } from '@testing-library/react';
import { afterEach, expect, it, onTestFinished, vi } from 'vitest';
import { publishReviewPublication, subscribeReviewPublication, useReviewMemoryWatch } from './reviewMemoryWatch';
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

// D569 mean 1: the arming baseline names how long ago memory last changed (`changedAgoMs`, read by the
// server after arming). The HTTP answers were asked after the review opened, so a change older than
// that is already in them and renews nothing; a newer one -- an edit before arming, which raised no
// native notification -- renews them once. File stamps may round down, so the bound has slack.
let clock = 0;
function startClocked() {
  start();
  clock = 100_000;
  const now = vi.spyOn(performance, 'now').mockImplementation(() => clock);
  onTestFinished(() => now.mockRestore());
}

it('renews nothing on an arming baseline whose memory last changed before the review opened', () => {
  startClocked();
  render(<Reader />);
  clock += 400;
  act(() => { latest().emit('open'); latest().emit('review.memory', { ...target, revision: 'armed:0', changedAgoMs: 10 * 60_000 }); });
  expect(changed).not.toHaveBeenCalled();
  act(() => latest().emit('review.memory', { ...target, revision: 'armed:1' }));
  expect(changed).toHaveBeenCalledTimes(1); // an edit after arming is a notification, as before
});

it('renews once on an arming baseline whose memory changed after the review opened, or within the stamp slack before it', () => {
  startClocked();
  const view = render(<Reader />);
  clock += 400;
  act(() => { latest().emit('open'); latest().emit('review.memory', { ...target, revision: 'armed:0', changedAgoMs: 100 }); });
  expect(changed).toHaveBeenCalledTimes(1);
  act(() => latest().emit('review.memory', { ...target, revision: 'armed:0', changedAgoMs: 100 }));
  expect(changed).toHaveBeenCalledTimes(1); // a duplicate of the same connection stays quiet
  view.rerender(<Reader master="other" />);
  clock += 400;
  const slackMs = 1_000; // a stamp 1 s before the open may have been rounded down past it
  act(() => latest().emit('review.memory', { ...target, master: 'other', revision: 'other:0', changedAgoMs: 400 + slackMs }));
  expect(changed).toHaveBeenCalledTimes(2);
});

it('renews on a reconnect baseline only for a change after the last event the old connection delivered', () => {
  startClocked();
  render(<Reader />);
  const source = latest();
  act(() => source.emit('review.memory', { ...target, revision: 'armed:0', changedAgoMs: 10 * 60_000 }));
  expect(changed).not.toHaveBeenCalled();
  clock += 30_000; // the connection drops and the browser reconnects
  act(() => { source.emit('open'); source.emit('review.memory', { ...target, revision: 'again:0', changedAgoMs: 40_000 }); });
  expect(changed).not.toHaveBeenCalled(); // last changed before the old connection's last event
  clock += 30_000;
  act(() => { source.emit('open'); source.emit('review.memory', { ...target, revision: 'again:0', changedAgoMs: 5_000 }); });
  expect(changed).toHaveBeenCalledTimes(1); // changed while no watch was armed
});

// D569: a stream that reopens is sent a publication baseline whose revision is per connection while
// its page and environment stamps name the publication state. A baseline repeating the state the
// page already saw renews nothing; a changed stamp, or a server sending no stamps, still renews.
it('renews nothing for a reconnect whose baseline repeats the publication it saw', () => {
  const renewed = vi.fn();
  const scope = { repo: 'agents-remember', master: 'watch', leaf: 'stamped-leaf' };
  const unsubscribe = subscribeReviewPublication(scope, renewed, vi.fn());
  const baseline = { ...scope, kind: 'baseline', pageStamp: 'page-1', environmentStamp: 'environment-1' };
  publishReviewPublication({ ...baseline, revision: 'first:0' });
  expect(renewed).toHaveBeenCalledTimes(1);
  publishReviewPublication({ ...baseline, revision: 'second:0' });
  expect(renewed).toHaveBeenCalledTimes(1);
  publishReviewPublication({ ...baseline, revision: 'third:0', environmentStamp: 'environment-2' });
  expect(renewed).toHaveBeenCalledTimes(2);
  publishReviewPublication({ ...scope, kind: 'baseline', revision: 'fourth:0', pageStamp: 'page-1' });
  expect(renewed).toHaveBeenCalledTimes(3);
  unsubscribe();
});

import { beforeEach,describe,expect,it } from 'vitest';

import snapshot from '../fixtures/snapshot.json';
import { reparsed } from '../test/fixtures/wire';
import { asServedProjection } from '../test/servedProjection';
import type { CloseoutQueueNode,WorkspaceProjection } from '../types/projection';
import { dashboardStore } from './store';

// `asServedProjection`, not `snapshot as unknown as WorkspaceProjection`. The double cast turned
// off assignability for this file too: the fixture could drop a field the store reads and nothing
// here would notice, because the cast had already promised the value was a `WorkspaceProjection`.
// The helper's parameter type is the check — the cast inside it only puts back the literal types
// `resolveJsonModule` erased (see `test/servedProjection.ts`).
const projection = asServedProjection(snapshot);

const FIXTURE_TASKS = projection.tasks.length;
const FIRST_KEY = projection.tasks[0].key;
const SECOND_KEY = projection.tasks[1].key;

const RESET_QUEUE: CloseoutQueueNode = {
  sprintRef: { repository: 'repo-reset', path: 'scenario-a/task.json' },
  revision: 37,
  serviceCondition: 'valid-built',
  sourceClassification: 'active',
  sourceFingerprint: 'ab'.repeat(32),
  sourceProblems: [],
  members: [
    {
      generationId: 'cd'.repeat(32),
      taskDocumentRef: { repository: 'repo-reset', path: 'scenario-a/stale-leaf.json' },
      owningMaster: { repository: 'repo-reset', path: 'scenario-a/task.json' },
      classification: 'ready',
      priority: 'normal',
      order: 0,
      reasons: [],
    },
  ],
};

beforeEach(() => dashboardStore.getState().reset());

describe('dashboard store', () => {
  it('slides the event window so the buffer never grows without bound', () => {
    dashboardStore.setState({ events: [], eventsHydrated: false });
    const total = 2100;
    for (let i = 0; i < total; i += 1) {
      dashboardStore
        .getState()
        .pushEvent(JSON.stringify({ id: `e-${i}`, kind: 'tool.completed', data: {} }));
    }
    const { events, eventsHydrated } = dashboardStore.getState();
    expect(eventsHydrated).toBe(true);
    expect(events.length).toBe(2000); // bounded sliding window, not unbounded growth
    expect(events[events.length - 1].id).toBe('e-2099'); // newest retained
    expect(events[0].id).toBe('e-100'); // oldest beyond the window slid off
  });

  it('folds a snapshot into id-keyed maps and goes live', () => {
    dashboardStore.getState().applySnapshot(projection);
    const state = dashboardStore.getState();
    expect(state.conn).toBe('live');
    expect(Object.keys(state.tasks)).toContain(FIRST_KEY);
    expect(Object.keys(state.enclosures)).toContain('sim-enclosure');
    expect(Object.keys(state.tasks)).toHaveLength(FIXTURE_TASKS);
    expect(state.taskCounts).toEqual(projection.taskCounts);
  });

  it('upserts a task delta by qualified reference', () => {
    dashboardStore.getState().applySnapshot(projection);
    const one = dashboardStore.getState().tasks[FIRST_KEY];
    dashboardStore.getState().applyDelta('task', { ...one, stage: 'completed' });
    expect(dashboardStore.getState().tasks[FIRST_KEY].stage).toBe('completed');
  });

  it('drops a task on the removed marker', () => {
    dashboardStore.getState().applySnapshot(projection);
    dashboardStore.getState().applyDelta('task.removed', { key: SECOND_KEY });
    expect(dashboardStore.getState().tasks[SECOND_KEY]).toBeUndefined();
  });

  it('replaces metrics / analytics wholesale', () => {
    dashboardStore.getState().applySnapshot(projection);
    dashboardStore
      .getState()
      .applyDelta('metrics', { ...projection.metrics, ignoredHostAgents: 9999 });
    expect(dashboardStore.getState().metrics?.ignoredHostAgents).toBe(9999);
  });

  it('marks the connection signal-lost', () => {
    dashboardStore.getState().setConn('signal-lost');
    expect(dashboardStore.getState().conn).toBe('signal-lost');
  });

  it('clears a seeded closeout queue and increments generation exactly once on reset', () => {
    dashboardStore.setState({ closeoutQueues: [RESET_QUEUE] });
    const generationBeforeReset = dashboardStore.getState().gen;
    expect(dashboardStore.getState().closeoutQueues).toEqual([RESET_QUEUE]);

    dashboardStore.getState().reset();

    const resetState = dashboardStore.getState();
    expect(resetState.closeoutQueues).toEqual([]);
    expect(resetState.gen).toBe(generationBeforeReset + 1);
  });
});

// ── The change gate (260703-L15) ───────────────────────────────────────────────
// A payload whose only movement is its volatile ages (or a byte-fresh but content-equal
// reconnect snapshot) must cost NOTHING: no store write, no new object graph, no re-render.

/** A byte-fresh copy of the projection (like a real wire parse) with volatile ages bumped.
 *  `reparsed` is the typed round-trip: `JSON.parse` answers `any`, which would have let this
 *  helper hand the store a shape the server could never send. */
function volatileBump(source: WorkspaceProjection, tick: number): WorkspaceProjection {
  const copy = reparsed(source);
  copy.generatedAt = `2026-07-07T05:00:${String(tick % 60).padStart(2, '0')}+00:00`;
  for (const item of copy.analytics?.attentionQueue ?? []) item.waitSeconds = 5 + tick;
  return copy;
}

describe('dashboard store change gate (260703-L15)', () => {
  it('applies N idle re-snapshots with zero store writes and stable identity', () => {
    dashboardStore.getState().applySnapshot(volatileBump(projection, 0));
    const before = dashboardStore.getState();
    let notifications = 0;
    const unsubscribe = dashboardStore.subscribe(() => {
      notifications += 1;
    });
    for (let tick = 1; tick <= 50; tick += 1) {
      dashboardStore.getState().applySnapshot(volatileBump(projection, tick));
    }
    unsubscribe();
    const after = dashboardStore.getState();
    expect(notifications).toBe(0); // zero store writes — set() was never called
    expect(after).toBe(before); // the state OBJECT is untouched
    expect(after.tasks).toBe(before.tasks);
    expect(after.analytics).toBe(before.analytics);
    expect(after.generatedAt).toBe(before.generatedAt); // ages/stamp coherence: no new content, no new stamp
  });

  it('skips a content-equal reparsed task delta without a store write', () => {
    dashboardStore.getState().applySnapshot(projection);
    const before = dashboardStore.getState();
    const node = structuredClone(before.tasks[FIRST_KEY]);
    dashboardStore.getState().applyDelta('task', node);
    expect(dashboardStore.getState()).toBe(before);
    expect(dashboardStore.getState().tasks).toBe(before.tasks);
  });

  it('skips a removed-marker for an absent node without a store write', () => {
    dashboardStore.getState().applySnapshot(projection);
    const before = dashboardStore.getState();
    dashboardStore.getState().applyDelta('task.removed', { key: 'repo/no-such-task.json' });
    expect(dashboardStore.getState()).toBe(before);
  });

  it('still applies a real delta exactly as before', () => {
    dashboardStore.getState().applySnapshot(projection);
    const before = dashboardStore.getState();
    const node = structuredClone(before.tasks[FIRST_KEY]);
    node.eventAt = '2026-06-14T09:09:09+00:00';
    dashboardStore.getState().applyDelta('task', node);
    const after = dashboardStore.getState();
    expect(after).not.toBe(before);
    expect(after.tasks[FIRST_KEY].eventAt).toBe('2026-06-14T09:09:09+00:00');
  });

  it('reuses unchanged nodes by identity when a snapshot carries one real change', () => {
    dashboardStore.getState().applySnapshot(projection);
    const untouched = dashboardStore.getState().tasks[SECOND_KEY];
    const next = volatileBump(projection, 7);
    const changed = next.tasks.find((x) => x.key === FIRST_KEY);
    if (!changed) throw new Error('fixture lifecycle missing');
    changed.eventAt = '2026-06-14T09:09:09+00:00';
    dashboardStore.getState().applySnapshot(next);
    const state = dashboardStore.getState();
    expect(state.tasks[FIRST_KEY].eventAt).toBe('2026-06-14T09:09:09+00:00');
    expect(state.tasks[SECOND_KEY]).toBe(untouched); // identity preserved
  });

  it('carries the serving-build stamp and keeps its identity across snapshots', () => {
    const build = { version: '9.9.9', commit: 'abc1234', bootedAt: '2026-07-07T05:00:00Z' };
    dashboardStore.getState().applySnapshot({ ...projection, servingBuild: build });
    const first = dashboardStore.getState().servingBuild;
    expect(first).toEqual(build);
    const next = volatileBump({ ...projection, servingBuild: { ...build } }, 3);
    const changed = next.tasks[0];
    changed.eventAt = '2026-06-14T09:09:09+00:00'; // force a real apply
    dashboardStore.getState().applySnapshot(next);
    expect(dashboardStore.getState().servingBuild).toBe(first); // equal stamp -> same object
  });
});

// ── The long-session guard (260703-L15) ────────────────────────────────────────
// A working day of ticks must not grow the store: identical polls keep identity, the event
// window stays bounded, and no collection accumulates.

describe('long-session guard', () => {
  it('stays flat across 500 simulated idle ticks with event traffic', () => {
    dashboardStore.setState({ events: [], eventsHydrated: false });
    dashboardStore.getState().applySnapshot(volatileBump(projection, 0));
    const tasksRef = dashboardStore.getState().tasks;
    const analyticsRef = dashboardStore.getState().analytics;
    for (let tick = 1; tick <= 500; tick += 1) {
      dashboardStore.getState().applySnapshot(volatileBump(projection, tick));
      for (let i = 0; i < 5; i += 1) {
        dashboardStore
          .getState()
          .pushEvent(JSON.stringify({ id: `t${tick}-${i}`, kind: 'tool.completed', data: {} }));
      }
    }
    const state = dashboardStore.getState();
    expect(state.tasks).toBe(tasksRef); // no per-tick object churn
    expect(state.analytics).toBe(analyticsRef);
    expect(state.events.length).toBeLessThanOrEqual(2000); // the sliding window bound
    expect(Object.keys(state.tasks).length).toBe(FIXTURE_TASKS); // nothing accumulates
  });
});

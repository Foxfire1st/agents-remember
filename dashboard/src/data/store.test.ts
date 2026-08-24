import { beforeEach, describe, expect, it } from "vitest";

import type { CloseoutQueueNode, WorkspaceProjection } from "../types/projection";
import { asServedProjection } from "../test/servedProjection";
import { reparsed, agentNotifierHeartbeat } from "../test/fixtures/wire";
import { dashboardStore } from "./store";
import snapshot from "../fixtures/snapshot.json";

// `asServedProjection`, not `snapshot as unknown as WorkspaceProjection`. The double cast turned
// off assignability for this file too: the fixture could drop a field the store reads and nothing
// here would notice, because the cast had already promised the value was a `WorkspaceProjection`.
// The helper's parameter type is the check — the cast inside it only puts back the literal types
// `resolveJsonModule` erased (see `test/servedProjection.ts`).
const projection = asServedProjection(snapshot);

// The fixture's lifecycle count, read from the fixture rather than repeated as a literal in each
// assertion below. It grew from 2 to 6 when `contract.test.ts` started requiring every member of
// every closed vocabulary to be exercised — six states need six lifecycles — and a hard-coded 2
// is exactly the kind of hand-kept second copy this leaf is about.
const FIXTURE_LIFECYCLES = projection.lifecycles.length;

const RESET_QUEUE: CloseoutQueueNode = {
  sprintRef: { repository: "repo-reset", path: "scenario-a/task.json" },
  revision: 37,
  serviceCondition: "valid-built",
  sourceClassification: "active",
  sourceFingerprint: "ab".repeat(32),
  sourceProblems: [],
  members: [
    {
      generationId: "cd".repeat(32),
      taskDocumentRef: { repository: "repo-reset", path: "scenario-a/stale-leaf.json" },
      owningMaster: { repository: "repo-reset", path: "scenario-a/task.json" },
      classification: "ready",
      priority: "normal",
      order: 0,
      reasons: [],
    },
  ],
};

beforeEach(() => {
  dashboardStore.setState({
    conn: "connecting",
    generatedAt: null,
    lifecycles: {},
    enclosures: {},
    providers: {},
    metrics: null,
    analytics: null,
    servingBuild: null,
  });
});

describe("dashboard store", () => {
  it("slides the event window so the buffer never grows without bound", () => {
    dashboardStore.setState({ events: [], eventsHydrated: false });
    const total = 2100;
    for (let i = 0; i < total; i += 1) {
      dashboardStore
        .getState()
        .pushEvent(JSON.stringify({ id: `e-${i}`, kind: "tool.completed", data: {} }));
    }
    const { events, eventsHydrated } = dashboardStore.getState();
    expect(eventsHydrated).toBe(true);
    expect(events.length).toBe(2000); // bounded sliding window, not unbounded growth
    expect(events[events.length - 1].id).toBe("e-2099"); // newest retained
    expect(events[0].id).toBe("e-100"); // oldest beyond the window slid off
  });

  it("folds a snapshot into id-keyed maps and goes live", () => {
    dashboardStore.getState().applySnapshot(projection);
    const state = dashboardStore.getState();
    expect(state.conn).toBe("live");
    expect(Object.keys(state.lifecycles)).toContain("sim-replay-lifecycle");
    expect(Object.keys(state.enclosures)).toContain("sim-enclosure");
    expect(state.metrics?.lifecycleCount).toBe(FIXTURE_LIFECYCLES);
  });

  it("upserts a lifecycle delta by id", () => {
    dashboardStore.getState().applySnapshot(projection);
    const one = dashboardStore.getState().lifecycles["sim-replay-lifecycle"];
    dashboardStore.getState().applyDelta("lifecycle", { ...one, phase: "close" });
    expect(dashboardStore.getState().lifecycles["sim-replay-lifecycle"].phase).toBe("close");
  });

  it("drops a lifecycle on the removed marker", () => {
    dashboardStore.getState().applySnapshot(projection);
    dashboardStore.getState().applyDelta("lifecycle.removed", { id: "fleeting-001" });
    expect(dashboardStore.getState().lifecycles["fleeting-001"]).toBeUndefined();
  });

  it("replaces metrics / analytics wholesale", () => {
    dashboardStore.getState().applySnapshot(projection);
    dashboardStore.getState().applyDelta("metrics", { ...projection.metrics, totalTokens: 9999 });
    expect(dashboardStore.getState().metrics?.totalTokens).toBe(9999);
  });

  it("marks the connection signal-lost", () => {
    dashboardStore.getState().setConn("signal-lost");
    expect(dashboardStore.getState().conn).toBe("signal-lost");
  });

  it("clears a seeded closeout queue and increments generation exactly once on reset", () => {
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
  copy.generatedAt = `2026-07-07T05:00:${String(tick % 60).padStart(2, "0")}+00:00`;
  for (const lifecycle of copy.lifecycles) lifecycle.staleSeconds = 10 + tick;
  for (const doc of copy.analytics?.taskDocuments ?? []) doc.ageSeconds = 100 + tick;
  for (const item of copy.analytics?.attentionQueue ?? []) item.waitSeconds = 5 + tick;
  return copy;
}

describe("dashboard store change gate (260703-L15)", () => {
  it("applies N idle re-snapshots with zero store writes and stable identity", () => {
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
    expect(after.lifecycles).toBe(before.lifecycles);
    expect(after.analytics).toBe(before.analytics);
    expect(after.generatedAt).toBe(before.generatedAt); // ages/stamp coherence: no new content, no new stamp
  });

  it("applies an idle re-snapshot with an unchanged agentNotifierHeartbeat (incl. null/null) with zero store writes", () => {
    dashboardStore.getState().applySnapshot(projection); // agentNotifierHeartbeat stays null
    const before = dashboardStore.getState();
    expect(before.agentNotifierHeartbeat).toBeNull();
    let notifications = 0;
    const unsubscribe = dashboardStore.subscribe(() => {
      notifications += 1;
    });
    dashboardStore.getState().applySnapshot(volatileBump(projection, 1)); // still no agentNotifierHeartbeat
    unsubscribe();
    expect(notifications).toBe(0);
    expect(dashboardStore.getState()).toBe(before);
    expect(dashboardStore.getState().agentNotifierHeartbeat).toBeNull();
  });

  it("applies an idle re-snapshot with a genuinely changed agentNotifierHeartbeat", () => {
    const heartbeat = agentNotifierHeartbeat({ pendingInboxCount: 2, redeliverableInboxCount: 1 });
    const withHeartbeat: WorkspaceProjection = { ...projection, agentNotifierHeartbeat: heartbeat };
    dashboardStore.getState().applySnapshot(withHeartbeat);
    const before = dashboardStore.getState();
    let notifications = 0;
    const unsubscribe = dashboardStore.subscribe(() => {
      notifications += 1;
    });
    const advanced: WorkspaceProjection = {
      ...withHeartbeat,
      agentNotifierHeartbeat: { ...heartbeat, ageSeconds: 5 },
    };
    dashboardStore.getState().applySnapshot(advanced);
    unsubscribe();
    expect(notifications).toBe(1); // the tick advance IS a store write
    const after = dashboardStore.getState();
    expect(after).not.toBe(before);
    expect(after.agentNotifierHeartbeat).toEqual(advanced.agentNotifierHeartbeat);
    // everything else stays identity-stable — only agentNotifierHeartbeat rode through
    expect(after.lifecycles).toBe(before.lifecycles);
    expect(after.analytics).toBe(before.analytics);
    expect(after.generatedAt).toBe(before.generatedAt);
  });

  it("accepts the legacy supervisorHeartbeat wire key during the rename window", () => {
    // The served body carries both keys while the window is open; an older server may emit
    // only the legacy key. The store must read either.
    dashboardStore.getState().applySnapshot(projection); // both null first
    const heartbeat = agentNotifierHeartbeat({ pendingInboxCount: 2, redeliverableInboxCount: 1 });
    const legacyOnly: WorkspaceProjection = { ...projection, supervisorHeartbeat: heartbeat };
    dashboardStore.getState().applySnapshot(legacyOnly);
    expect(dashboardStore.getState().agentNotifierHeartbeat).toEqual(heartbeat);
  });

  it("skips a redundant delta (volatile-only node) without a store write", () => {
    dashboardStore.getState().applySnapshot(projection);
    const before = dashboardStore.getState();
    const node = JSON.parse(JSON.stringify(before.lifecycles["sim-replay-lifecycle"]));
    node.staleSeconds = 12345;
    dashboardStore.getState().applyDelta("lifecycle", node);
    expect(dashboardStore.getState()).toBe(before);
    expect(dashboardStore.getState().lifecycles).toBe(before.lifecycles);
  });

  it("skips a removed-marker for an absent node without a store write", () => {
    dashboardStore.getState().applySnapshot(projection);
    const before = dashboardStore.getState();
    dashboardStore.getState().applyDelta("lifecycle.removed", { id: "no-such-lifecycle" });
    expect(dashboardStore.getState()).toBe(before);
  });

  it("still applies a real delta exactly as before", () => {
    dashboardStore.getState().applySnapshot(projection);
    const before = dashboardStore.getState();
    const node = JSON.parse(JSON.stringify(before.lifecycles["sim-replay-lifecycle"]));
    node.tokens = 999999;
    node.staleSeconds = 1; // fresh ages ride along with real changes
    dashboardStore.getState().applyDelta("lifecycle", node);
    const after = dashboardStore.getState();
    expect(after).not.toBe(before);
    expect(after.lifecycles["sim-replay-lifecycle"].tokens).toBe(999999);
  });

  it("reuses unchanged nodes by identity when a snapshot carries one real change", () => {
    dashboardStore.getState().applySnapshot(projection);
    const untouched = dashboardStore.getState().lifecycles["fleeting-001"];
    const next = volatileBump(projection, 7);
    const changed = next.lifecycles.find((x) => x.id === "sim-replay-lifecycle");
    if (!changed) throw new Error("fixture lifecycle missing");
    changed.tokens = 424242;
    dashboardStore.getState().applySnapshot(next);
    const state = dashboardStore.getState();
    expect(state.lifecycles["sim-replay-lifecycle"].tokens).toBe(424242);
    expect(state.lifecycles["fleeting-001"]).toBe(untouched); // identity preserved
  });

  it("carries the serving-build stamp and keeps its identity across snapshots", () => {
    const build = { version: "9.9.9", commit: "abc1234", bootedAt: "2026-07-07T05:00:00Z" };
    dashboardStore.getState().applySnapshot({ ...projection, servingBuild: build });
    const first = dashboardStore.getState().servingBuild;
    expect(first).toEqual(build);
    const next = volatileBump({ ...projection, servingBuild: { ...build } }, 3);
    const changed = next.lifecycles[0];
    changed.tokens = 111; // force a real apply
    dashboardStore.getState().applySnapshot(next);
    expect(dashboardStore.getState().servingBuild).toBe(first); // equal stamp -> same object
  });
});

// ── The long-session guard (260703-L15) ────────────────────────────────────────
// A working day of ticks must not grow the store: identical polls keep identity, the event
// window stays bounded, and no collection accumulates.

describe("long-session guard", () => {
  it("stays flat across 500 simulated idle ticks with event traffic", () => {
    dashboardStore.setState({ events: [], eventsHydrated: false });
    dashboardStore.getState().applySnapshot(volatileBump(projection, 0));
    const lifecyclesRef = dashboardStore.getState().lifecycles;
    const analyticsRef = dashboardStore.getState().analytics;
    for (let tick = 1; tick <= 500; tick += 1) {
      dashboardStore.getState().applySnapshot(volatileBump(projection, tick));
      for (let i = 0; i < 5; i += 1) {
        dashboardStore
          .getState()
          .pushEvent(JSON.stringify({ id: `t${tick}-${i}`, kind: "tool.completed", data: {} }));
      }
    }
    const state = dashboardStore.getState();
    expect(state.lifecycles).toBe(lifecyclesRef); // no per-tick object churn
    expect(state.analytics).toBe(analyticsRef);
    expect(state.events.length).toBeLessThanOrEqual(2000); // the sliding window bound
    expect(Object.keys(state.lifecycles).length).toBe(FIXTURE_LIFECYCLES); // nothing accumulates
  });
});

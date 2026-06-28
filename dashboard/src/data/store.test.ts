import { beforeEach, describe, expect, it } from "vitest";

import type { WorkspaceProjection } from "../types/projection";
import { dashboardStore } from "./store";
import snapshot from "../fixtures/snapshot.json";

const projection = snapshot as unknown as WorkspaceProjection;

beforeEach(() => {
  dashboardStore.setState({
    conn: "connecting",
    generatedAt: null,
    lifecycles: {},
    enclosures: {},
    providers: {},
    metrics: null,
    analytics: null,
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
    expect(state.metrics?.lifecycleCount).toBe(2);
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
});

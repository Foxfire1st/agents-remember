import { beforeEach, describe, expect, it } from "vitest";

import { catalogRow } from "../test/fixtures/catalogRows";
import { sessionCockpitStore, startCockpitMirror } from "./sessionCockpitStore";
import { fromTerminalSessionInfo, sessionStore } from "./sessions";

// The cockpit store's honesty invariants (260715-FEUI-L2 R3): requested ≠ effective everywhere,
// queued never moves the effective marker, evidence starts at 'pending', per-kind pending sets.

const store = sessionCockpitStore;
const per = (id: string) => store.getState().perSession[id];

beforeEach(() => {
  store.setState({
    focusedSessionId: null,
    layout: { railCollapsed: false, inspectorCollapsed: false },
    paletteOpen: false,
    pollHealth: { lastBeatAt: null, missedBeats: 0, healthy: true },
    perSession: {},
  });
  sessionStore.getState().hydrate([]);
  window.localStorage.clear();
});

describe("perSession skeleton", () => {
  it("materializes the honest defaults on first touch — evidence tier 'pending', empty ledgers", () => {
    store.getState().setComposerDraft("s1", "hello");
    expect(per("s1")).toMatchObject({
      pendingSets: {},
      setLedger: [],
      launchEvidence: { tier: "pending" },
      composer: { draft: "hello", draftRevision: 1 },
      surfaceTab: "terminal",
      turnClock: { workingSince: null },
      freshness: { ptyWs: "none", lastOutputAt: null },
      queue: [],
    });
  });

  it("keeps pending sets PER KIND — a model set never clobbers an in-flight effort set", () => {
    store.getState().recordPendingSet("s1", "effort", {
      requestedValue: "xhigh",
      sentAt: 1,
      phase: "inflight",
    });
    store.getState().recordPendingSet("s1", "model", {
      requestedValue: "gpt-x",
      sentAt: 2,
      phase: "queued-awaiting-turn",
    });
    expect(per("s1").pendingSets.effort?.requestedValue).toBe("xhigh");
    expect(per("s1").pendingSets.model?.requestedValue).toBe("gpt-x");
    store.getState().clearPendingSet("s1", "model");
    expect(per("s1").pendingSets.model).toBeUndefined();
    expect(per("s1").pendingSets.effort?.requestedValue).toBe("xhigh");
  });
});

describe("set ledger + acknowledgment (F22)", () => {
  it("appends unacknowledged entries and acknowledges them explicitly", () => {
    store.getState().appendSetLedger("s1", {
      at: 10,
      kind: "model",
      requestedValue: "gpt-x",
      result: { acceptance: "queued", requestedValue: "gpt-x" },
    });
    expect(per("s1").setLedger).toEqual([
      expect.objectContaining({ acknowledged: false, requestedValue: "gpt-x" }),
    ]);
    store.getState().acknowledgeSetOutcomes("s1");
    expect(per("s1").setLedger[0].acknowledged).toBe(true);
  });

  it("QUEUED NEVER MOVES THE EFFECTIVE MARKER: ledger writes leave launchEvidence untouched", () => {
    store.getState().setLaunchEvidence("s1", { retainedModel: "gpt-old", tier: "readback" });
    store.getState().appendSetLedger("s1", {
      at: 10,
      kind: "model",
      requestedValue: "gpt-new",
      result: { acceptance: "queued", requestedValue: "gpt-new" },
    });
    // requested and effective stay separate fields — the marker moved nowhere.
    expect(per("s1").launchEvidence).toEqual({ retainedModel: "gpt-old", tier: "readback" });
    expect(per("s1").setLedger[0].result.effectiveValue).toBeUndefined();
  });
});

describe("client queue (F13 — a list, labeled 'yours')", () => {
  it("enqueues, supersedes the LAST live item (alt+↑ pop-back), and dequeues by requestId", () => {
    store.getState().enqueueSubmit("s1", { requestId: "r1", preview: "first", queuedAt: 1 });
    store.getState().enqueueSubmit("s1", { requestId: "r2", preview: "second", queuedAt: 2 });
    const popped = store.getState().supersedeLastQueued("s1");
    expect(popped?.requestId).toBe("r2");
    expect(per("s1").queue.map((item) => [item.requestId, item.superseded])).toEqual([
      ["r1", false],
      ["r2", true], // marked superseded, requestId never resent — not silently dropped
    ]);
    expect(store.getState().supersedeLastQueued("s1")?.requestId).toBe("r1");
    expect(store.getState().supersedeLastQueued("s1")).toBeNull();
    store.getState().dequeueSubmit("s1", "r2");
    expect(per("s1").queue.map((item) => item.requestId)).toEqual(["r1"]);
  });
});

describe("freshness + poll health (R15)", () => {
  it("tracks per-pane ws state and last output", () => {
    store.getState().setPtyWs("s1", "connected");
    store.getState().recordPtyOutput("s1", 1234);
    expect(per("s1").freshness).toEqual({ ptyWs: "connected", lastOutputAt: 1234 });
  });

  it("poll beats: three misses flip healthy, one success restores it", () => {
    store.getState().recordPollBeat(false);
    store.getState().recordPollBeat(false);
    expect(store.getState().pollHealth.healthy).toBe(true);
    store.getState().recordPollBeat(false);
    expect(store.getState().pollHealth).toMatchObject({ missedBeats: 3, healthy: false });
    store.getState().recordPollBeat(true);
    expect(store.getState().pollHealth).toMatchObject({ missedBeats: 0, healthy: true });
  });
});

describe("turn clock (client-measured, ~-labeled)", () => {
  it("starts on an observed transition INTO working and clears on leaving it", () => {
    store.getState().recordTurnObservation("s1", "working", 100);
    expect(per("s1").turnClock).toEqual({ workingSince: 100, lastObservedTurnState: "working" });
    // Same state again: no restart (the clock anchors the FIRST observation).
    store.getState().recordTurnObservation("s1", "working", 200);
    expect(per("s1").turnClock.workingSince).toBe(100);
    store.getState().recordTurnObservation("s1", "turn-ended", 300);
    expect(per("s1").turnClock).toEqual({ workingSince: null, lastObservedTurnState: "turn-ended" });
  });

  it("mirrors the session registry via startCockpitMirror", () => {
    const release = startCockpitMirror();
    sessionStore
      .getState()
      .hydrate([fromTerminalSessionInfo(catalogRow({ id: "m1", turnState: "working" }))]);
    expect(per("m1").turnClock.workingSince).not.toBeNull();
    release();
  });
});

describe("orchestration-tree toggle (decision: per-user localStorage persistence)", () => {
  it("persists the toggle", () => {
    store.getState().setOrchestrationTreeView(true);
    expect(window.localStorage.getItem("cockpit.sessions.orchestration-tree")).toBe("true");
    store.getState().setOrchestrationTreeView(false);
    expect(window.localStorage.getItem("cockpit.sessions.orchestration-tree")).toBe("false");
  });
});

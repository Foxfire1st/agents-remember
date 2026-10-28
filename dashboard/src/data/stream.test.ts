import { afterEach, describe, expect, it, vi } from "vitest";

import { subscribeReviewExplainerMoved } from "./reviewMemoryWatch";
import type { ReviewExplainerMovedEvent } from "./reviewMemoryWatch";
import { dashboardStore } from "./store";
import {
  STATE_STREAM_OPEN_DEADLINE_MS,
  STREAM_REOPEN_FIRST_MS,
  STREAM_REOPEN_LAST_MS,
  connectEvents,
  connectState,
} from "./stream";

// connectState and the sleep/wake half-open-corpse class
// (data/streamLiveness.ts): after an OS sleep the open EventSource never fires `error`, so the
// native auto-reconnect never engages and the cockpit would render a live-LOOKING but frozen
// projection forever. The watchdog cycles a judged corpse to a fresh subscribe — quietly: the
// re-snapshot rides the identity-preserving merge and conn never leaves "live".
class ControlledSource {
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSED = 2;
  static instances: ControlledSource[] = [];
  listeners: Record<string, Array<(event: unknown) => void>> = {};
  closed = false;
  readyState = 0;
  constructor(public url: string) {
    ControlledSource.instances.push(this);
  }
  addEventListener(type: string, fn: (event: unknown) => void): void {
    (this.listeners[type] ??= []).push(fn);
  }
  close(): void {
    this.closed = true;
  }
  fire(type: string, event?: unknown): void {
    for (const fn of this.listeners[type] ?? []) fn(event ?? {});
  }
}

afterEach(() => {
  dashboardStore.setState({ conn: "connecting" });
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  vi.useRealTimers();
});

describe("connectState liveness (260723 sleep/wake)", () => {
  it("cycles a wake-judged corpse to a fresh subscribe without flashing SIGNAL LOST", () => {
    vi.useFakeTimers();
    vi.stubGlobal("EventSource", ControlledSource);
    ControlledSource.instances = [];
    const dispose = connectState();
    ControlledSource.instances[0].fire("open");
    expect(dashboardStore.getState().conn).toBe("live");

    // Ordinary ticking alone never cycles a quiet (idle-projection) channel.
    vi.advanceTimersByTime(20_000);
    expect(ControlledSource.instances).toHaveLength(1);

    // OS sleep: the wall clock jumps an hour without a tick; the first post-wake tick judges.
    vi.setSystemTime(Date.now() + 3_600_000);
    vi.advanceTimersByTime(5_000);
    expect(ControlledSource.instances[0].closed).toBe(true); // the corpse is closed
    expect(ControlledSource.instances).toHaveLength(2); // fresh subscribe
    expect(ControlledSource.instances[1].url).toBe("/api/stream");
    expect(dashboardStore.getState().conn).toBe("live"); // quiet: never "signal-lost"

    ControlledSource.instances[1].fire("open");
    expect(dashboardStore.getState().conn).toBe("live");
    dispose();
  });

  // D569: the server names a quiet stream alive every 15 s, because its transport keepalive is a
  // comment frame an EventSource never surfaces. Before, a healthy quiet stream was judged dead by
  // the 90 s backstop and reopened; with the named event only a silent transport is cycled.
  it("never cycles a quiet stream that names itself alive, and still cycles a silent one", () => {
    vi.useFakeTimers();
    vi.stubGlobal("EventSource", ControlledSource);
    ControlledSource.instances = [];
    const dispose = connectState();
    ControlledSource.instances[0].fire("open");
    for (let at = 0; at < 300_000; at += 15_000) {
      vi.advanceTimersByTime(15_000);
      ControlledSource.instances[0].fire("alive");
    }
    expect(ControlledSource.instances).toHaveLength(1);
    expect(ControlledSource.instances[0].closed).toBe(false);

    vi.advanceTimersByTime(100_000); // the transport goes silent: no alive, no frame
    expect(ControlledSource.instances[0].closed).toBe(true);
    expect(ControlledSource.instances).toHaveLength(2);
    dispose();
  });

  it("never reconnect-storms a hidden tab", () => {
    vi.useFakeTimers();
    vi.stubGlobal("EventSource", ControlledSource);
    ControlledSource.instances = [];
    const dispose = connectState();
    ControlledSource.instances[0].fire("open");

    vi.spyOn(document, "hidden", "get").mockReturnValue(true);
    vi.advanceTimersByTime(300_000);
    expect(ControlledSource.instances).toHaveLength(1);
    expect(ControlledSource.instances[0].closed).toBe(false);
    dispose();
  });

  it("an ordinary drop still reports SIGNAL LOST (the native auto-reconnect owns it)", () => {
    vi.useFakeTimers();
    vi.stubGlobal("EventSource", ControlledSource);
    ControlledSource.instances = [];
    const dispose = connectState();
    ControlledSource.instances[0].fire("open");
    ControlledSource.instances[0].fire("error");
    expect(dashboardStore.getState().conn).toBe("signal-lost");
    dispose();
  });

  // The watchdog cycles a wake-judged corpse QUIETLY (no phase write), holding
  // conn at "live". If that fresh subscribe is itself a half-open corpse it fires NEITHER `open`
  // NOR `error` (a CONNECTING EventSource cannot surface the failure) and the watchdog no-ops on a
  // non-open channel — so before the open-deadline conn sat at a fabricated "live" forever: a lit
  // cockpit LIVE badge with nothing connected. The deadline drops conn to the honest "signal-lost"
  // and re-enters the open cycle.
  it("drops a never-opening cycled subscribe off 'live' — no fabricated LIVE badge (M4)", () => {
    vi.useFakeTimers();
    vi.stubGlobal("EventSource", ControlledSource);
    ControlledSource.instances = [];
    const dispose = connectState();
    ControlledSource.instances[0].fire("open");
    expect(dashboardStore.getState().conn).toBe("live");

    // OS sleep wedges the live channel; the first post-wake tick quietly cycles it to a fresh
    // subscribe — conn stays "live" through the cycle itself (the QUIET-cycle property).
    vi.setSystemTime(Date.now() + 3_600_000);
    vi.advanceTimersByTime(5_000);
    expect(ControlledSource.instances).toHaveLength(2);
    expect(dashboardStore.getState().conn).toBe("live");

    // The cycled subscribe NEVER fires `open` or `error`. WITHOUT the open-deadline nothing else
    // fires (the watchdog no-ops on a non-open channel) and conn stays a fabricated "live"; WITH it
    // the deadline drops conn to "signal-lost" and re-enters the open cycle for a fresh attempt.
    vi.advanceTimersByTime(STATE_STREAM_OPEN_DEADLINE_MS);
    expect(dashboardStore.getState().conn).toBe("signal-lost");
    expect(ControlledSource.instances).toHaveLength(3); // re-attempted a fresh subscribe

    // The re-attempt can still recover honestly: a fresh instance that opens restores "live".
    ControlledSource.instances[2].fire("open");
    expect(dashboardStore.getState().conn).toBe("live");
    dispose();
  });
});

describe("connectState moved notice (review.explainer.moved)", () => {
  const scope = { repo: "agents-remember", master: "m183", leaf: "l183" };
  const movedPayload = {
    ...scope,
    kind: "comparison-moved",
    from: 7,
    to: 8,
    blocksChanged: 3,
    updateOffered: true,
    text: "task moved: #7 -> #8, 3 blocks changed",
  };

  it("delivers the named SSE event to a mounted subscriber without a page reopen", () => {
    vi.stubGlobal("EventSource", ControlledSource);
    ControlledSource.instances = [];
    const received: ReviewExplainerMovedEvent[] = [];
    const unsubscribe = subscribeReviewExplainerMoved(scope, (event) => {
      received.push(event);
    });
    const dispose = connectState();

    ControlledSource.instances[0].fire("review.explainer.moved", {
      data: JSON.stringify(movedPayload),
    });
    expect(received).toEqual([movedPayload]);

    // A sibling leaf's notice never reaches this subscriber.
    ControlledSource.instances[0].fire("review.explainer.moved", {
      data: JSON.stringify({ ...movedPayload, leaf: "l999" }),
    });
    expect(received).toHaveLength(1);

    unsubscribe();
    dispose();
  });

  it("carries the no-work move and the cleared notice to the same subscriber", () => {
    vi.stubGlobal("EventSource", ControlledSource);
    ControlledSource.instances = [];
    const received: ReviewExplainerMovedEvent[] = [];
    const unsubscribe = subscribeReviewExplainerMoved(scope, (event) => {
      received.push(event);
    });
    const dispose = connectState();

    // A code-only move on a leaf with no explainer work: the page renews its live
    // deterministic view from this event, and nothing wakes an agent.
    const noWork = {
      ...scope,
      kind: "comparison-moved",
      from: 7,
      to: 8,
      blocksChanged: 2,
      updateOffered: false,
      hasExplainer: false,
      text: "task moved: #7 -> #8, 2 blocks changed",
    };
    ControlledSource.instances[0].fire("review.explainer.moved", {
      data: JSON.stringify(noWork),
    });
    expect(received).toEqual([noWork]);

    // After Update/Retire the channel publishes the cleared notice; the page drops the mark.
    const cleared = {
      ...scope,
      kind: "current",
      from: null,
      to: null,
      blocksChanged: 0,
      updateOffered: false,
      cleared: true,
      moved: null,
      text: "current",
    };
    ControlledSource.instances[0].fire("review.explainer.moved", {
      data: JSON.stringify(cleared),
    });
    expect(received[1]).toEqual(cleared);

    unsubscribe();
    dispose();
  });
});

describe("connectEvents ready marker (MIK-R114 rule 8)", () => {
  it("hands the ready marker's last feed to the opener", () => {
    vi.stubGlobal("EventSource", ControlledSource);
    ControlledSource.instances = [];
    const seen: Array<{ lastEventTs?: string } | undefined> = [];
    const dispose = connectEvents(
      () => {},
      "",
      (ready) => {
        seen.push(ready);
      },
    );
    ControlledSource.instances[0].fire("ready", {
      data: JSON.stringify({ ready: true, lastEventTs: "2026-06-23T09:00:00+00:00" }),
    });
    expect(seen).toEqual([{ lastEventTs: "2026-06-23T09:00:00+00:00" }]);
    dispose();
  });

  it("still opens the gate on an unreadable ready marker", () => {
    vi.stubGlobal("EventSource", ControlledSource);
    ControlledSource.instances = [];
    const seen: Array<{ lastEventTs?: string } | undefined> = [];
    const dispose = connectEvents(
      () => {},
      "",
      (ready) => {
        seen.push(ready);
      },
    );
    ControlledSource.instances[0].fire("ready", { data: "not json" });
    expect(seen).toEqual([undefined]);
    dispose();
  });
});

// The browser gives up on a stream answered with an error status -- a server shutting down answers
// one -- and a closed EventSource never retries. The page reopens it itself, with a doubling wait,
// and the badge says SIGNAL LOST until a stream opens again.
describe("a stream the browser gave up on is reopened by the page", () => {
  const closedBy = (source: ControlledSource) => {
    source.readyState = ControlledSource.CLOSED;
    source.fire("error");
  };

  it("reopens the state stream after an error status, waiting longer each time, until one opens", () => {
    vi.useFakeTimers();
    vi.stubGlobal("EventSource", ControlledSource);
    ControlledSource.instances = [];
    const dispose = connectState();
    ControlledSource.instances[0].fire("open");
    closedBy(ControlledSource.instances[0]);
    expect(dashboardStore.getState().conn).toBe("signal-lost");
    const waits: number[] = [];
    for (let attempt = 1; attempt <= 6; attempt++) {
      const before = ControlledSource.instances.length;
      let waited = 0;
      while (ControlledSource.instances.length === before && waited < 4 * STREAM_REOPEN_LAST_MS) {
        vi.advanceTimersByTime(250);
        waited += 250;
      }
      waits.push(waited);
      expect(dashboardStore.getState().conn).toBe("signal-lost");
      closedBy(ControlledSource.instances.at(-1)!);
    }
    expect(waits).toEqual([1, 2, 4, 8, 15, 15].map((s) => s * 1000));
    expect(waits[0]).toBe(STREAM_REOPEN_FIRST_MS);
    expect(Math.max(...waits)).toBe(STREAM_REOPEN_LAST_MS);

    vi.advanceTimersByTime(STREAM_REOPEN_LAST_MS);
    const live = ControlledSource.instances.at(-1)!;
    live.fire("open");
    expect(dashboardStore.getState().conn).toBe("live");
    // An open starts the waits again: the next failure is reopened after the first wait.
    closedBy(live);
    vi.advanceTimersByTime(STREAM_REOPEN_FIRST_MS);
    expect(ControlledSource.instances.at(-1)).not.toBe(live);
    dispose();
  });

  it("leaves a stream the browser still retries to the browser", () => {
    vi.useFakeTimers();
    vi.stubGlobal("EventSource", ControlledSource);
    ControlledSource.instances = [];
    const dispose = connectState();
    ControlledSource.instances[0].fire("open");
    ControlledSource.instances[0].readyState = ControlledSource.CONNECTING;
    ControlledSource.instances[0].fire("error"); // a dropped connection: the native retry owns it
    vi.advanceTimersByTime(STREAM_REOPEN_LAST_MS);
    expect(ControlledSource.instances).toHaveLength(1);
    dispose();
  });

  it("reopens the river from the last event it had, and a disposed one stays closed", () => {
    vi.useFakeTimers();
    vi.stubGlobal("EventSource", ControlledSource);
    ControlledSource.instances = [];
    const lines: string[] = [];
    const dispose = connectEvents((line) => lines.push(line));
    const first = ControlledSource.instances[0];
    first.fire("event", { data: "one", lastEventId: "cursor-1" });
    closedBy(first);
    vi.advanceTimersByTime(STREAM_REOPEN_FIRST_MS);
    expect(ControlledSource.instances).toHaveLength(2);
    expect(ControlledSource.instances[1].url).toBe("/api/events?lastEventId=cursor-1");
    ControlledSource.instances[1].fire("event", { data: "two", lastEventId: "cursor-2" });
    expect(lines).toEqual(["one", "two"]);
    dispose();
    closedBy(ControlledSource.instances[1]);
    vi.advanceTimersByTime(STREAM_REOPEN_LAST_MS);
    expect(ControlledSource.instances).toHaveLength(2);
  });
});

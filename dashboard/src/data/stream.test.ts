import { afterEach, describe, expect, it, vi } from "vitest";

import { dashboardStore } from "./store";
import { STATE_STREAM_OPEN_DEADLINE_MS, connectState } from "./stream";

// connectState shares the sleep/wake half-open-corpse class with the conversation stream
// (data/streamLiveness.ts): after an OS sleep the open EventSource never fires `error`, so the
// native auto-reconnect never engages and the cockpit would render a live-LOOKING but frozen
// projection forever. The watchdog cycles a judged corpse to a fresh subscribe — quietly: the
// re-snapshot rides the identity-preserving merge and conn never leaves "live".
class ControlledSource {
  static instances: ControlledSource[] = [];
  listeners: Record<string, Array<(event: unknown) => void>> = {};
  closed = false;
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
  // cockpit LIVE badge with nothing connected, the sibling of the conversation stream's silent dead
  // chat. The deadline drops conn to the honest "signal-lost" and re-enters the open cycle.
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

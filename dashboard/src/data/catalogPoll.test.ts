import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { catalogRow } from "../test/fixtures/catalogRows";
import {
  CATALOG_REFRESH_INTERVAL_MS,
  hydrateTerminalSessionsFromCatalog,
  startCatalogPollDriver,
  startCatalogReconciler,
} from "./catalogPoll";
import { sessionCockpitStore } from "./sessionCockpitStore";
import { sessionStore } from "./sessions";
import { TERMINAL_CATALOG_REQUEST_TIMEOUT_MS } from "./terminal";

// The hoisted shared poll driver: one self-scheduled request regardless of subscriber count,
// poll-health beats recorded once per physical attempt rather than once per logical waiter.

const okResponse = (sessions: unknown[]) =>
  ({ ok: true, json: async () => ({ sessions }) }) as Response;

class FakeBroadcastChannel {
  static instances: FakeBroadcastChannel[] = [];
  onmessage: ((event: MessageEvent) => void) | null = null;
  closed = false;

  constructor(public name: string) {
    FakeBroadcastChannel.instances.push(this);
  }

  postMessage(): void {}

  close(): void {
    this.closed = true;
  }

  static dispatch(data: unknown): void {
    for (const instance of FakeBroadcastChannel.instances) {
      if (!instance.closed) instance.onmessage?.({ data } as MessageEvent);
    }
  }

  static reset(): void {
    FakeBroadcastChannel.instances = [];
  }
}

beforeEach(() => {
  sessionStore.getState().hydrate([]);
  // reset poll health
  sessionCockpitStore.setState({ pollHealth: { lastBeatAt: null, missedBeats: 0, healthy: true } });
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
  FakeBroadcastChannel.reset();
});

describe("hydrateTerminalSessionsFromCatalog", () => {
  it("hydrates the session store from the catalog and records a healthy beat", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(okResponse([catalogRow({ id: "row-1" })])));
    await hydrateTerminalSessionsFromCatalog(false);
    expect(sessionStore.getState().sessions.map((session) => session.id)).toEqual(["row-1"]);
    expect(sessionCockpitStore.getState().pollHealth).toMatchObject({ missedBeats: 0, healthy: true });
  });

  it("counts missed beats on failure and flips healthy=false at the stale cutoff", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("down")));
    await hydrateTerminalSessionsFromCatalog(false);
    await hydrateTerminalSessionsFromCatalog(false);
    expect(sessionCockpitStore.getState().pollHealth).toMatchObject({ missedBeats: 2, healthy: true });
    await hydrateTerminalSessionsFromCatalog(false);
    expect(sessionCockpitStore.getState().pollHealth).toMatchObject({ missedBeats: 3, healthy: false });
    // Recovery resets the counter.
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(okResponse([])));
    await hydrateTerminalSessionsFromCatalog(true);
    expect(sessionCockpitStore.getState().pollHealth).toMatchObject({ missedBeats: 0, healthy: true });
  });

  it("records one missed beat when four logical hydrators share one failed physical request", async () => {
    let release: ((response: Response) => void) | undefined;
    const pending = new Promise<Response>((resolve) => {
      release = resolve;
    });
    const fetchMock = vi.fn().mockReturnValue(pending);
    vi.stubGlobal("fetch", fetchMock);

    const hydrators = Array.from({ length: 4 }, () => hydrateTerminalSessionsFromCatalog(false));
    expect(fetchMock).toHaveBeenCalledTimes(1);
    release?.({ ok: false, status: 503, json: async () => ({}) } as Response);
    await Promise.all(hydrators);

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(sessionCockpitStore.getState().pollHealth).toMatchObject({
      missedBeats: 1,
      healthy: true,
    });
  });

  it("a hung catalog socket expires as ONE honestly-recorded missed beat; the poll loop survives (260721 live wedge)", async () => {
    vi.useFakeTimers();
    try {
      const fetchMock = vi
        .fn()
        // A half-dead socket: never settles until the fetchWithTimeout bound aborts it.
        .mockImplementationOnce(
          (_url: string, init?: RequestInit) =>
            new Promise((_resolve, reject) => {
              init?.signal?.addEventListener("abort", () =>
                reject(new DOMException("The operation was aborted.", "AbortError")),
              );
            }),
        )
        .mockResolvedValueOnce(okResponse([catalogRow({ id: "row-1" })]));
      vi.stubGlobal("fetch", fetchMock);

      const hungBeat = hydrateTerminalSessionsFromCatalog(false);
      await vi.advanceTimersByTimeAsync(TERMINAL_CATALOG_REQUEST_TIMEOUT_MS);
      await hungBeat;
      // The timed-out beat counts as exactly ONE missed beat — honestly recorded, not frozen.
      expect(sessionCockpitStore.getState().pollHealth).toMatchObject({ missedBeats: 1, healthy: true });

      // The loop is alive: the next beat fires on a FRESH socket (the single-flight slot was
      // released), hydrates rows, and resets the missed counter.
      await hydrateTerminalSessionsFromCatalog(false);
      expect(fetchMock).toHaveBeenCalledTimes(2);
      expect(sessionStore.getState().sessions.map((session) => session.id)).toEqual(["row-1"]);
      expect(sessionCockpitStore.getState().pollHealth).toMatchObject({ missedBeats: 0, healthy: true });
    } finally {
      vi.useRealTimers();
    }
  });

  it("keeps the empty-list guard: an empty catalog only applies when allowed", async () => {
    sessionStore.getState().add("Chat", "existing");
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(okResponse([])));
    await hydrateTerminalSessionsFromCatalog(false);
    expect(sessionStore.getState().sessions).toHaveLength(1);
    await hydrateTerminalSessionsFromCatalog(true);
    expect(sessionStore.getState().sessions).toHaveLength(0);
  });

  it("excludes just-terminated ids so a stale snapshot cannot resurrect them", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(okResponse([catalogRow({ id: "dead" }), catalogRow({ id: "alive" })])),
    );
    await hydrateTerminalSessionsFromCatalog(true, new Set(["dead"]));
    expect(sessionStore.getState().sessions.map((session) => session.id)).toEqual(["alive"]);
  });

  it("shares termination exclusions across every waiter on one physical snapshot", async () => {
    let release: ((response: Response) => void) | undefined;
    const pending = new Promise<Response>((resolve) => {
      release = resolve;
    });
    const fetchMock = vi.fn().mockReturnValue(pending);
    vi.stubGlobal("fetch", fetchMock);

    // The termination confirmer attaches first; a normal poll joins second. Without a shared
    // exclusion union, the later continuation re-applies the same stale `dead` row.
    const excluding = hydrateTerminalSessionsFromCatalog(true, new Set(["dead"]));
    const ordinary = hydrateTerminalSessionsFromCatalog(true);
    release?.(
      okResponse([catalogRow({ id: "dead" }), catalogRow({ id: "alive" })]),
    );
    await Promise.all([excluding, ordinary]);

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(sessionStore.getState().sessions.map((session) => session.id)).toEqual(["alive"]);
  });

  it("does zero store work on an unchanged payload, reconciles a changed one (260721 F2)", async () => {
    let rows = [catalogRow({ id: "row-1" }), catalogRow({ id: "row-2" })];
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation(() => Promise.resolve(okResponse(rows))),
    );
    await hydrateTerminalSessionsFromCatalog(false);
    const settled = sessionStore.getState();
    const listener = vi.fn();
    const unsubscribe = sessionStore.subscribe(listener);
    try {
      // A byte-identical next beat: the store is not touched (no notification, same references —
      // the always-mounted hidden SessionsView renders nothing), but the health beat still lands.
      await hydrateTerminalSessionsFromCatalog(false);
      expect(listener).not.toHaveBeenCalled();
      expect(sessionStore.getState()).toBe(settled);
      expect(sessionCockpitStore.getState().pollHealth.lastBeatAt).not.toBeNull();

      // A changed payload reconciles: the changed row is replaced, the unchanged row keeps
      // object identity so memoized downstream surfaces skip it.
      rows = [catalogRow({ id: "row-1" }), catalogRow({ id: "row-2", turnState: "turn-ended" })];
      await hydrateTerminalSessionsFromCatalog(false);
      expect(listener).toHaveBeenCalledTimes(1);
      const next = sessionStore.getState();
      expect(next.sessions[0]).toBe(settled.sessions[0]);
      expect(next.sessions[1]).not.toBe(settled.sessions[1]);
      expect(next.sessions[1]?.turnState).toBe("turn-ended");
    } finally {
      unsubscribe();
    }
  });
});

describe("startCatalogPollDriver (refcounted)", () => {
  it("runs ONE 2500 ms self-scheduled loop for any number of subscribers and stops with the last release", async () => {
    vi.useFakeTimers();
    const fetchMock = vi.fn().mockResolvedValue(okResponse([catalogRow({ id: "tick" })]));
    vi.stubGlobal("fetch", fetchMock);

    const releaseA = startCatalogPollDriver();
    const releaseB = startCatalogPollDriver();
    await vi.advanceTimersByTimeAsync(CATALOG_REFRESH_INTERVAL_MS);
    expect(fetchMock).toHaveBeenCalledTimes(1); // shared driver, never a second timer

    releaseA();
    releaseA(); // double-release is inert
    await vi.advanceTimersByTimeAsync(CATALOG_REFRESH_INTERVAL_MS);
    expect(fetchMock).toHaveBeenCalledTimes(2); // B still holds the driver

    releaseB();
    await vi.advanceTimersByTimeAsync(CATALOG_REFRESH_INTERVAL_MS * 3);
    expect(fetchMock).toHaveBeenCalledTimes(2); // stopped with the last release
  });

  it("does not stack interval callers behind a hung 10 s request", async () => {
    vi.useFakeTimers();
    const fetchMock = vi
      .fn()
      .mockImplementationOnce(
        (_url: string, init?: RequestInit) =>
          new Promise((_resolve, reject) => {
            init?.signal?.addEventListener("abort", () =>
              reject(new DOMException("The operation was aborted.", "AbortError")),
            );
          }),
      )
      .mockResolvedValueOnce(okResponse([catalogRow({ id: "recovered" })]));
    vi.stubGlobal("fetch", fetchMock);

    const release = startCatalogPollDriver();
    try {
      await vi.advanceTimersByTimeAsync(CATALOG_REFRESH_INTERVAL_MS);
      expect(fetchMock).toHaveBeenCalledTimes(1);
      await vi.advanceTimersByTimeAsync(CATALOG_REFRESH_INTERVAL_MS * 3);
      expect(fetchMock).toHaveBeenCalledTimes(1);
      expect(sessionCockpitStore.getState().pollHealth.missedBeats).toBe(0);

      await vi.advanceTimersByTimeAsync(
        TERMINAL_CATALOG_REQUEST_TIMEOUT_MS - CATALOG_REFRESH_INTERVAL_MS * 3,
      );
      expect(sessionCockpitStore.getState().pollHealth.missedBeats).toBe(1);

      await vi.advanceTimersByTimeAsync(CATALOG_REFRESH_INTERVAL_MS);
      expect(fetchMock).toHaveBeenCalledTimes(2);
      expect(sessionStore.getState().sessions.map((session) => session.id)).toEqual(["recovered"]);
      expect(sessionCockpitStore.getState().pollHealth.missedBeats).toBe(0);
    } finally {
      release();
    }
  });
});

describe("startCatalogReconciler (eager + cross-tab)", () => {
  it("hydrates once for any subscriber count and immediately removes a remotely terminated row", async () => {
    vi.stubGlobal("BroadcastChannel", FakeBroadcastChannel);
    let rows = [catalogRow({ id: "remote-row" })];
    const fetchMock = vi.fn().mockImplementation(() => Promise.resolve(okResponse(rows)));
    vi.stubGlobal("fetch", fetchMock);

    const releaseA = startCatalogReconciler();
    const releaseB = startCatalogReconciler();
    try {
      await vi.waitFor(() =>
        expect(sessionStore.getState().sessions.map((session) => session.id)).toEqual(["remote-row"]),
      );
      expect(fetchMock).toHaveBeenCalledTimes(1);
      expect(FakeBroadcastChannel.instances).toHaveLength(1);

      // Releasing one subscriber twice is inert; the remaining owner still receives immediate
      // create/leaf invalidations, including an authoritative empty catalog.
      releaseA();
      releaseA();
      expect(FakeBroadcastChannel.instances[0]?.closed).toBe(false);
      rows = [];
      FakeBroadcastChannel.dispatch({
        type: "terminal-catalog-changed",
        source: "other-tab",
        reason: "leaf",
        sessionId: "remote-row",
      });
      await vi.waitFor(() => expect(sessionStore.getState().sessions).toEqual([]));
      expect(fetchMock).toHaveBeenCalledTimes(2);

      // Even if the confirming catalog read is stale, the excluded id cannot be resurrected.
      rows = [catalogRow({ id: "remote-row" })];
      FakeBroadcastChannel.dispatch({
        type: "terminal-catalog-changed",
        source: "other-tab",
        reason: "terminate",
        sessionId: "remote-row",
      });
      await vi.waitFor(() => expect(sessionStore.getState().sessions).toEqual([]));
      expect(fetchMock).toHaveBeenCalledTimes(3);

      releaseB();
      expect(FakeBroadcastChannel.instances[0]?.closed).toBe(true);
      FakeBroadcastChannel.dispatch({
        type: "terminal-catalog-changed",
        source: "other-tab",
        reason: "create",
      });
      await Promise.resolve();
      expect(fetchMock).toHaveBeenCalledTimes(3);
    } finally {
      releaseA();
      releaseB();
    }
  });
});

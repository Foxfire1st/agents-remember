import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { catalogRow } from "../test/fixtures/catalogRows";
import {
  CATALOG_REFRESH_INTERVAL_MS,
  hydrateTerminalSessionsFromCatalog,
  startCatalogPollDriver,
} from "./catalogPoll";
import { sessionCockpitStore } from "./sessionCockpitStore";
import { sessionStore } from "./sessions";

// The hoisted shared poll driver (260715-FEUI-L2 R1): one interval regardless of subscriber
// count, poll-health beats recorded on every read (R15/F3).

const okResponse = (sessions: unknown[]) =>
  ({ ok: true, json: async () => ({ sessions }) }) as Response;

beforeEach(() => {
  sessionStore.getState().hydrate([]);
  // reset poll health
  sessionCockpitStore.setState({ pollHealth: { lastBeatAt: null, missedBeats: 0, healthy: true } });
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
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
});

describe("startCatalogPollDriver (refcounted)", () => {
  it("runs ONE 2500 ms interval for any number of subscribers and stops with the last release", async () => {
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
});

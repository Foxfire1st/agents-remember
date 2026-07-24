import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  CAPABILITY_ERROR_BODIES,
  capabilityEnvelope,
} from "../test/fixtures/capabilityEnvelopes";
import {
  capabilityCatalogStore,
  capabilityCostNote,
  capabilityLoadingCopy,
  cacheStatusNote,
  fetchHarnessCapabilities,
  harnessCapabilities,
  HARNESS_CAPABILITIES_REQUEST_TIMEOUT_MS,
} from "./capabilityCatalog";

// The pre-session capability store: dynamic-only, verbatim errors,
// miss-cost honesty. No fallback catalog exists anywhere in this module — these tests prove the
// store can only ever contain what the daemon actually said.

const ok = (body: unknown) =>
  ({ ok: true, status: 200, json: async () => body }) as Response;
const err = (status: number, body: unknown) =>
  ({ ok: false, status, json: async () => body }) as Response;

// A half-dead socket (live wedge class): never settles on its own; rejects only when
// the request's abort signal fires — what a REAL fetch does when the fetchWithTimeout bound
// aborts it.
const hungSocketImplementation = (_url: string, init?: RequestInit) =>
  new Promise((_resolve, reject) => {
    init?.signal?.addEventListener("abort", () =>
      reject(new DOMException("The operation was aborted.", "AbortError")),
    );
  });

beforeEach(() => capabilityCatalogStore.setState({ perHarness: {} }));
afterEach(() => vi.unstubAllGlobals());

describe("fetchHarnessCapabilities", () => {
  it("first read: idle → loading → envelope stored whole (cacheStatus hit)", async () => {
    const envelope = capabilityEnvelope("claude", "hit");
    let observedLoading = false;
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation(async () => {
        observedLoading = harnessCapabilities("claude").fetchState === "loading";
        return ok(envelope);
      }),
    );
    const entry = await fetchHarnessCapabilities("claude");
    expect(observedLoading).toBe(true);
    expect(entry.fetchState).toBe("idle");
    expect(entry.envelope).toEqual(envelope);
    expect(harnessCapabilities("claude").envelope).toEqual(envelope);
  });

  it("refresh=true sends the flag, shows 'refreshing', and replaces the envelope whole", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(ok(capabilityEnvelope("claude", "miss")))
      .mockImplementationOnce(async () => {
        expect(harnessCapabilities("claude").fetchState).toBe("refreshing");
        return ok(capabilityEnvelope("claude", "refreshed"));
      });
    vi.stubGlobal("fetch", fetchMock);
    await fetchHarnessCapabilities("claude");
    const entry = await fetchHarnessCapabilities("claude", { refresh: true });
    expect(fetchMock.mock.calls[0][0]).toBe("/api/harnesses/claude/capabilities");
    expect(fetchMock.mock.calls[1][0]).toBe("/api/harnesses/claude/capabilities?refresh=true");
    expect(entry.envelope?.cacheStatus).toBe("refreshed");
  });

  it("renders 404/409/503 errors with the VERBATIM status + detail and DROPS the envelope", async () => {
    for (const { httpStatus, body } of Object.values(CAPABILITY_ERROR_BODIES)) {
      capabilityCatalogStore.setState({ perHarness: {} });
      vi.stubGlobal(
        "fetch",
        vi
          .fn()
          .mockResolvedValueOnce(ok(capabilityEnvelope("claude", "hit")))
          .mockResolvedValueOnce(err(httpStatus, body)),
      );
      await fetchHarnessCapabilities("claude");
      const entry = await fetchHarnessCapabilities("claude", { refresh: true });
      expect(entry.fetchState).toBe("error");
      expect(entry.error).toEqual({ httpStatus, status: body.status, detail: body.detail });
      // The daemon quarantined its entry; a stale client snapshot must not masquerade on.
      expect(entry.envelope).toBeUndefined();
      expect(harnessCapabilities("claude").envelope).toBeUndefined();
    }
  });

  it("a thrown fetch is an honest transport error — never a fallback catalog", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("daemon down")));
    const entry = await fetchHarnessCapabilities("codex");
    expect(entry).toMatchObject({
      fetchState: "error",
      error: { httpStatus: null, status: "transport", detail: "daemon down" },
    });
  });

  it("a 200 body that is not the v1 envelope is refused, not adopted", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(ok({ schema: "something-else" })));
    const entry = await fetchHarnessCapabilities("pi");
    expect(entry.fetchState).toBe("error");
    expect(entry.error?.detail).toContain("ar-harness-capabilities/v1");
    expect(entry.envelope).toBeUndefined();
  });

  it("a 200 with malformed MODEL ROWS lands in the honest error path, never a render crash (review finding 4)", async () => {
    const base = capabilityEnvelope("claude", "hit");
    const malformed = [
      { ...base, capabilities: { ...base.capabilities, models: [null] } },
      {
        ...base,
        capabilities: {
          ...base.capabilities,
          models: [{ key: "m1", displayName: "m1", hidden: false }], // missing fields
        },
      },
      {
        ...base,
        capabilities: {
          ...base.capabilities,
          models: [{ ...base.capabilities.models[0], effortOptions: "nope" }],
        },
      },
    ];
    for (const body of malformed) {
      capabilityCatalogStore.setState({ perHarness: {} });
      vi.stubGlobal("fetch", vi.fn().mockResolvedValue(ok(body)));
      const entry = await fetchHarnessCapabilities("claude");
      expect(entry.fetchState).toBe("error");
      expect(entry.error?.detail).toContain("ar-harness-capabilities/v1");
      expect(entry.envelope).toBeUndefined();
    }
  });

  it("a shapeless error body wears 'transport', never a server status word (review finding 2)", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 502,
        json: async () => {
          throw new Error("not json — a gateway page");
        },
      } as unknown as Response),
    );
    const entry = await fetchHarnessCapabilities("claude");
    expect(entry.error).toEqual({ httpStatus: 502, status: "transport", detail: "HTTP 502" });
  });

  it("single-flight: concurrent reads of one harness share ONE request", async () => {
    const fetchMock = vi.fn().mockResolvedValue(ok(capabilityEnvelope("claude", "hit")));
    vi.stubGlobal("fetch", fetchMock);
    await Promise.all([fetchHarnessCapabilities("claude"), fetchHarnessCapabilities("claude")]);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("a hung capabilities socket expires into a transport error and releases the single-flight slot", async () => {
    vi.useFakeTimers();
    try {
      const fetchMock = vi
        .fn()
        .mockImplementationOnce(hungSocketImplementation)
        .mockResolvedValueOnce(ok(capabilityEnvelope("claude", "hit")));
      vi.stubGlobal("fetch", fetchMock);

      const hung = fetchHarnessCapabilities("claude");
      await vi.advanceTimersByTimeAsync(HARNESS_CAPABILITIES_REQUEST_TIMEOUT_MS);
      const entry = await hung; // NEVER throws — the ordinary transport error entry
      expect(entry.fetchState).toBe("error");
      expect(entry.error?.httpStatus).toBeNull();
      expect(entry.error?.status).toBe("transport");

      // Slot released: a later read re-fires and succeeds instead of joining the hung socket.
      const recovered = await fetchHarnessCapabilities("claude");
      expect(recovered.fetchState).toBe("idle");
      expect(fetchMock).toHaveBeenCalledTimes(2);
    } finally {
      vi.useRealTimers();
    }
  });

  it("refresh:true NEVER silently joins an in-flight plain read — it chains a real refresh (review finding 3)", async () => {
    let releaseFirst: (() => void) | undefined;
    const gate = new Promise<void>((resolve) => {
      releaseFirst = resolve;
    });
    const fetchMock = vi.fn().mockImplementation(async (url: string) => {
      if (!url.includes("refresh=true")) await gate;
      return ok(capabilityEnvelope("claude", url.includes("refresh=true") ? "refreshed" : "hit"));
    });
    vi.stubGlobal("fetch", fetchMock);
    const plain = fetchHarnessCapabilities("claude");
    const refreshed = fetchHarnessCapabilities("claude", { refresh: true });
    // a second refresh joins the CHAINED refresh, not the plain read
    const joinedRefresh = fetchHarnessCapabilities("claude", { refresh: true });
    releaseFirst?.();
    const [plainEntry, refreshedEntry, joinedEntry] = await Promise.all([
      plain,
      refreshed,
      joinedRefresh,
    ]);
    expect(plainEntry.envelope?.cacheStatus).toBe("hit");
    expect(refreshedEntry.envelope?.cacheStatus).toBe("refreshed"); // daemon invalidation happened
    expect(joinedEntry.envelope?.cacheStatus).toBe("refreshed");
    expect(fetchMock).toHaveBeenCalledTimes(2); // plain + ONE chained refresh, never a stampede
    expect(fetchMock.mock.calls[1][0]).toBe("/api/harnesses/claude/capabilities?refresh=true");
  });

  it("snapshots are memory-only — a fresh store starts empty (never persisted across reloads)", () => {
    expect(capabilityCatalogStore.getState().perHarness).toEqual({});
    expect(harnessCapabilities("claude").fetchState).toBe("idle");
  });
});

describe("cost honesty (R2)", () => {
  it("miss/initial loading and explicit refresh carry the SAME generic cost naming", () => {
    const cost = capabilityCostNote("claude");
    expect(cost).toBe("starts a short-lived native claude process");
    expect(capabilityLoadingCopy("claude", "initial")).toContain(cost);
    expect(capabilityLoadingCopy("claude", "refresh")).toContain(cost);
    // GENERIC copy only — no invented seconds constant anywhere in the treatment.
    expect(capabilityLoadingCopy("claude", "initial")).not.toMatch(/\d+\s*(s|sec|second)/);
    expect(capabilityLoadingCopy("claude", "refresh")).not.toMatch(/\d+\s*(s|sec|second)/);
  });

  it("cacheStatusNote names whether discovery actually ran — miss reads like refresh", () => {
    expect(cacheStatusNote("hit")).toContain("no native process ran");
    expect(cacheStatusNote("miss")).toContain("same short-lived native discovery as a refresh");
    expect(cacheStatusNote("refreshed")).toContain("native discovery process ran");
  });
});

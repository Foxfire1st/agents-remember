import { beforeEach, describe, expect, it } from "vitest";

import { libraryConversationKey } from "../../test/fixtures/conversationWire";
import { beginOpen, conversationLibraryStore, reconcileOpen } from "./store";
import type { OpenConversationOperation } from "./types";

const KEY = libraryConversationKey("ar-lck1.k1");

function op(overrides: Partial<OpenConversationOperation>): OpenConversationOperation {
  return {
    requestId: "req-1",
    requestFingerprint: "fp",
    revision: 1,
    phase: "requested",
    outcome: "pending",
    rollback: "not-needed",
    ...overrides,
  };
}

function jsonResponse(status: number, body: unknown): Response {
  return { status, ok: status >= 200 && status < 300, json: async () => body } as unknown as Response;
}

function makeFetch(byRoute: Record<string, () => Response>): typeof fetch {
  return (async (input: RequestInfo | URL) => {
    const url = String(input);
    const key = Object.keys(byRoute).find((route) => url.endsWith(route));
    if (key === undefined) throw new Error(`unexpected url ${url}`);
    return byRoute[key]();
  }) as unknown as typeof fetch;
}

describe("conversation library open flow (R4 — focus only on exact opened proof)", () => {
  beforeEach(() => conversationLibraryStore.getState().reset());

  it("marks openedForFocus ONLY when phase=opened && outcome=opened", async () => {
    const fetchImpl = makeFetch({
      "/open": () =>
        jsonResponse(201, op({ phase: "opened", outcome: "opened", arSessionId: "ar-new", bridgeEpoch: "e2" })),
    });
    await beginOpen("codex", KEY, "req-1", "digest-1", {}, { fetchImpl });
    const open = conversationLibraryStore.getState().open;
    expect(open?.openedForFocus).toBe(true);
    expect(open?.operation?.arSessionId).toBe("ar-new");
  });

  it("does NOT focus on a non-opened terminal outcome (unsupported)", async () => {
    const fetchImpl = makeFetch({
      "/open": () => jsonResponse(422, op({ phase: "failed", outcome: "unsupported" })),
    });
    await beginOpen("codex", KEY, "req-1", "digest-1", {}, { fetchImpl });
    const open = conversationLibraryStore.getState().open;
    expect(open?.openedForFocus).toBe(false);
    expect(open?.operation?.outcome).toBe("unsupported");
  });

  it("keeps the SAME requestId across a pending->opened status poll (never a fresh id)", async () => {
    const seenRequestIds: string[] = [];
    const bodies: Record<string, () => Response> = {
      "/open": () => jsonResponse(202, op({ phase: "launching", outcome: "pending" })),
      "/open-status": () =>
        jsonResponse(201, op({ phase: "opened", outcome: "opened", arSessionId: "ar-new", bridgeEpoch: "e2" })),
    };
    const fetchImpl = (async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (typeof init?.body === "string") {
        seenRequestIds.push((JSON.parse(init.body) as { requestId: string }).requestId);
      }
      const key = Object.keys(bodies).find((route) => url.endsWith(route))!;
      return bodies[key]();
    }) as unknown as typeof fetch;

    const scheduled: Array<() => void> = [];
    await beginOpen("codex", KEY, "req-stable", "digest-1", {}, {
      fetchImpl,
      setTimeoutImpl: (fn) => {
        scheduled.push(fn);
        return scheduled.length;
      },
    });
    expect(conversationLibraryStore.getState().open?.operation?.outcome).toBe("pending");
    // Flush the scheduled status poll and let its fetch+json chain drain.
    const poll = scheduled.shift();
    poll?.();
    await new Promise((resolve) => setTimeout(resolve, 0));
    const open = conversationLibraryStore.getState().open;
    expect(open?.openedForFocus).toBe(true);
    expect(new Set(seenRequestIds)).toEqual(new Set(["req-stable"]));
  });

  it("never marks a library row active in this store (no active-session field exists)", () => {
    // Structural proof: the library state has no field that could flag a row as a live AR session.
    const state = conversationLibraryStore.getState();
    expect(Object.keys(state)).not.toContain("activeSessionId");
    expect(state.open).toBeUndefined();
  });

  it("keeps the requestId after a transport failure so a re-attempt reconciles, never mints a fresh id (F6b)", async () => {
    const fetchImpl = makeFetch({ "/open": () => jsonResponse(0, undefined) }); // simulate transport drop
    await beginOpen("codex", KEY, "req-keep", "digest-1", {}, { fetchImpl });
    const open = conversationLibraryStore.getState().open;
    expect(open?.requestId).toBe("req-keep"); // id retained for reconcile
    expect(open?.dispatching).toBe(false);
    expect(open?.error).toBeDefined();
  });

  it("reconcileOpen re-drives open-reconcile under the SAME requestId (F6a)", async () => {
    const seen: string[] = [];
    const fetchImpl = (async (input: RequestInfo | URL, init?: RequestInit) => {
      seen.push(String(input));
      const body = typeof init?.body === "string" ? (JSON.parse(init.body) as { requestId: string }) : { requestId: "" };
      expect(body.requestId).toBe("req-stable");
      return jsonResponse(201, op({ phase: "opened", outcome: "opened", arSessionId: "ar-x", bridgeEpoch: "e" }));
    }) as unknown as typeof fetch;
    const scheduled: Array<() => void> = [];
    reconcileOpen("codex", KEY, "req-stable", {
      fetchImpl,
      setTimeoutImpl: (fn) => {
        scheduled.push(fn);
        return scheduled.length;
      },
    });
    scheduled.shift()?.();
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(seen.some((url) => url.endsWith("/open-reconcile"))).toBe(true);
    expect(conversationLibraryStore.getState().open?.openedForFocus).toBe(true);
  });

  it("marks dispatching from the first call so a double-dispatch is blocked (F6c)", () => {
    // A never-resolving fetch keeps the first request in flight; dispatching must be true immediately.
    const fetchImpl = (() => new Promise<Response>(() => {})) as unknown as typeof fetch;
    void beginOpen("codex", KEY, "req-1", "digest-1", {}, { fetchImpl });
    expect(conversationLibraryStore.getState().open?.dispatching).toBe(true);
  });
});

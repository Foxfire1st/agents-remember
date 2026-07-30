import { afterEach, describe, expect, it, vi } from "vitest";

import {
  AGENT_HISTORY_CHILD_LIMIT,
  activeConversationStore,
  connectConversation,
  disconnectConversation,
  hydrateAgentConversation,
  LRU_LIMIT,
  touchConversation,
} from "./store";
import type { EventSourceCtor } from "./stream";
import type {
  ActiveConversationRef,
  ActiveEventCursor,
  ConversationCapabilities,
  ConversationPage,
  ConversationStatus,
} from "./types";

function identity(sessionId: string, epoch = "e1"): ActiveConversationRef {
  return {
    harnessId: "codex",
    vendorConversationId: "v",
    projectScope: "/r",
    identityDigest: "d",
    arSessionId: sessionId,
    bridgeEpoch: epoch,
  };
}

function page(sessionId: string): ConversationPage {
  return {
    identity: identity(sessionId),
    items: [],
    page: { olderCursor: null, hasOlder: false },
    eventCursor: "evt-0" as ActiveEventCursor,
    hydrationId: "h",
    status: { turn: { state: "ready", turnId: null, stateSince: null } } as unknown as ConversationStatus,
    capabilities: {} as unknown as ConversationCapabilities,
  };
}

// A no-op EventSource so connect can open a stream without a network.
const FakeEventSource = class {
  addEventListener(): void {}
  close(): void {}
} as unknown as EventSourceCtor;

function okFetch(sessionId: string): typeof fetch {
  return (async () => ({ ok: true, status: 200, json: async () => page(sessionId) }) as Response) as unknown as typeof fetch;
}

function errorFetch(status: number, body: unknown): typeof fetch {
  return (async () => ({ ok: false, status, json: async () => body }) as Response) as unknown as typeof fetch;
}

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

afterEach(() => {
  for (const id of Object.keys(activeConversationStore.getState().bySession)) disconnectConversation(id);
  activeConversationStore.getState().reset();
});

describe("activeConversationStore orchestration (F4 keep-alive / LRU, F15 error threading)", () => {
  it("requests native history only for the selected child on the warm session", async () => {
    const calls: Array<{ url: string; method: string }> = [];
    const fetchImpl = (async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      calls.push({ url, method });
      if (method === "POST") {
        return {
          ok: true,
          status: 200,
          json: async () => ({ status: "hydrated", agentId: "agent/a" }),
        } as Response;
      }
      return { ok: true, status: 200, json: async () => page("s1") } as Response;
    }) as typeof fetch;
    connectConversation("s1", "e1", { fetchImpl, eventSourceCtor: FakeEventSource });
    await flush();

    const result = await hydrateAgentConversation("s1", "agent/a");

    expect(result).toEqual({
      ok: true,
      outcome: { status: "hydrated", agentId: "agent/a" },
    });
    const selected = calls.find((call) => call.method === "POST");
    expect(selected?.url).toBe(
      "/api/terminal/s1/conversation/agents/agent%2Fa/history?expectedBridgeEpoch=e1",
    );
    expect(calls.filter((call) => call.method === "POST")).toHaveLength(1);
    expect(activeConversationStore.getState().agentHistoryBySession.s1?.["agent/a"]).toEqual({
      phase: "ready",
      outcome: { status: "hydrated", agentId: "agent/a" },
    });
  });

  it("singleflights concurrent requests for the same selected child", async () => {
    let releasePost: ((response: Response) => void) | undefined;
    const pendingPost = new Promise<Response>((resolve) => {
      releasePost = resolve;
    });
    let postCalls = 0;
    const fetchImpl = (async (_input: RequestInfo | URL, init?: RequestInit) => {
      if ((init?.method ?? "GET") === "POST") {
        postCalls += 1;
        return pendingPost;
      }
      return { ok: true, status: 200, json: async () => page("s1") } as Response;
    }) as typeof fetch;
    connectConversation("s1", "e1", { fetchImpl, eventSourceCtor: FakeEventSource });
    await flush();

    const first = hydrateAgentConversation("s1", "agent-one");
    const second = hydrateAgentConversation("s1", "agent-one");
    expect(postCalls).toBe(1);
    releasePost?.({
      ok: true,
      status: 200,
      json: async () => ({ status: "hydrated", agentId: "agent-one" }),
    } as Response);

    expect(await Promise.all([first, second])).toEqual([
      { ok: true, outcome: { status: "hydrated", agentId: "agent-one" } },
      { ok: true, outcome: { status: "hydrated", agentId: "agent-one" } },
    ]);
    expect(postCalls).toBe(1);
  });

  it("bounds concurrent and retained selected-child bookkeeping per session", async () => {
    let releasePosts: (() => void) | undefined;
    const postGate = new Promise<void>((resolve) => {
      releasePosts = resolve;
    });
    let postCalls = 0;
    const fetchImpl = (async (input: RequestInfo | URL, init?: RequestInit) => {
      if ((init?.method ?? "GET") !== "POST") {
        return { ok: true, status: 200, json: async () => page("s1") } as Response;
      }
      postCalls += 1;
      await postGate;
      const encoded = String(input).split("/agents/")[1]?.split("/history")[0] ?? "";
      return {
        ok: true,
        status: 200,
        json: async () => ({
          status: "hydrated",
          agentId: decodeURIComponent(encoded),
        }),
      } as Response;
    }) as typeof fetch;
    connectConversation("s1", "e1", { fetchImpl, eventSourceCtor: FakeEventSource });
    await flush();

    const accepted = Array.from(
      { length: AGENT_HISTORY_CHILD_LIMIT },
      (_unused, index) => hydrateAgentConversation("s1", `agent-${index}`),
    );
    const refused = await hydrateAgentConversation(
      "s1",
      `agent-${AGENT_HISTORY_CHILD_LIMIT}`,
    );
    expect(refused).toMatchObject({
      ok: false,
      error: { status: "local-resource-limit" },
    });
    expect(postCalls).toBe(AGENT_HISTORY_CHILD_LIMIT);
    expect(
      Object.keys(activeConversationStore.getState().agentHistoryBySession.s1 ?? {}),
    ).toHaveLength(AGENT_HISTORY_CHILD_LIMIT);

    releasePosts?.();
    await Promise.all(accepted);
    expect(
      Object.keys(activeConversationStore.getState().agentHistoryBySession.s1 ?? {}),
    ).toHaveLength(AGENT_HISTORY_CHILD_LIMIT);
  });

  it.each([
    {
      label: "non-2xx",
      post: async () =>
        ({
          ok: false,
          status: 503,
          json: async () => ({ status: "history-offline", detail: "child bridge unavailable" }),
        }) as Response,
      detail: "child bridge unavailable",
      status: "history-offline",
    },
    {
      label: "network",
      post: async () => {
        throw new TypeError("fetch failed");
      },
      detail: "network",
      status: "transport",
    },
    {
      label: "timeout",
      post: async () => {
        throw new DOMException("request expired", "TimeoutError");
      },
      detail: "selected child history request timed out",
      status: "transport",
    },
  ])(
    "keeps a $label failure child-scoped and retryable while the parent stream stays live",
    async ({ post, detail, status }) => {
      let fail = true;
      const fetchImpl = (async (_input: RequestInfo | URL, init?: RequestInit) => {
        if ((init?.method ?? "GET") !== "POST") {
          return { ok: true, status: 200, json: async () => page("s1") } as Response;
        }
        if (fail) return post();
        return {
          ok: true,
          status: 200,
          json: async () => ({ status: "hydrated", agentId: "agent-one" }),
        } as Response;
      }) as typeof fetch;
      connectConversation("s1", "e1", { fetchImpl, eventSourceCtor: FakeEventSource });
      await flush();
      activeConversationStore.getState().setStreamPhase("s1", "live");

      const failed = await hydrateAgentConversation("s1", "agent-one");
      expect(failed?.ok).toBe(false);
      expect(activeConversationStore.getState().agentHistoryBySession.s1?.["agent-one"])
        .toMatchObject({
          phase: "failed",
          error: { status, detail },
        });
      expect(activeConversationStore.getState().bySession.s1?.stream).toBe("live");
      expect(activeConversationStore.getState().errorBySession.s1).toBeUndefined();

      fail = false;
      const recovered = await hydrateAgentConversation("s1", "agent-one");
      expect(recovered).toEqual({
        ok: true,
        outcome: { status: "hydrated", agentId: "agent-one" },
      });
      expect(activeConversationStore.getState().agentHistoryBySession.s1?.["agent-one"])
        .toMatchObject({ phase: "ready" });
      expect(activeConversationStore.getState().bySession.s1?.stream).toBe("live");
    },
  );

  it("keeps an unfocused session's projection across disconnect (keep-alive) and rehydrates on refocus", async () => {
    connectConversation("s1", "e1", { fetchImpl: okFetch("s1"), eventSourceCtor: FakeEventSource });
    await flush();
    expect(activeConversationStore.getState().bySession.s1).toBeDefined();

    // Disconnecting stops the stream but MUST NOT destroy the projection (§11.1 keep-alive).
    disconnectConversation("s1");
    expect(activeConversationStore.getState().bySession.s1).toBeDefined();

    // Refocus rehydrates from server authority.
    connectConversation("s1", "e2", { fetchImpl: okFetch("s1"), eventSourceCtor: FakeEventSource });
    await flush();
    expect(activeConversationStore.getState().bySession.s1).toBeDefined();
  });

  it("bounds bySession with a LRU and evicts the oldest disconnected projections; evicted sessions rehydrate", async () => {
    // Seed 7 hydrated-then-disconnected sessions (projections present, no live runtime).
    for (let index = 1; index <= 7; index += 1) {
      const id = `s${index}`;
      activeConversationStore.getState().applyPage(id, page(id), "initial");
    }
    expect(Object.keys(activeConversationStore.getState().bySession)).toHaveLength(7);

    // Connecting a new session enforces the bounded LRU (limit 6), evicting the oldest non-runtime ones.
    connectConversation("s8", "e1", { fetchImpl: okFetch("s8"), eventSourceCtor: FakeEventSource });
    const after = Object.keys(activeConversationStore.getState().bySession);
    expect(after.length).toBeLessThanOrEqual(6);
    // The most-recently-touched sessions survive; the oldest (s1) is evicted.
    expect(after).not.toContain("s1");

    // An evicted session simply rehydrates on demand.
    connectConversation("s1", "e1", { fetchImpl: okFetch("s1"), eventSourceCtor: FakeEventSource });
    await flush();
    expect(activeConversationStore.getState().bySession.s1).toBeDefined();
  });

  it("keeps EXACTLY LRU_LIMIT warm chats on refocus — the 6th is not evicted (m7)", async () => {
    // Keep-warm: up to LRU_LIMIT recently-focused chats stay connected, and the LRU is the
    // only passive evictor. Refocusing a warm chat (touchConversation) must not disconnect+evict a
    // warm sibling. The regression: touchConversation prepends the focused id THEN enforced the LRU,
    // so `[focused, ...slice(0, LRU_LIMIT - 1)]` double-counted focused and kept only LRU_LIMIT - 1
    // — silently evicting the 6th warm chat on every refocus. The bound pins at exactly 6.
    expect(LRU_LIMIT).toBe(6);
    const ids = Array.from({ length: LRU_LIMIT }, (_, index) => `s${index + 1}`);
    for (const id of ids) {
      connectConversation(id, "e1", { fetchImpl: okFetch(id), eventSourceCtor: FakeEventSource });
      await flush();
    }
    // Precondition: all six are warm (projection present for each).
    expect(Object.keys(activeConversationStore.getState().bySession).sort()).toEqual([...ids].sort());

    // Refocus the OLDEST warm chat. The bound is LRU_LIMIT, so nothing is evicted — all six survive.
    touchConversation("s1");
    const after = Object.keys(activeConversationStore.getState().bySession);
    expect(after).toHaveLength(LRU_LIMIT);
    expect(after.sort()).toEqual([...ids].sort());
  });

  it("threads the server's typed error to the store on a first-connect page failure (F15)", async () => {
    connectConversation("s1", "e1", {
      fetchImpl: errorFetch(409, { status: "cursor-reset-required", detail: "epoch rolled" }),
      eventSourceCtor: FakeEventSource,
    });
    await flush();
    const error = activeConversationStore.getState().errorBySession.s1;
    expect(error?.status).toBe("cursor-reset-required");
    expect(error?.detail).toBe("epoch rolled");
    expect(error?.httpStatus).toBe(409);
    // No projection was fabricated on failure.
    expect(activeConversationStore.getState().bySession.s1).toBeUndefined();
  });

  it("retries a transient boot-race failure on the quiet connecting phase, never fail-loud (R10)", async () => {
    // A fresh launch's first fetch can race the runner's boot and
    // answer transiently (503 / connection refused). Unlike a hard 4xx it must NOT flash the
    // fail-loud "structured surface unavailable" alarm — it retries quietly across the boot window.
    let calls = 0;
    const alwaysTransient = (async () => {
      calls += 1;
      return { ok: false, status: 503, json: async () => ({ status: "unavailable", detail: "bridge composing" }) } as Response;
    }) as unknown as typeof fetch;

    connectConversation("s9", "e1", { fetchImpl: alwaysTransient, eventSourceCtor: FakeEventSource });
    await flush();
    // Right after the first transient 503: no fail-loud error (contrast the 409 case above which
    // sets errorBySession immediately). The surface stays on the quiet connecting phase.
    expect(activeConversationStore.getState().errorBySession.s9).toBeUndefined();
    // It keeps retrying rather than giving up after one attempt.
    await new Promise((resolve) => setTimeout(resolve, 500));
    expect(calls).toBeGreaterThanOrEqual(2);
    // Stop the bounded retry loop for a clean teardown.
    disconnectConversation("s9");
  });

  it("survives a slow fresh-chat boot: transient 503s then 200 inside the window hydrates (260721 D1)", async () => {
    // The measured fresh-chat boot 503s the first page for ~5–7 s; the old fixed 8×400 ms window
    // exhausted first and failed loud. The bounded 30 s window must cover it instead.
    vi.useFakeTimers();
    try {
      const startedAt = Date.now();
      const slowBootFetch = (async () => {
        if (Date.now() - startedAt < 10_000) {
          return { ok: false, status: 503, json: async () => ({ status: "unavailable", detail: "bridge composing" }) } as Response;
        }
        return { ok: true, status: 200, json: async () => page("slow") } as Response;
      }) as unknown as typeof fetch;

      connectConversation("slow", "e1", { fetchImpl: slowBootFetch, eventSourceCtor: FakeEventSource });
      // Still booting at +9 s: quiet connecting, never the alarm.
      await vi.advanceTimersByTimeAsync(9_000);
      expect(activeConversationStore.getState().errorBySession.slow).toBeUndefined();
      expect(activeConversationStore.getState().bySession.slow).toBeUndefined();
      // The bridge answers at +10 s: the projection hydrates with no fail-loud error.
      await vi.advanceTimersByTimeAsync(3_000);
      expect(activeConversationStore.getState().bySession.slow).toBeDefined();
      expect(activeConversationStore.getState().errorBySession.slow).toBeUndefined();
      disconnectConversation("slow");
    } finally {
      vi.useRealTimers();
    }
  });

  it("discovers a ~2.3 s page-ready within ~0.5 s of the 200: the tuned hydrate poll is 250/500/1000 (cap)", async () => {
    // The hydrate fires right after the epoch resolve and often 503s while the
    // bridge finishes, so it shares the boot-quantization cost — the coarse 400→2500 ms backoff
    // could sit up to ~2.5 s stale on a ready bridge. The fine schedule's attempt times are exact:
    // t = 0, then delays 250 → 500 → 1 000 (cap).
    vi.useFakeTimers();
    try {
      const startedAt = Date.now();
      const attemptTimes: number[] = [];
      const tunedBootFetch = (async () => {
        const at = Date.now() - startedAt;
        attemptTimes.push(at);
        if (at < 2_300) {
          return { ok: false, status: 503, json: async () => ({ status: "unavailable", detail: "bridge composing" }) } as Response;
        }
        return { ok: true, status: 200, json: async () => page("tuned") } as Response;
      }) as unknown as typeof fetch;

      connectConversation("tuned", "e1", { fetchImpl: tunedBootFetch, eventSourceCtor: FakeEventSource });
      // Attempts at 0 / 250 / 750 / 1 750, quiet connecting throughout — never the alarm.
      await vi.advanceTimersByTimeAsync(1_800);
      expect(attemptTimes).toEqual([0, 250, 750, 1_750]);
      expect(activeConversationStore.getState().bySession.tuned).toBeUndefined();
      expect(activeConversationStore.getState().errorBySession.tuned).toBeUndefined();
      // The page 200s at ~2.3 s; the next attempt (2.75 s) hydrates — 0.45 s stale at most.
      await vi.advanceTimersByTimeAsync(1_000);
      expect(attemptTimes[attemptTimes.length - 1]).toBe(2_750);
      expect(activeConversationStore.getState().bySession.tuned).toBeDefined();
      expect(activeConversationStore.getState().errorBySession.tuned).toBeUndefined();
      disconnectConversation("tuned");
    } finally {
      vi.useRealTimers();
    }
  });

  it("still fails loud when the boot window truly exhausts (260721 D1 bound)", async () => {
    vi.useFakeTimers();
    try {
      const alwaysTransient = (async () => ({ ok: false, status: 503, json: async () => ({ status: "unavailable", detail: "bridge composing" }) }) as Response) as unknown as typeof fetch;
      connectConversation("dead", "e1", { fetchImpl: alwaysTransient, eventSourceCtor: FakeEventSource });
      await vi.advanceTimersByTimeAsync(29_999);
      expect(activeConversationStore.getState().errorBySession.dead).toBeUndefined();
      await vi.advanceTimersByTimeAsync(2_500);
      // Past the 30 s bound the honest alarm lands with the server's typed reason.
      expect(activeConversationStore.getState().errorBySession.dead?.httpStatus).toBe(503);
      // No projection was fabricated on failure.
      expect(activeConversationStore.getState().bySession.dead).toBeUndefined();
      disconnectConversation("dead");
    } finally {
      vi.useRealTimers();
    }
  });
});

describe("dead-stream escalation (260718-CHATS-L5I F-h)", () => {
  // The live defect: a browser tab holding a resume cursor signed by a PREVIOUS daemon process —
  // the restarted daemon answers 400 cursor-invalid, which EventSource can only surface as an
  // error WITHOUT an open. Retrying the same cursor can never succeed; the store must re-page for
  // a fresh cursor and, if streams still never open, fail LOUD instead of spinning forever.
  class ControlledSource {
    static instances: ControlledSource[] = [];
    listeners: Record<string, Array<(event: unknown) => void>> = {};
    constructor(public url: string) {
      ControlledSource.instances.push(this);
    }
    addEventListener(type: string, fn: (event: unknown) => void): void {
      (this.listeners[type] ??= []).push(fn);
    }
    close(): void {}
    fire(type: string): void {
      for (const fn of this.listeners[type] ?? []) fn({});
    }
  }

  it("re-pages after two unopened attempts, then fails loud at the recovery cap", async () => {
    vi.useFakeTimers();
    try {
      ControlledSource.instances = [];
      let fetches = 0;
      const countingFetch = (async () => {
        fetches += 1;
        return { ok: true, status: 200, json: async () => page("sx") } as Response;
      }) as unknown as typeof fetch;
      connectConversation("sx", "e1", {
        fetchImpl: countingFetch,
        eventSourceCtor: ControlledSource as unknown as EventSourceCtor,
      });
      await vi.advanceTimersByTimeAsync(0);
      expect(fetches).toBe(1);
      expect(ControlledSource.instances.length).toBe(1);

      // Unopened failure 1: the controller backs off and reopens with the SAME cursor.
      ControlledSource.instances[0].fire("error");
      await vi.advanceTimersByTimeAsync(2000);
      expect(ControlledSource.instances.length).toBe(2);
      // Unopened failure 2: escalates — a FRESH page (new server-minted cursor) + restarted stream.
      ControlledSource.instances[1].fire("error");
      await vi.advanceTimersByTimeAsync(0);
      expect(fetches).toBe(2);
      expect(ControlledSource.instances.length).toBe(3);

      // Streams keep getting rejected: after the bounded recoveries, the projection fails LOUD.
      for (let cycle = 0; cycle < 3; cycle += 1) {
        ControlledSource.instances.at(-1)?.fire("error");
        await vi.advanceTimersByTimeAsync(2000);
        ControlledSource.instances.at(-1)?.fire("error");
        await vi.advanceTimersByTimeAsync(0);
      }
      expect(activeConversationStore.getState().bySession.sx?.stream).toBe("projection-failed");
    } finally {
      vi.useRealTimers();
    }
  });
});

describe("sleep/wake stream liveness (260723 developer report)", () => {
  // The live defect: the laptop slept with a chat open; on wake the tab showed "connecting…"
  // FOREVER — the EventSource was a half-open corpse that never fires `error`, so neither the
  // reconnect backoff nor the dead-stream escalation could engage. The stream's liveness watchdog
  // detects the wake (wall-clock jump between timer ticks) and quietly re-subscribes from the
  // projection's resume cursor: no re-page, and the surface never leaves "live".
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
    fire(type: string): void {
      for (const fn of this.listeners[type] ?? []) fn({});
    }
  }

  it("a wall-clock jump on a live stream quietly re-subscribes from the projection cursor — no re-page, never 'connecting…'", async () => {
    vi.useFakeTimers();
    try {
      ControlledSource.instances = [];
      let fetches = 0;
      const countingFetch = (async () => {
        fetches += 1;
        return { ok: true, status: 200, json: async () => page("sz") } as Response;
      }) as unknown as typeof fetch;
      connectConversation("sz", "e1", {
        fetchImpl: countingFetch,
        eventSourceCtor: ControlledSource as unknown as EventSourceCtor,
      });
      await vi.advanceTimersByTimeAsync(0);
      expect(fetches).toBe(1);
      expect(ControlledSource.instances).toHaveLength(1);
      ControlledSource.instances[0].fire("open");
      expect(activeConversationStore.getState().bySession.sz?.stream).toBe("live");

      // OS sleep with the tab visible: the wall clock jumps an hour without a timer tick, then
      // ticks resume — the first post-wake tick judges the open corpse dead.
      vi.setSystemTime(Date.now() + 3_600_000);
      await vi.advanceTimersByTimeAsync(5_000);

      // A FRESH resumable subscribe replaced the corpse, off the projection's own cursor...
      expect(ControlledSource.instances).toHaveLength(2);
      expect(ControlledSource.instances[0].closed).toBe(true);
      expect(ControlledSource.instances[1].url).toContain("after=evt-0");
      // ...with NO re-page (the chain is intact) and NO phase flicker — the projection stayed
      // "live" throughout; "connecting…" / "reconnecting" never rendered.
      expect(fetches).toBe(1);
      expect(activeConversationStore.getState().bySession.sz?.stream).toBe("live");

      // The fresh subscribe opens instantly (priming comment): the resume is invisible.
      ControlledSource.instances[1].fire("open");
      expect(activeConversationStore.getState().bySession.sz?.stream).toBe("live");
      expect(activeConversationStore.getState().errorBySession.sz).toBeUndefined();
    } finally {
      vi.useRealTimers();
    }
  });

  it("a cycled re-subscribe that is REJECTED still escalates through F-h to a re-page", async () => {
    vi.useFakeTimers();
    try {
      ControlledSource.instances = [];
      let fetches = 0;
      const countingFetch = (async () => {
        fetches += 1;
        return { ok: true, status: 200, json: async () => page("sy") } as Response;
      }) as unknown as typeof fetch;
      connectConversation("sy", "e1", {
        fetchImpl: countingFetch,
        eventSourceCtor: ControlledSource as unknown as EventSourceCtor,
      });
      await vi.advanceTimersByTimeAsync(0);
      ControlledSource.instances[0].fire("open");

      // Wake-judge, then the fresh subscribe is refused unopened (e.g. cursor-invalid after a
      // daemon restart during the sleep): the ordinary dead-stream escalation owns recovery.
      vi.setSystemTime(Date.now() + 3_600_000);
      await vi.advanceTimersByTimeAsync(5_000);
      expect(ControlledSource.instances).toHaveLength(2);

      ControlledSource.instances[1].fire("error"); // unopened failure 1: backoff, same cursor
      await vi.advanceTimersByTimeAsync(2_000);
      expect(ControlledSource.instances).toHaveLength(3);
      ControlledSource.instances[2].fire("error"); // unopened failure 2: escalate to a re-page
      await vi.advanceTimersByTimeAsync(0);
      expect(fetches).toBe(2); // the honest full re-page, ONLY now that the chain is broken
      expect(ControlledSource.instances).toHaveLength(4);
      expect(activeConversationStore.getState().bySession.sy?.stream).not.toBe("projection-failed");
    } finally {
      vi.useRealTimers();
    }
  });
});

describe("dead-stream re-page transient tolerance (260724 review m6)", () => {
  // The dead-stream re-page used to treat ANY page failure as terminal — a single transient boot
  // 503 (the daemon restart that dropped the stream also briefly 503s the PAGE route while the bridge
  // recomposes) fired the loud banner immediately, even though hydrateAndStream retries the identical
  // failure across the boot window. The re-page now shares that transient-boot tolerance, so a
  // transient re-page failure retries quietly and never burns a dead-stream recovery; only a
  // genuinely terminal (4xx) answer or an exhausted window counts toward MAX_DEAD_STREAM_RECOVERIES.
  class ControlledSource {
    static instances: ControlledSource[] = [];
    listeners: Record<string, Array<(event: unknown) => void>> = {};
    constructor(public url: string) {
      ControlledSource.instances.push(this);
    }
    addEventListener(type: string, fn: (event: unknown) => void): void {
      (this.listeners[type] ??= []).push(fn);
    }
    close(): void {}
    fire(type: string): void {
      for (const fn of this.listeners[type] ?? []) fn({});
    }
  }

  // Drive ONE dead-stream escalation: two unopened errors on the latest instance (openFailure ×2) →
  // the store re-pages (or, once the budget is spent, fails loud). Mirrors the dead-stream escalation cadence.
  async function driveEscalation(): Promise<void> {
    ControlledSource.instances.at(-1)?.fire("error"); // openFailure 1: backoff + reconnect
    await vi.advanceTimersByTimeAsync(2000);
    ControlledSource.instances.at(-1)?.fire("error"); // openFailure 2: escalate → re-page
    await vi.advanceTimersByTimeAsync(0);
  }

  it("retries a TRANSIENT re-page 503 inside the boot window instead of failing loud, never burning a recovery", async () => {
    vi.useFakeTimers();
    try {
      ControlledSource.instances = [];
      let calls = 0;
      // Hydrate succeeds; the re-page 503s (transient) three times, then the bridge answers 200.
      const bootRacingFetch = (async () => {
        calls += 1;
        if (calls === 1 || calls >= 5) {
          return { ok: true, status: 200, json: async () => page("m6t") } as Response;
        }
        return { ok: false, status: 503, json: async () => ({ status: "unavailable", detail: "bridge composing" }) } as Response;
      }) as unknown as typeof fetch;

      connectConversation("m6t", "e1", {
        fetchImpl: bootRacingFetch,
        eventSourceCtor: ControlledSource as unknown as EventSourceCtor,
      });
      await vi.advanceTimersByTimeAsync(0);
      expect(calls).toBe(1); // hydrate only, one live stream

      // Escalate the stream into a re-page whose first page fetch 503s transiently. The re-page must
      // NOT fail loud on that blip — it stays quiet, retrying inside the boot window. (With the pre-fix
      // code the same 503 sets projection-failed here, so these assertions pin the fix.)
      await driveEscalation();
      expect(calls).toBe(2);
      expect(activeConversationStore.getState().bySession.m6t?.stream).not.toBe("projection-failed");
      expect(activeConversationStore.getState().errorBySession.m6t).toBeUndefined();

      // It keeps retrying (250 → 500 → 1000 backoff) and recovers when the page answers 200 — the
      // transient blip never advanced the dead-stream counter toward the fail-loud cap.
      await vi.advanceTimersByTimeAsync(2000);
      expect(calls).toBeGreaterThanOrEqual(5);
      expect(activeConversationStore.getState().bySession.m6t?.stream).not.toBe("projection-failed");
      expect(activeConversationStore.getState().errorBySession.m6t).toBeUndefined();
      disconnectConversation("m6t");
    } finally {
      vi.useRealTimers();
    }
  });

  it("counts a TERMINAL re-page failure toward the dead-stream cap: 3 terminal ones surface the loud banner", async () => {
    vi.useFakeTimers();
    try {
      ControlledSource.instances = [];
      let calls = 0;
      // Hydrate succeeds; every re-page answers a real terminal 404 (unknown session) — never a blip.
      const terminalRepageFetch = (async () => {
        calls += 1;
        if (calls === 1) {
          return { ok: true, status: 200, json: async () => page("m6x") } as Response;
        }
        return { ok: false, status: 404, json: async () => ({ status: "unknown-session", detail: "gone" }) } as Response;
      }) as unknown as typeof fetch;

      connectConversation("m6x", "e1", {
        fetchImpl: terminalRepageFetch,
        eventSourceCtor: ControlledSource as unknown as EventSourceCtor,
      });
      await vi.advanceTimersByTimeAsync(0);
      expect(calls).toBe(1);

      // One escalation drives the re-page. Each terminal 404 is a failed recovery attempt that burns
      // one budget slot; the loud banner lands only after the whole budget is spent — THREE terminal
      // re-pages, not the first. (Pre-fix the first 404 failed loud, so `calls` would be 2 here — this
      // count is what discriminates the fix from a revert.)
      await driveEscalation();
      await vi.advanceTimersByTimeAsync(0);
      expect(calls).toBe(4); // hydrate + three terminal re-pages (streamRecoveries 1→2→3→cap)
      expect(activeConversationStore.getState().bySession.m6x?.stream).toBe("projection-failed");
      expect(activeConversationStore.getState().errorBySession.m6x?.httpStatus).toBe(404);
      disconnectConversation("m6x");
    } finally {
      vi.useRealTimers();
    }
  });
});

describe("streamRecoveries reset on a clean open (260724 review m16 / F-h)", () => {
  // The store resets streamRecoveries = 0 on every successful open so each SEPARATE dead-stream episode
  // gets its full MAX_DEAD_STREAM_RECOVERIES budget. Without the reset the counter is cumulative for
  // the projection's lifetime, so a chat that recovered cleanly earlier fails loud too soon on the
  // NEXT, unrelated dead-stream — a false projection-failed. This pins the reset: budget spent before
  // a clean open must not carry into a later episode. (Mutation: turn the reset into a no-op → the
  // discriminating assertion below fires the banner early and fails.)
  class ControlledSource {
    static instances: ControlledSource[] = [];
    listeners: Record<string, Array<(event: unknown) => void>> = {};
    constructor(public url: string) {
      ControlledSource.instances.push(this);
    }
    addEventListener(type: string, fn: (event: unknown) => void): void {
      (this.listeners[type] ??= []).push(fn);
    }
    close(): void {}
    fire(type: string): void {
      for (const fn of this.listeners[type] ?? []) fn({});
    }
  }

  // Two unopened errors on the latest instance = one dead-stream escalation (re-page, or the banner
  // once the budget is spent).
  async function escalate(): Promise<void> {
    ControlledSource.instances.at(-1)?.fire("error");
    await vi.advanceTimersByTimeAsync(2000);
    ControlledSource.instances.at(-1)?.fire("error");
    await vi.advanceTimersByTimeAsync(0);
  }

  it("a clean open resets the dead-stream budget so a later, separate episode still gets the full cap", async () => {
    vi.useFakeTimers();
    try {
      ControlledSource.instances = [];
      const okFetchImpl = (async () => ({ ok: true, status: 200, json: async () => page("m16") }) as Response) as unknown as typeof fetch;
      connectConversation("m16", "e1", {
        fetchImpl: okFetchImpl,
        eventSourceCtor: ControlledSource as unknown as EventSourceCtor,
      });
      await vi.advanceTimersByTimeAsync(0);

      // Episode 1: spend MAX_DEAD_STREAM_RECOVERIES - 1 (= 2) recoveries — every re-page succeeds but
      // the fresh stream never opens — WITHOUT reaching the cap. No banner yet.
      await escalate(); // streamRecoveries 0 → 1
      await escalate(); // streamRecoveries 1 → 2
      expect(activeConversationStore.getState().bySession.m16?.stream).not.toBe("projection-failed");

      // A clean open now — the onOpen reset returns the budget to zero (the pinned line).
      ControlledSource.instances.at(-1)?.fire("open");
      expect(activeConversationStore.getState().bySession.m16?.stream).toBe("live");

      // Episode 2 (a separate dead-stream). The opened instance's first error is a DISCONNECT (it
      // opened), so prime one reconnect to reach a fresh unopened instance before escalating.
      ControlledSource.instances.at(-1)?.fire("error"); // disconnect on the opened instance
      await vi.advanceTimersByTimeAsync(2000);

      // Two escalations. WITH the reset the budget is a fresh 3, so streamRecoveries is only 0→1→2 —
      // NO banner. WITHOUT the reset it is still 2, so these same two escalations reach the cap
      // (2→3→fail) and the banner fires. This assertion is what fails when the reset is a no-op.
      await escalate();
      await escalate();
      expect(activeConversationStore.getState().bySession.m16?.stream).not.toBe("projection-failed");

      // The reset restored the FULL budget, not an infinite one: two more escalations spend the rest
      // and the honest loud banner finally lands (proving the bound survives the reset).
      await escalate();
      await escalate();
      expect(activeConversationStore.getState().bySession.m16?.stream).toBe("projection-failed");
      disconnectConversation("m16");
    } finally {
      vi.useRealTimers();
    }
  });
});

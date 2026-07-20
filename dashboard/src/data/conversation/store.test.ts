import { afterEach, describe, expect, it } from "vitest";

import {
  activeConversationStore,
  connectConversation,
  disconnectConversation,
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
});

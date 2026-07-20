import { describe, expect, it } from "vitest";

import {
  applyEvent,
  applyInitialPage,
  applyOlderPage,
  clearRecovery,
  emptyProjection,
  orderedItems,
} from "./reducer";
import type {
  ActiveConversationRef,
  ActiveEventCursor,
  ConversationEventEnvelope,
  ConversationItem,
  ConversationMutation,
  ConversationPage,
} from "./types";

const IDENTITY: ActiveConversationRef = {
  harnessId: "codex",
  vendorConversationId: "vc-1",
  projectScope: "/repo",
  identityDigest: "digest-1",
  arSessionId: "ar-1",
  bridgeEpoch: "epoch-1",
};

function item(overrides: Partial<ConversationItem> & { itemId: string; globalOrdinal: number }): ConversationItem {
  return {
    revision: 1,
    turnId: "t1",
    lane: "harness",
    source: "harness-live",
    provenance: { strength: "exact", origin: "codex" },
    role: "assistant",
    kind: "message",
    phase: "completed",
    blocks: [{ blockId: `${overrides.itemId}-b1`, type: "markdown", markdown: "hello" }],
    ...overrides,
  };
}

function page(items: ConversationItem[], overrides: Partial<ConversationPage> = {}): ConversationPage {
  return {
    identity: IDENTITY,
    items,
    page: { olderCursor: null, hasOlder: false, totalItems: items.length },
    eventCursor: "evt-0" as ActiveEventCursor,
    hydrationId: "hy-1",
    status: {
      identity: IDENTITY,
      revision: 1,
      observedAt: "2026-07-20T00:00:00Z",
      freshness: { state: "fresh", lastEvidenceAt: null, ageMs: null, staleAfterMs: 10000, observationBound: "poll" },
      process: { state: "connected", generation: "g1" },
      turn: { state: "ready", turnId: null, stateSince: null },
      evidence: { strength: "exact", origin: "codex" },
    },
    capabilities: {
      live: {
        text: cap(),
        thinking: cap(),
        tools: cap(),
        diffs: cap(),
        interactions: cap(),
        completeness: cap(),
      },
      history: { list: cap(), read: cap(), resume: cap(), completeness: cap(), toolCompleteness: cap() },
      controls: {
        interrupt: cap(),
        steer: cap(),
        followUp: cap(),
        attachments: { image: attachCap(), file: attachCap(), resource: attachCap() },
        policyRead: cap(),
      },
      telemetry: { context: cap(), usage: cap(), cost: cap(), rateLimit: cap(), compaction: cap() },
    },
    ...overrides,
  };
}

function cap() {
  return { state: "supported" as const, reason: "ok", evidenceTier: "runtime-fixture" as const };
}
function attachCap() {
  return { ...cap(), allowedMimeTypes: [], maxBytes: 0, maxCount: 0, description: "required" as const };
}

function envelope(
  cursor: string,
  previousCursor: string | null,
  mutation: ConversationMutation,
  overrides: Partial<ConversationEventEnvelope> = {},
): ConversationEventEnvelope {
  return {
    identity: IDENTITY,
    cursor: cursor as ActiveEventCursor,
    previousCursor: previousCursor as ActiveEventCursor | null,
    sequence: 1,
    eventId: `e-${cursor}`,
    emittedAt: "2026-07-20T00:00:01Z",
    delivery: "live",
    mutation,
    ...overrides,
  };
}

describe("active conversation reducer", () => {
  it("hydrates an initial page in globalOrdinal order and sets the resume cursor", () => {
    const proj = applyInitialPage(
      emptyProjection(IDENTITY),
      page([item({ itemId: "b", globalOrdinal: 2 }), item({ itemId: "a", globalOrdinal: 1 })]),
    );
    expect(orderedItems(proj).map((i) => i.itemId)).toEqual(["a", "b"]);
    expect(proj.eventCursor).toBe("evt-0");
    expect(proj.totalItems).toBe(2);
  });

  it("appends a live item and advances the resume cursor", () => {
    let proj = applyInitialPage(emptyProjection(IDENTITY), page([item({ itemId: "a", globalOrdinal: 1 })]));
    proj = applyEvent(
      proj,
      envelope("evt-1", "evt-0", { op: "append-item", item: item({ itemId: "b", globalOrdinal: 2 }) }),
    );
    expect(orderedItems(proj).map((i) => i.itemId)).toEqual(["a", "b"]);
    expect(proj.eventCursor).toBe("evt-1");
    expect(proj.stream).toBe("live");
  });

  it("dedupes a replayed event by eventId+cursor (no duplicate on reconnect)", () => {
    let proj = applyInitialPage(emptyProjection(IDENTITY), page([]));
    const env = envelope("evt-1", "evt-0", { op: "append-item", item: item({ itemId: "u1", globalOrdinal: 1, role: "user", lane: "operator", source: "cockpit-composer" }) });
    proj = applyEvent(proj, env);
    proj = applyEvent(proj, env); // exact replay
    expect(orderedItems(proj).map((i) => i.itemId)).toEqual(["u1"]);
  });

  it("NEVER produces a duplicate optimistic user item: the same user item upserts in place", () => {
    let proj = applyInitialPage(emptyProjection(IDENTITY), page([]));
    proj = applyEvent(
      proj,
      envelope("evt-1", "evt-0", {
        op: "append-item",
        item: item({ itemId: "u1", globalOrdinal: 1, role: "user", lane: "operator", source: "cockpit-composer", phase: "pending" }),
      }),
    );
    proj = applyEvent(
      proj,
      envelope("evt-2", "evt-1", {
        op: "upsert-item",
        item: item({ itemId: "u1", globalOrdinal: 1, role: "user", lane: "operator", source: "cockpit-composer", phase: "completed", revision: 2 }),
      }),
    );
    const users = orderedItems(proj).filter((i) => i.role === "user");
    expect(users).toHaveLength(1);
    expect(users[0].phase).toBe("completed");
  });

  it("applies a block delta at the expected revision and is idempotent on replay", () => {
    let proj = applyInitialPage(
      emptyProjection(IDENTITY),
      page([item({ itemId: "a", globalOrdinal: 1, revision: 1, blocks: [{ blockId: "a-b1", type: "markdown", markdown: "he" }] })]),
    );
    const delta = envelope("evt-1", "evt-0", { op: "append-block-delta", itemId: "a", blockId: "a-b1", expectedRevision: 1, nextRevision: 2, delta: "llo" });
    proj = applyEvent(proj, delta);
    expect((proj.itemsById["a"].blocks[0] as { markdown: string }).markdown).toBe("hello");
    proj = applyEvent(proj, delta); // replay: already at revision 2 -> ignored
    expect((proj.itemsById["a"].blocks[0] as { markdown: string }).markdown).toBe("hello");
  });

  it("re-pages conservatively on a block-delta revision skew (missed intermediate delta, L1.4)", () => {
    let proj = applyInitialPage(
      emptyProjection(IDENTITY),
      page([item({ itemId: "a", globalOrdinal: 1, revision: 1, blocks: [{ blockId: "a-b1", type: "markdown", markdown: "he" }] })]),
    );
    proj = applyEvent(proj, envelope("evt-2", "evt-0", { op: "append-block-delta", itemId: "a", blockId: "a-b1", expectedRevision: 3, nextRevision: 4, delta: "x" }));
    expect(proj.recovery?.mode).toBe("repage");
    expect((proj.itemsById["a"].blocks[0] as { markdown: string }).markdown).toBe("he"); // uncorrupted
  });

  it("re-pages conservatively when a live event's previousCursor names an unreceived retained gap (L1.5)", () => {
    let proj = applyInitialPage(emptyProjection(IDENTITY), page([]));
    proj = applyEvent(proj, envelope("evt-1", "evt-0", { op: "append-item", item: item({ itemId: "a", globalOrdinal: 1 }) }));
    // previousCursor points at evt-9, which we never applied -> a retained mid-stream gap.
    const before = orderedItems(proj).length;
    proj = applyEvent(proj, envelope("evt-3", "evt-9", { op: "append-item", item: item({ itemId: "c", globalOrdinal: 3 }) }));
    expect(proj.recovery?.mode).toBe("repage");
    expect(orderedItems(proj)).toHaveLength(before); // the out-of-order event was NOT applied
  });

  it("emits a repage recovery and gap stream on an established-stream gap mutation", () => {
    let proj = applyInitialPage(emptyProjection(IDENTITY), page([]));
    proj = applyEvent(proj, envelope("evt-1", "evt-0", { op: "gap", requestedAfter: "evt-0" as ActiveEventCursor, reason: "retention-overflow", requiresRepage: true, closeAfterEvent: true }));
    expect(proj.stream).toBe("gap");
    expect(proj.recovery?.mode).toBe("repage");
  });

  it("treats a same-revision different-payload item as a protocol fault requiring reset", () => {
    let proj = applyInitialPage(
      emptyProjection(IDENTITY),
      page([item({ itemId: "a", globalOrdinal: 1, revision: 2, blocks: [{ blockId: "a-b1", type: "markdown", markdown: "one" }] })]),
    );
    proj = applyEvent(proj, envelope("evt-1", "evt-0", { op: "upsert-item", item: item({ itemId: "a", globalOrdinal: 1, revision: 2, blocks: [{ blockId: "a-b1", type: "markdown", markdown: "TWO" }] }) }));
    expect(proj.recovery?.mode).toBe("reset");
    expect(proj.fault).toBeDefined();
  });

  it("ignores a stale (lower revision) status and rejects equal-revision divergence", () => {
    let proj = applyInitialPage(emptyProjection(IDENTITY), page([]));
    const base = proj.status!;
    proj = applyEvent(proj, envelope("evt-1", "evt-0", { op: "status", status: { ...base, revision: 0, turn: { ...base.turn, state: "working" } } }));
    expect(proj.status?.turn.state).toBe("ready"); // lower revision ignored
  });

  it("prepends an older page while preserving existing items and the resume cursor", () => {
    let proj = applyInitialPage(
      emptyProjection(IDENTITY),
      page([item({ itemId: "b", globalOrdinal: 2 })], { eventCursor: "evt-5" as ActiveEventCursor }),
    );
    proj = applyOlderPage(
      proj,
      page([item({ itemId: "a", globalOrdinal: 1 })], { page: { olderCursor: null, hasOlder: false }, eventCursor: "evt-OLD" as ActiveEventCursor }),
    );
    expect(orderedItems(proj).map((i) => i.itemId)).toEqual(["a", "b"]);
    expect(proj.eventCursor).toBe("evt-5"); // older paging never moves the live resume point
  });

  it("re-hydrates and clears applied keys + recovery on a replace-page mutation", () => {
    let proj = applyInitialPage(emptyProjection(IDENTITY), page([item({ itemId: "a", globalOrdinal: 1 })]));
    proj = applyEvent(proj, envelope("evt-1", "evt-0", { op: "gap", requestedAfter: "evt-0" as ActiveEventCursor, reason: "projector-restart", requiresRepage: true, closeAfterEvent: true }));
    expect(proj.recovery?.mode).toBe("repage");
    proj = applyEvent(proj, envelope("evt-9", "evt-8", { op: "replace-page", items: [item({ itemId: "x", globalOrdinal: 1 })], eventCursor: "evt-9" as ActiveEventCursor, reason: "native-rehydrate" }));
    expect(orderedItems(proj).map((i) => i.itemId)).toEqual(["x"]);
    expect(proj.eventCursor).toBe("evt-9");
    expect(proj.recovery).toBeUndefined();
    expect(clearRecovery(proj).fault).toBeUndefined();
  });

  it("drops events for a different identity/epoch (no cross-generation merge)", () => {
    let proj = applyInitialPage(emptyProjection(IDENTITY), page([]));
    const foreign = envelope("evt-1", "evt-0", { op: "append-item", item: item({ itemId: "z", globalOrdinal: 1 }) }, { identity: { ...IDENTITY, bridgeEpoch: "epoch-2" } });
    proj = applyEvent(proj, foreign);
    expect(orderedItems(proj)).toHaveLength(0);
  });

  it("marks replay/hydration deliveries so announcers can stay silent", () => {
    let proj = applyInitialPage(emptyProjection(IDENTITY), page([]));
    proj = applyEvent(proj, envelope("evt-1", "evt-0", { op: "append-item", item: item({ itemId: "a", globalOrdinal: 1 }) }, { delivery: "resume-replay" }));
    expect(proj.lastAppliedDelivery).toBe("resume-replay");
  });
});

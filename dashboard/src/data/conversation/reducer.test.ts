import { describe, expect, it } from "vitest";

import {
  applyEvent,
  applyInitialPage,
  applyOlderPage,
  clearRecovery,
  emptyProjection,
  orderedItems,
} from "./reducer";
import {
  conversationIdentity,
  conversationItem as item,
  conversationPage,
  eventCursor,
} from "../../test/fixtures/conversationWire";
import type {
  ActiveConversationRef,
  ConversationEventEnvelope,
  ConversationItem,
  ConversationMutation,
  ConversationPage,
} from "./types";

const IDENTITY: ActiveConversationRef = conversationIdentity();

function page(items: ConversationItem[], overrides: Partial<ConversationPage> = {}): ConversationPage {
  return conversationPage({ identity: IDENTITY, items, ...overrides });
}

function envelope(
  cursor: string,
  previousCursor: string | null,
  mutation: ConversationMutation,
  overrides: Partial<ConversationEventEnvelope> = {},
): ConversationEventEnvelope {
  return {
    identity: IDENTITY,
    cursor: eventCursor(cursor),
    previousCursor: previousCursor === null ? null : eventCursor(previousCursor),
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
    proj = applyEvent(proj, envelope("evt-1", "evt-0", { op: "gap", requestedAfter: eventCursor("evt-0"), reason: "retention-overflow", requiresRepage: true, closeAfterEvent: true }));
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
      page([item({ itemId: "b", globalOrdinal: 2 })], { eventCursor: eventCursor("evt-5") }),
    );
    proj = applyOlderPage(
      proj,
      page([item({ itemId: "a", globalOrdinal: 1 })], { page: { olderCursor: null, hasOlder: false }, eventCursor: eventCursor("evt-OLD") }),
    );
    expect(orderedItems(proj).map((i) => i.itemId)).toEqual(["a", "b"]);
    expect(proj.eventCursor).toBe("evt-5"); // older paging never moves the live resume point
  });

  it("re-hydrates and clears applied keys + recovery on a replace-page mutation", () => {
    let proj = applyInitialPage(emptyProjection(IDENTITY), page([item({ itemId: "a", globalOrdinal: 1 })]));
    proj = applyEvent(proj, envelope("evt-1", "evt-0", { op: "gap", requestedAfter: eventCursor("evt-0"), reason: "projector-restart", requiresRepage: true, closeAfterEvent: true }));
    expect(proj.recovery?.mode).toBe("repage");
    proj = applyEvent(proj, envelope("evt-9", "evt-8", { op: "replace-page", items: [item({ itemId: "x", globalOrdinal: 1 })], eventCursor: eventCursor("evt-9"), reason: "native-rehydrate" }));
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

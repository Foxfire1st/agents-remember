import { beforeEach, describe, expect, it } from "vitest";

import { catalogRow } from "../test/fixtures/catalogRows";
import { applySeatEvent, applySeatEventLine, createGatedSeatEventApplier } from "./seatEvents";
import { fromTerminalSessionInfo, sessionStore } from "./sessions";
import { observerEvent } from "../test/fixtures/wire";
import type { ObserverEvent } from "../types/event";

// Seat-event dedup against the authoritative poll (260715-FEUI-L2 R2): push may pre-apply what
// the next catalog beat confirms, but can never regress a row the poll already advanced.

// The seat-event default this file reads: a `seat.retired` envelope stamped at the same instant
// the seeded row's `turnStateChangedAt` carries, so the turn-state ordering tests below measure
// their own explicit `ts` against a known one.
const event = (overrides: Partial<ObserverEvent>): ObserverEvent =>
  observerEvent({
    id: "evt-1",
    ts: "2026-07-16T10:00:00Z",
    kind: "seat.retired",
    ...overrides,
  });

const seed = (overrides: Parameters<typeof catalogRow>[0] = {}) => {
  sessionStore
    .getState()
    .hydrate([fromTerminalSessionInfo(catalogRow({ id: "seat-1", label: "seat-1", ...overrides }))]);
};

const seat = () => sessionStore.getState().sessions.find((session) => session.id === "seat-1");

beforeEach(() => {
  sessionStore.getState().hydrate([]);
});

describe("seat.retired / seat.landed", () => {
  it("marks a running row terminated with retirement provenance", () => {
    seed();
    applySeatEvent(
      event({
        sessionId: "seat-1",
        data: { session: "seat-1", retiredReason: "superseded", retiredEdge: "manual" },
      }),
    );
    expect(seat()).toMatchObject({
      status: "terminated",
      retiredReason: "superseded",
      retiredEdge: "manual",
    });
  });

  it("never resurrects or double-applies over poll truth (poll authoritative)", () => {
    seed({ status: "landed", landedReason: "leaf integrated" });
    applySeatEvent(event({ sessionId: "seat-1", data: { retiredReason: "late event" } }));
    expect(seat()).toMatchObject({ status: "landed", landedReason: "leaf integrated" });
    expect(seat()?.retiredReason).toBeUndefined();
  });

  it("lands a running row with landing provenance", () => {
    seed();
    applySeatEvent(
      event({
        kind: "seat.landed",
        sessionId: "seat-1",
        data: { landedReason: "review approved", landedEdge: "leaf-integration" },
      }),
    );
    expect(seat()).toMatchObject({ status: "landed", landedReason: "review approved" });
  });

  it("ignores unknown sessions — push never invents a row", () => {
    seed();
    applySeatEvent(event({ sessionId: "ghost", data: {} }));
    expect(sessionStore.getState().sessions).toHaveLength(1);
    expect(seat()?.status).toBe("running");
  });
});

describe("seat.renamed", () => {
  it("applies a rename and freezes the original label", () => {
    seed();
    applySeatEvent(
      event({
        kind: "seat.renamed",
        sessionId: "seat-1",
        data: { label: "worker-renamed", spawnedLabel: "seat-1" },
      }),
    );
    expect(seat()).toMatchObject({ label: "worker-renamed", spawnedLabel: "seat-1" });
  });

  it("is a no-op when the poll already delivered the same label", () => {
    seed({ label: "already" });
    const before = sessionStore.getState().sessions;
    applySeatEvent(event({ kind: "seat.renamed", sessionId: "seat-1", data: { label: "already" } }));
    expect(sessionStore.getState().sessions).toBe(before);
  });
});

describe("seat.turn-state-changed (sweep-emitted — can never beat the poll)", () => {
  it("applies only when strictly newer than the stored transition", () => {
    seed({ turnState: "working", turnStateChangedAt: "2026-07-16T10:00:00Z" });
    // Equal timestamp → stale duplicate of what the poll already served: ignored.
    applySeatEvent(
      event({
        kind: "seat.turn-state-changed",
        sessionId: "seat-1",
        ts: "2026-07-16T10:00:00Z",
        data: { turnState: "turn-ended" },
      }),
    );
    expect(seat()?.turnState).toBe("working");
    // Older event → ignored.
    applySeatEvent(
      event({
        kind: "seat.turn-state-changed",
        sessionId: "seat-1",
        ts: "2026-07-16T09:59:59Z",
        data: { turnState: "turn-ended" },
      }),
    );
    expect(seat()?.turnState).toBe("working");
    // Strictly newer → the consistency backstop applies it.
    applySeatEvent(
      event({
        kind: "seat.turn-state-changed",
        sessionId: "seat-1",
        ts: "2026-07-16T10:00:01Z",
        data: { turnState: "turn-ended" },
      }),
    );
    expect(seat()).toMatchObject({ turnState: "turn-ended", turnStateChangedAt: "2026-07-16T10:00:01Z" });
  });

  it("rejects unknown turn-state words — mirrored vocabulary only", () => {
    seed({ turnState: "working", turnStateChangedAt: "2026-07-16T10:00:00Z" });
    applySeatEvent(
      event({
        kind: "seat.turn-state-changed",
        sessionId: "seat-1",
        ts: "2026-07-16T11:00:00Z",
        data: { turnState: "daydreaming" },
      }),
    );
    expect(seat()?.turnState).toBe("working");
  });
});

describe("applySeatEventLine", () => {
  it("parses JSONL lines, ignores malformed ones and non-seat kinds", () => {
    seed();
    applySeatEventLine("not json {");
    applySeatEventLine(JSON.stringify(event({ kind: "gate.decided", sessionId: "seat-1" })));
    expect(seat()?.status).toBe("running");
    applySeatEventLine(
      JSON.stringify(event({ kind: "seat.landed", sessionId: "seat-1", data: { landedReason: "done" } })),
    );
    expect(seat()).toMatchObject({ status: "landed", landedReason: "done" });
  });
});

describe("createGatedSeatEventApplier (per-connection backlog gate — review finding 2)", () => {
  const renameLine = (label: string) =>
    JSON.stringify(event({ kind: "seat.renamed", sessionId: "seat-1", data: { label } }));

  it("applies lines only between a ready and the next interruption", () => {
    seed();
    const gate = createGatedSeatEventApplier();
    gate.onLine(renameLine("from-backlog"));
    expect(seat()?.label).toBe("seat-1"); // pre-ready backlog never applies
    gate.onReady();
    gate.onLine(renameLine("live-rename"));
    expect(seat()?.label).toBe("live-rename"); // live window applies
  });

  it("re-closes on interrupt: a reconnect's backlog (undecodable-cursor replay) never applies", () => {
    seed();
    const gate = createGatedSeatEventApplier();
    gate.onReady();
    gate.onInterrupt(); // connection lost — the NEXT connection replays a backlog first
    gate.onLine(renameLine("stale-history"));
    expect(seat()?.label).toBe("seat-1"); // replayed history stays out
    gate.onReady(); // the new connection's own ready re-opens the gate
    gate.onLine(renameLine("post-reconnect"));
    expect(seat()?.label).toBe("post-reconnect");
  });
});

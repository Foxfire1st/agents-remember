import type { ObserverEvent } from "../types/event";
import { sessionStore, type OpenSession } from "./sessions";

// Seat-event reconciliation (260715-FEUI-L2 S2/R2): the `/api/events` observer channel carries
// seat.retired | seat.landed | seat.renamed | seat.turn-state-changed (serving/seat_events.py).
// Cockpit wires `applySeatEventLine` into the SAME EventSource the Event River already holds —
// one connection, two consumers. THE POLL STAYS AUTHORITATIVE: everything applied here is
// pre-applied UI state the next 2500 ms catalog beat confirms or replaces.
//
// Honest latency scoping (UA-6): retired/renamed/landed are emitted at the moment of the tool
// call and CAN beat the poll by up to one beat. turn-state-changed is emitted BY the liveness
// sweep itself (10 s rate-limited) and can NEVER beat the poll — it is applied only as a
// consistency backstop (e.g. after a dropped hydrate), never a latency win.

const SEAT_EVENT_KINDS = new Set([
  "seat.retired",
  "seat.landed",
  "seat.renamed",
  "seat.turn-state-changed",
]);

const TURN_STATES = new Set(["working", "turn-ended", "awaiting-input", "stale"]);

function dataString(event: ObserverEvent, key: string): string | undefined {
  const value = event.data?.[key];
  return typeof value === "string" ? value : undefined;
}

function findSession(event: ObserverEvent): OpenSession | undefined {
  const id = event.sessionId ?? dataString(event, "session");
  if (!id) return undefined;
  return sessionStore.getState().sessions.find((session) => session.id === id);
}

/**
 * Apply one seat event against the session registry, deduplicating against poll truth: an event
 * that says nothing newer than the store is a no-op. Unknown sessions are ignored — the poll
 * brings new rows; push never invents one.
 */
export function applySeatEvent(event: ObserverEvent): void {
  if (!SEAT_EVENT_KINDS.has(event.kind)) return;
  const session = findSession(event);
  if (!session) return;
  const store = sessionStore.getState();

  switch (event.kind) {
    case "seat.retired": {
      // A terminal mark: never resurrect, never double-apply over poll truth.
      if (session.status === "terminated" || session.status === "landed") return;
      store.setStatus(session.id, "terminated");
      store.patch(session.id, {
        retiredAt: event.ts,
        ...(dataString(event, "retiredReason") ? { retiredReason: dataString(event, "retiredReason") } : {}),
        ...(dataString(event, "retiredEdge") ? { retiredEdge: dataString(event, "retiredEdge") } : {}),
        ...(dataString(event, "retiredBySession")
          ? { retiredBySession: dataString(event, "retiredBySession") }
          : {}),
      });
      return;
    }
    case "seat.landed": {
      if (session.status === "terminated" || session.status === "landed") return;
      store.setStatus(session.id, "landed");
      store.patch(session.id, {
        landedAt: event.ts,
        ...(dataString(event, "landedReason") ? { landedReason: dataString(event, "landedReason") } : {}),
        ...(dataString(event, "landedEdge") ? { landedEdge: dataString(event, "landedEdge") } : {}),
      });
      return;
    }
    case "seat.renamed": {
      const label = dataString(event, "label");
      if (!label || label === session.label) return;
      store.patch(session.id, {
        label,
        ...(dataString(event, "spawnedLabel") ? { spawnedLabel: dataString(event, "spawnedLabel") } : {}),
      });
      return;
    }
    case "seat.turn-state-changed": {
      const turnState = dataString(event, "turnState");
      if (!turnState || !TURN_STATES.has(turnState)) return;
      // Dedup: the sweep's event timestamp must be strictly newer than the stored transition —
      // the poll (which serves the same sweep's classification 4× as often) usually got here
      // first, and an equal-or-older event must never regress the row.
      if (session.turnStateChangedAt && event.ts <= session.turnStateChangedAt) return;
      if (session.turnState === turnState) return;
      store.patch(session.id, { turnState, turnStateChangedAt: event.ts });
      return;
    }
  }
}

/** Line adapter for `connectEvents` — malformed lines are ignored, never break the feed. */
export function applySeatEventLine(line: string): void {
  let event: ObserverEvent;
  try {
    event = JSON.parse(line) as ObserverEvent;
  } catch {
    return;
  }
  if (typeof event !== "object" || event === null || typeof event.kind !== "string") return;
  applySeatEvent(event);
}

/**
 * The backlog gate around {@link applySeatEventLine}: the channel replays history before EACH
 * connection's `ready` marker (a reconnect with an undecodable cursor replays the full initial
 * window — review finding 2), and replayed history must never touch live rows. Lines apply only
 * between a `ready` and the next interruption; everything else is the poll's job anyway (the
 * poll stays authoritative, so a closed gate costs at most one 2.5 s beat of push latency).
 */
export function createGatedSeatEventApplier(): {
  onLine: (line: string) => void;
  onReady: () => void;
  onInterrupt: () => void;
} {
  let open = false;
  return {
    onLine: (line) => {
      if (open) applySeatEventLine(line);
    },
    onReady: () => {
      open = true;
    },
    onInterrupt: () => {
      open = false; // the next connection replays a backlog before its own `ready`
    },
  };
}

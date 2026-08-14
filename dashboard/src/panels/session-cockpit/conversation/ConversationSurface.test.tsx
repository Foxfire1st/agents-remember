// ConversationSurface visibility gating: while the ChatsStageBody keep-alive pool holds a
// surface mounted-but-hidden behind another chat, its projection keeps updating (the data layer
// stays warm) — but the global live regions must never voice a chat the operator is not looking
// at, and a re-show must not retro-announce what happened while hidden. The refs keep TRACKING
// either way, so only genuinely fresh, visible transitions are voiced.

import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { announceAssertive, announcePolite } from "../../../data/announcer";
import type { ActiveConversationProjection } from "../../../data/conversation/reducer";
import { emptyProjection } from "../../../data/conversation/reducer";
import { activeConversationStore, readConversationScroll } from "../../../data/conversation/store";
import type {
  ActiveConversationRef,
  ConversationItem,
  ConversationStatus,
} from "../../../data/conversation/types";
import { ConversationSurface } from "./ConversationSurface";

vi.mock("../../../data/announcer", () => ({
  announcePolite: vi.fn(),
  announceAssertive: vi.fn(),
}));
// AmbientTelemetry fetches on mount/revision; the ambient chips are never asserted here.
vi.mock("./AmbientTelemetry", () => ({ AmbientTelemetry: () => null }));

const SESSION_ID = "keepalive-hidden";

function identity(): ActiveConversationRef {
  return {
    harnessId: "codex",
    vendorConversationId: "v",
    projectScope: "/r",
    identityDigest: "d",
    arSessionId: SESSION_ID,
    bridgeEpoch: "e1",
  };
}

function status(turnState: ConversationStatus["turn"]["state"], revision: number): ConversationStatus {
  return {
    identity: identity(),
    revision,
    observedAt: "2026-07-21T00:00:00Z",
    freshness: {
      state: "fresh",
      lastEvidenceAt: null,
      ageMs: null,
      staleAfterMs: 1,
      observationBound: "poll",
    },
    process: { state: "connected", generation: "g" },
    turn: { state: turnState, turnId: null, stateSince: null },
    evidence: { strength: "exact", origin: "codex" },
  };
}

function seed(overrides: Partial<ActiveConversationProjection>): void {
  const current = activeConversationStore.getState().bySession[SESSION_ID];
  const base = current ?? {
    ...emptyProjection(identity()),
    stream: "live" as const,
    status: status("ready", 1),
    lastAppliedDelivery: "live" as const,
  };
  activeConversationStore.setState({
    bySession: { [SESSION_ID]: { ...base, ...overrides } },
  });
}

function surface(visible: boolean) {
  return (
    <ConversationSurface
      sessionId={SESSION_ID}
      visible={visible}
      onRetry={() => {}}
      onShowDiagnostics={() => {}}
    />
  );
}

afterEach(() => {
  cleanup();
  activeConversationStore.getState().reset();
  vi.clearAllMocks();
});

describe("ConversationSurface hidden keep-alive gating (F-j)", () => {
  it("renders the Agents Remember welcome surface for a live empty conversation", () => {
    seed({});
    render(surface(true));

    expect(screen.getByTestId("conversation-welcome").textContent).toContain("Agents Remember");
    expect(screen.getByTestId("conversation-welcome-harness").textContent).toContain(
      "codex link ready",
    );
    expect(screen.queryByText(/No messages yet/i)).toBeNull();
  });

  it("tracks but never voices stream/turn transitions while hidden — and voices fresh ones once visible", () => {
    seed({});
    const { rerender } = render(surface(false));

    // Hidden: a live turn failure and a stream drop land on the projection but stay silent.
    act(() => seed({ status: status("failed", 2) }));
    act(() => seed({ stream: "reconnecting" }));
    expect(announcePolite).not.toHaveBeenCalled();
    expect(announceAssertive).not.toHaveBeenCalled();

    // Re-show alone must NOT retro-announce what happened while hidden.
    rerender(surface(true));
    expect(announcePolite).not.toHaveBeenCalled();
    expect(announceAssertive).not.toHaveBeenCalled();

    // Genuinely fresh transitions once visible voice exactly as before.
    act(() => seed({ stream: "gap" }));
    expect(announcePolite).toHaveBeenCalledWith("re-syncing history");
    act(() => seed({ status: status("ready", 3) }));
    expect(announcePolite).toHaveBeenCalledWith("response complete");
  });
});

describe("ConversationSurface — latest chip (B3)", () => {
  // jsdom has no layout: pin a fixed geometry so the virtualizer renders rows and the chip's
  // scrollable gate reads an honest box, and tanstack's scrollTo stays observable.
  beforeEach(() => {
    Object.defineProperty(HTMLElement.prototype, "offsetHeight", { configurable: true, value: 600 });
    Object.defineProperty(HTMLElement.prototype, "offsetWidth", { configurable: true, value: 800 });
    Object.defineProperty(HTMLElement.prototype, "scrollHeight", { configurable: true, value: 6000 });
    Object.defineProperty(HTMLElement.prototype, "clientHeight", { configurable: true, value: 600 });
    Object.defineProperty(HTMLElement.prototype, "scrollTo", {
      configurable: true,
      value: function (this: HTMLElement, options?: { top?: number }) {
        this.scrollTop = options?.top ?? 0;
      },
    });
  });
  afterEach(() => {
    const proto = HTMLElement.prototype as unknown as Record<string, unknown>;
    delete proto.offsetHeight;
    delete proto.offsetWidth;
    delete proto.scrollHeight;
    delete proto.clientHeight;
    delete proto.scrollTo;
  });

  function seedItems(count: number): void {
    const items: ConversationItem[] = Array.from({ length: count }, (_, index) => ({
      itemId: `fab-item-${index + 1}`,
      globalOrdinal: index + 1,
      revision: 1,
      lane: "harness",
      source: "harness-live",
      provenance: { strength: "exact", origin: "codex" },
      role: "assistant",
      kind: "message",
      phase: "completed",
      blocks: [{ blockId: `fab-item-${index + 1}-b`, type: "markdown", markdown: `message ${index + 1}` }],
    }));
    seed({
      orderedItemIds: items.map((item) => item.itemId),
      itemsById: Object.fromEntries(items.map((item) => [item.itemId, item])),
      totalItems: count,
    });
  }

  it("shows one latest chip only after the feed moves away from the live edge, then pins the end", () => {
    seedItems(6);
    render(surface(true));
    const viewport = screen.getByTestId("conversation-viewport");
    expect(screen.queryByTestId("conversation-scroll-latest")).toBeNull();

    fireEvent.wheel(viewport);
    viewport.scrollTop = 3000;
    fireEvent.scroll(viewport);
    expect(screen.getByTestId("conversation-scroll-latest").getAttribute("aria-label")).toBe("Jump to latest");
    expect(screen.queryByTestId("conversation-scroll-top")).toBeNull();

    fireEvent.click(screen.getByTestId("conversation-scroll-latest"));
    expect(viewport.scrollTop).toBe(6000 - 600);
    expect(screen.queryByTestId("conversation-scroll-latest")).toBeNull();
  });
});

describe("ConversationSurface — the scroll-up trap, production wiring (L5I interactive fix)", () => {
  // The confirmed repro, kept as a permanent regression test against the PRODUCTION
  // wiring (the real surface + store): (a) the follow-on-growth effect re-pinned mid-gesture —
  // one 50px wheel-up inside the 120px band snapped back to the end; (b) every scroll event
  // rebuilt the store projection via the dead setScrollAnchor write, re-rendering the surface
  // and re-running the follow with fresh rows identity. The store write chain is deleted and
  // the follow bails while the operator's genuine-input window owns a displaced landing.
  beforeEach(() => {
    Object.defineProperty(HTMLElement.prototype, "offsetHeight", { configurable: true, value: 600 });
    Object.defineProperty(HTMLElement.prototype, "offsetWidth", { configurable: true, value: 800 });
    Object.defineProperty(HTMLElement.prototype, "scrollHeight", { configurable: true, value: 6000 });
    Object.defineProperty(HTMLElement.prototype, "clientHeight", { configurable: true, value: 600 });
    Object.defineProperty(HTMLElement.prototype, "scrollTo", {
      configurable: true,
      value: function (this: HTMLElement, options?: { top?: number }) {
        this.scrollTop = options?.top ?? 0;
      },
    });
  });
  afterEach(() => {
    const proto = HTMLElement.prototype as unknown as Record<string, unknown>;
    delete proto.offsetHeight;
    delete proto.offsetWidth;
    delete proto.scrollHeight;
    delete proto.clientHeight;
    delete proto.scrollTo;
  });

  function seedItems(count: number): void {
    const items: ConversationItem[] = Array.from({ length: count }, (_, index) => ({
      itemId: `trap-item-${index + 1}`,
      globalOrdinal: index + 1,
      revision: 1,
      lane: "harness",
      source: "harness-live",
      provenance: { strength: "exact", origin: "codex" },
      role: "assistant",
      kind: "message",
      phase: "completed",
      blocks: [{ blockId: `trap-item-${index + 1}-b`, type: "markdown", markdown: `message ${index + 1}` }],
    }));
    seed({
      orderedItemIds: items.map((item) => item.itemId),
      itemsById: Object.fromEntries(items.map((item) => [item.itemId, item])),
      totalItems: count,
    });
  }

  function pinViewport(viewport: HTMLElement, scrollHeight: number, clientHeight: number): void {
    Object.defineProperty(viewport, "scrollHeight", { configurable: true, value: scrollHeight });
    Object.defineProperty(viewport, "clientHeight", { configurable: true, value: clientHeight });
  }

  /** An append-block delta: the LAST item grows in place (same itemId → same row key). */
  function growLastItem(): void {
    const projection = activeConversationStore.getState().bySession[SESSION_ID];
    const lastId = projection.orderedItemIds[projection.orderedItemIds.length - 1];
    const last = projection.itemsById[lastId];
    seed({
      itemsById: {
        ...projection.itemsById,
        [lastId]: {
          ...last,
          blocks: [{ blockId: `${lastId}-b`, type: "markdown", markdown: "message\nstreamed more\nand more\nand more" }],
        },
      },
    });
  }

  it("a 50px wheel-up inside the band holds through growth, six 40px deltas accumulate, bottom re-locks and follows — and no scroll event rebuilds the projection", async () => {
    vi.useFakeTimers();
    try {
      seedItems(10);
      render(surface(true));
      const viewport = screen.getByTestId("conversation-viewport");
      pinViewport(viewport, 6000, 600);
      await act(async () => { await vi.advanceTimersByTimeAsync(250); });
      expect(viewport.scrollTop).toBe(5400); // the mount restore drove to the end and consumed

      // The dead write chain is gone from the store entirely (defect 1b).
      expect("setScrollAnchor" in activeConversationStore.getState()).toBe(false);

      // One 50px wheel-up INSIDE the band (distance 50 ≤ 120: lock stays engaged by design)…
      fireEvent.wheel(viewport);
      viewport.scrollTop = 5350;
      fireEvent.scroll(viewport);

      // …then a stream delta grows the last row IN PLACE mid-gesture (the repro's growth shape):
      // before the fix the follow-on-growth effect snapped the offset to 5700 on the spot.
      pinViewport(viewport, 6300, 600);
      act(() => growLastItem());
      expect(viewport.scrollTop).toBe(5350); // HOLDS — no snap back

      // Six 40px trackpad deltas accumulate upward (the second leaves the band and disengages
      // the lock); a scroll event must NEVER rebuild the store projection.
      const projectionAfterGrowth = activeConversationStore.getState().bySession[SESSION_ID];
      let expected = 5350;
      for (let delta = 0; delta < 6; delta += 1) {
        fireEvent.wheel(viewport);
        expected -= 40;
        viewport.scrollTop = expected;
        fireEvent.scroll(viewport);
        expect(activeConversationStore.getState().bySession[SESSION_ID]).toBe(projectionAfterGrowth);
      }
      expect(viewport.scrollTop).toBe(5350 - 240);

      // The next arrival (a new row) holds the accumulated position exactly and pills…
      pinViewport(viewport, 6900, 600);
      act(() => seedItems(11));
      expect(viewport.scrollTop).toBe(5110);
      expect(screen.getByTestId("conversation-new-updates")).not.toBeNull();
      // …and the live scroll memory keeps tracking the position — it is the
      // replacement for the deleted anchor mirror, never conversation authority.
      expect(readConversationScroll(SESSION_ID)).toEqual({ scrollTop: 5110, atBottom: false });

      // A genuine scroll all the way down re-locks: the pill clears and the next growth pins
      // the new end (the follow still owns a locked feed — no regression).
      fireEvent.wheel(viewport);
      viewport.scrollTop = 6300; // 6900 - 600: the exact end
      fireEvent.scroll(viewport);
      expect(screen.queryByTestId("conversation-new-updates")).toBeNull();
      pinViewport(viewport, 7500, 600);
      act(() => seedItems(12));
      expect(viewport.scrollTop).toBe(7500 - 600);
    } finally {
      vi.useRealTimers();
    }
  });
});

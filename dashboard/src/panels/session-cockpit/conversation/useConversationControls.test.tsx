import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { emptyProjection, type ActiveConversationProjection } from "../../../data/conversation/reducer";
import { activeConversationStore } from "../../../data/conversation/store";
import type {
  ActiveConversationRef,
  ConversationItem,
  ConversationStatus,
} from "../../../data/conversation/types";
import {
  capabilitiesWithInterrupt as capabilities,
  conversationIdentity,
  conversationItem,
  conversationStatus,
} from "../../../test/fixtures/conversationWire";
import { resolveWorkingTurnId, useConversationInterrupt } from "./useConversationControls";

const IDENTITY: ActiveConversationRef = conversationIdentity({
  vendorConversationId: "v",
  projectScope: "/r",
  identityDigest: "d",
  arSessionId: "s1",
  bridgeEpoch: "e1",
});

function streamingItem(turnId: string | undefined): ConversationItem {
  return conversationItem({
    itemId: "i1",
    globalOrdinal: 1,
    turnId,
    phase: "streaming",
    blocks: [],
  });
}

function status(turnState: ConversationStatus["turn"]["state"], turnId: string | null): ConversationStatus {
  return conversationStatus({
    identity: IDENTITY,
    freshness: { state: "fresh", lastEvidenceAt: null, ageMs: null, staleAfterMs: 1, observationBound: "poll" },
    process: { state: "connected", generation: "g" },
    turn: { state: turnState, turnId, stateSince: null },
  });
}

function seed(overrides: Partial<ActiveConversationProjection>): void {
  const base = emptyProjection(IDENTITY);
  activeConversationStore.setState({ bySession: { s1: { ...base, ...overrides } } });
}

afterEach(() => {
  activeConversationStore.getState().reset();
  vi.unstubAllGlobals();
});

describe("resolveWorkingTurnId (F1 turn-id correlation)", () => {
  const proj = (over: Partial<ActiveConversationProjection>): ActiveConversationProjection => ({
    ...emptyProjection(IDENTITY),
    ...over,
  });

  it("prefers the canonical status turn id", () => {
    expect(resolveWorkingTurnId(proj({ status: status("working", "canonical") }))).toBe("canonical");
  });

  it("correlates from the newest streaming item's turnId when status omits it (hosted-codex topology)", () => {
    const item = streamingItem("turn-from-item");
    expect(
      resolveWorkingTurnId(proj({ status: status("working", null), orderedItemIds: ["i1"], itemsById: { i1: item } })),
    ).toBe("turn-from-item");
  });

  it("is null when the turn is not working, even if items carry a turnId", () => {
    const item = streamingItem("turn-x");
    expect(
      resolveWorkingTurnId(proj({ status: status("ready", null), orderedItemIds: ["i1"], itemsById: { i1: item } })),
    ).toBeNull();
  });

  it("is null when a working turn's id is genuinely unresolvable", () => {
    expect(resolveWorkingTurnId(proj({ status: status("working", null) }))).toBeNull();
  });
});

describe("useConversationInterrupt (F1 enable / F5 honest reasons)", () => {
  it("ENABLES the stop when working + turn id resolves from item evidence (no stale L1 reason)", () => {
    const item = streamingItem("turn-9");
    seed({
      status: status("working", null),
      orderedItemIds: ["i1"],
      itemsById: { i1: item },
      capabilities: capabilities("unverified"), // the stale L1 view must NOT hide a landed interrupt
    });
    const { result } = renderHook(() => useConversationInterrupt("s1"));
    expect(result.current.available).toBe(true);
    expect(result.current.onStop).toBeTypeOf("function");
    expect(result.current.keyshortcut).toBe("Control+Shift+.");
    // The enabled control carries NO reason — the known-stale L1 `unverified` text must never leak
    // here (F24); WorkingLine renders an honest action tooltip instead.
    expect(result.current.reason).toBeUndefined();
  });

  it("renders visible-disabled with an HONEST reason when a working turn id is unresolvable (never the stale capability text)", () => {
    seed({ status: status("working", null), capabilities: capabilities("unverified") });
    const { result } = renderHook(() => useConversationInterrupt("s1"));
    expect(result.current.available).toBe(false);
    expect(result.current.reason).toBe("turn identity unavailable on this wire");
  });

  it("does not offer a stop when the turn is not working", () => {
    seed({ status: status("ready", null) });
    const { result } = renderHook(() => useConversationInterrupt("s1"));
    expect(result.current.available).toBe(false);
    expect(result.current.onStop).toBeUndefined();
  });

  it("disables with the exact reason when the capability is a hard unavailable", () => {
    const item = streamingItem("turn-9");
    seed({
      status: status("working", null),
      orderedItemIds: ["i1"],
      itemsById: { i1: item },
      capabilities: capabilities("unavailable"),
    });
    const { result } = renderHook(() => useConversationInterrupt("s1"));
    expect(result.current.available).toBe(false);
    expect(result.current.reason).toBe("interrupt unavailable");
  });

  it("a typed refusal disables the stop for THAT turn with the server's exact reason, and a later turn re-enables it (F26/F5a claude refusal path)", async () => {
    // The claude-style refusal: the server returns a typed 422 envelope (no `acknowledgement`), which
    // the client parses as a refusal — never guessed into an accepted interrupt.
    const refusal = { status: "codex-interrupt-refused", detail: "codex interrupt requires app-server ≥ 1.2" };
    const fetchMock = vi.fn().mockResolvedValue({ status: 422, json: async () => refusal });
    vi.stubGlobal("fetch", fetchMock);

    seed({
      status: status("working", null),
      orderedItemIds: ["i1"],
      itemsById: { i1: streamingItem("turn-9") },
      capabilities: capabilities("unverified"),
    });
    const { result } = renderHook(() => useConversationInterrupt("s1"));
    expect(result.current.available).toBe(true);

    // Dispatch the interrupt; the server refuses.
    await act(async () => {
      result.current.onStop?.();
      await Promise.resolve();
    });

    // The refusal disables the control for the current turn, surfacing the server's exact reason —
    // never the stale L1 capability text.
    await waitFor(() => expect(result.current.available).toBe(false));
    expect(result.current.reason).toBe("codex interrupt requires app-server ≥ 1.2");
    expect(result.current.onStop).toBeUndefined();

    // A new working turn clears the turn-scoped refusal and re-enables the stop with no reason.
    act(() => {
      seed({
        status: status("working", null),
        orderedItemIds: ["i2"],
        itemsById: { i2: { ...streamingItem("turn-10"), itemId: "i2" } },
        capabilities: capabilities("unverified"),
      });
    });
    await waitFor(() => expect(result.current.available).toBe(true));
    expect(result.current.reason).toBeUndefined();
  });
});

import { act, cleanup, fireEvent, render, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  activeConversationStore,
  connectConversation,
  focusConversation,
  hasWarmConversation,
  LRU_LIMIT,
  readConversationScroll,
} from "../../data/conversation/store";
import { sessionStore, type OpenSession } from "../../data/sessions";
import {
  readSubmissionAuthority,
  SubmissionLifecycleRouteError,
  type SubmissionAuthorityWire,
  type SubmissionLifecycleTransport,
} from "../../data/submissionLifecycleClient";
import type {
  ActiveConversationRef,
  ConversationItem,
  ConversationPage,
} from "../../data/conversation/types";
import {
  conversationIdentity,
  conversationItem,
  conversationPage,
  conversationStatus,
} from "../../test/fixtures/conversationWire";
import { ChatsStageBody } from "./ChatsStageBody";

// The authority resolve and the conversation orchestration are the two network edges; both are
// mocked at their module boundary (the real stores/hooks stay, so the surface renders honestly).
vi.mock("../../data/submissionLifecycleClient", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../data/submissionLifecycleClient")>();
  return { ...actual, readSubmissionAuthority: vi.fn() };
});

vi.mock("../../data/conversation/store", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../data/conversation/store")>();
  return {
    ...actual,
    connectConversation: vi.fn(),
    focusConversation: vi.fn(),
    hasWarmConversation: vi.fn(() => false),
    touchConversation: vi.fn(),
  };
});

// The legacy-raw PTY branch and the diagnostics drawer's xterm are never under test here — the
// PtySurface stands in as an identifiable node whose IDENTITY proves the layer never unmounts
// and whose props expose the focused seat + hidden flag.
vi.mock("./PtySurface", () => ({
  PtySurface: (props: { focused?: OpenSession; hidden?: boolean }) => (
    <div
      data-testid="mock-pty-surface"
      data-focused={props.focused?.id ?? "none"}
      data-hidden={String(props.hidden ?? false)}
    />
  ),
}));
// AmbientTelemetry fetches on mount/revision; the ambient chips are never asserted here.
vi.mock("./conversation/AmbientTelemetry", () => ({ AmbientTelemetry: () => null }));

const bootingSession = (
  id: string,
  controlState: OpenSession["controlState"] = "starting",
): OpenSession => ({
  id,
  label: id,
  kind: "harness",
  harness: "claude",
  status: "running",
  ...(controlState ? { controlState } : {}),
});

// A legacy-raw terminal seat: no controlState ⇒ never controlled, the PTY is its primary body.
const terminalSession = (id: string): OpenSession => ({
  id,
  label: id,
  kind: "terminal",
  status: "running",
});

function stageElement(session: OpenSession, viewActive = true) {
  return (
    <ChatsStageBody
      focused={session}
      libraryOpen={false}
      onCloseLibrary={() => {}}
      diagnosticsOpen={false}
      onToggleDiagnostics={() => {}}
      onSessionOpened={() => {}}
      viewActive={viewActive}
    />
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(hasWarmConversation).mockReturnValue(false);
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  sessionStore.getState().hydrate([]);
  activeConversationStore.getState().reset();
});

describe("ChatsStageBody fresh-chat boot (260721 D1/D2)", () => {
  it("rides out a slow bridge boot: 503s then 200 resolves the epoch with no fail-loud strip", async () => {
    // The measured fresh-chat boot: submission-authority 503s for ~5–7 s before the bridge
    // listens. The resolve must poll readiness across the bounded window instead of giving up
    // after one retry — and the healthy slow boot must never reach the alarm.
    vi.useFakeTimers();
    const session = bootingSession("boot-slow");
    sessionStore.getState().hydrate([session]);
    const startedAt = Date.now();
    vi.mocked(readSubmissionAuthority).mockImplementation(async () => {
      if (Date.now() - startedAt < 7_000) {
        throw new SubmissionLifecycleRouteError(503, "unavailable", "bridge composing");
      }
      return { bridgeEpoch: "epoch-1" };
    });
    const { queryByTestId } = render(stageElement(session));

    // Mid-boot: quiet resolving — retries are happening, the fail-loud strip is not.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3_000);
    });
    expect(vi.mocked(readSubmissionAuthority).mock.calls.length).toBeGreaterThanOrEqual(2);
    expect(queryByTestId("conversation-retry")).toBeNull();
    expect(vi.mocked(connectConversation)).not.toHaveBeenCalled();

    // The bridge answers at ~7 s: the epoch resolves and the projection connects.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(6_000);
    });
    expect(vi.mocked(connectConversation)).toHaveBeenCalledWith("boot-slow", "epoch-1");
    expect(queryByTestId("conversation-retry")).toBeNull();
    // An authority 200 is the daemon's direct proof native control answered — the gate's
    // catalog row is pre-applied ready (the next poll confirms or replaces it).
    expect(
      sessionStore.getState().sessions.find((row) => row.id === "boot-slow")?.controlState,
    ).toBe("ready");
  });

  it("discovers a ~2.3 s readiness within ~0.5 s of the 200: the tuned poll is 250/500/1000 (cap)", async () => {
    // The coarse 800→2500 ms backoff could sit up to 2.5 s stale on a bridge that
    // had just turned ready. The fine schedule's attempt times are exact: t = 0, then delays
    // 250 → 500 → 1 000 (cap), so a typical ~2–5 s boot is discovered within ≤1 s of the first 200.
    vi.useFakeTimers();
    const session = bootingSession("boot-tuned");
    sessionStore.getState().hydrate([session]);
    const startedAt = Date.now();
    const attemptTimes: number[] = [];
    vi.mocked(readSubmissionAuthority).mockImplementation(async () => {
      const at = Date.now() - startedAt;
      attemptTimes.push(at);
      if (at < 2_300) {
        throw new SubmissionLifecycleRouteError(503, "unavailable", "bridge composing");
      }
      return { bridgeEpoch: "epoch-tuned" };
    });
    const { queryByTestId } = render(stageElement(session));

    // The tuned backoff schedule, exactly: attempts at 0 / 250 / 750 / 1 750 (delays 250, 500,
    // then the 1 000 ms cap), quiet resolving throughout.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1_800);
    });
    expect(attemptTimes).toEqual([0, 250, 750, 1_750]);
    expect(vi.mocked(connectConversation)).not.toHaveBeenCalled();
    expect(queryByTestId("conversation-retry")).toBeNull();

    // The bridge answers at ~2.3 s; the next poll (2.75 s) discovers it — 0.45 s stale, where the
    // old cap could wait 2.5 s. The window and the fail-loud end-state are untouched.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1_000);
    });
    expect(attemptTimes[attemptTimes.length - 1]).toBe(2_750);
    expect(vi.mocked(connectConversation)).toHaveBeenCalledWith("boot-tuned", "epoch-tuned");
    expect(queryByTestId("conversation-retry")).toBeNull();
  });

  it("fails loud only past the readiness bound, then re-drives the connect when the catalog reports ready", async () => {
    vi.useFakeTimers();
    const session = bootingSession("boot-dead");
    sessionStore.getState().hydrate([session]);
    vi.mocked(readSubmissionAuthority).mockRejectedValue(
      new SubmissionLifecycleRouteError(503, "unavailable", "bridge composing"),
    );
    const { getByTestId, queryByTestId, rerender } = render(stageElement(session));

    // Inside the window: quiet resolving, never the alarm.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(29_999);
    });
    expect(queryByTestId("conversation-retry")).toBeNull();
    // Past the 30 s bound the honest end-state lands: the fail-loud strip with a manual retry.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2_500);
    });
    expect(getByTestId("conversation-retry")).toBeDefined();
    expect(vi.mocked(connectConversation)).not.toHaveBeenCalled();

    // Recovery: the catalog flips controlState=ready (the bridge finally came up) — the stage
    // re-drives the connect on that transition instead of staying failed until a manual retry.
    vi.mocked(readSubmissionAuthority).mockResolvedValue({ bridgeEpoch: "epoch-2" });
    rerender(stageElement(bootingSession("boot-dead", "ready")));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(vi.mocked(connectConversation)).toHaveBeenCalledWith("boot-dead", "epoch-2");
    expect(queryByTestId("conversation-retry")).toBeNull();
  });

  it("a terminal 4xx answer fails loud immediately, never masked behind the window", async () => {
    vi.useFakeTimers();
    const session = bootingSession("boot-404");
    sessionStore.getState().hydrate([session]);
    vi.mocked(readSubmissionAuthority).mockRejectedValue(
      new SubmissionLifecycleRouteError(404, "unknown-session", "no such session"),
    );
    const { getByTestId } = render(stageElement(session));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(getByTestId("conversation-retry")).toBeDefined();
    expect(vi.mocked(readSubmissionAuthority)).toHaveBeenCalledTimes(1);
    expect(vi.mocked(connectConversation)).not.toHaveBeenCalled();
  });
});


// ── Per-session keep-alive surfaces ─────────────────────────────────────────────────────────────
// The bug this fixes: switching between chats did not stay at the bottom but jumped to the start
// of the conversation and then tried to jump back to the end — the surface must not be unloaded
// on switch. The stage used to serve one shared (unkeyed)
// surface whose timeline unmounted/remounted per focus switch. These tests pin the keep-alive
// contract: mounted-but-hidden, identity/scroll preserved, bounded, never resurrecting the dead.

function identity(sessionId: string): ActiveConversationRef {
  return conversationIdentity({
    vendorConversationId: "v",
    projectScope: "/r",
    identityDigest: "d",
    arSessionId: sessionId,
    bridgeEpoch: "e1",
  });
}

function feedItem(sessionId: string, ordinal: number): ConversationItem {
  return conversationItem({
    itemId: `${sessionId}-item-${ordinal}`,
    globalOrdinal: ordinal,
    blocks: [
      {
        blockId: `${sessionId}-item-${ordinal}-b`,
        type: "markdown",
        markdown: `${sessionId} message ${ordinal}`,
      },
    ],
  });
}

function page(sessionId: string, itemCount = 3): ConversationPage {
  return conversationPage({
    identity: identity(sessionId),
    items: Array.from({ length: itemCount }, (_, index) => feedItem(sessionId, index + 1)),
    status: conversationStatus({ identity: identity(sessionId) }),
  });
}

function seedWarm(sessionId: string): void {
  activeConversationStore.getState().applyPage(sessionId, page(sessionId), "initial");
}

const flushEffects = () => act(async () => {});

describe("ChatsStageBody keep-alive pool (F-j)", () => {
  it("keeps a switched-away chat mounted but hidden, and re-shows the SAME DOM node with its scroll offset untouched", async () => {
    vi.mocked(hasWarmConversation).mockReturnValue(true);
    const a = bootingSession("chat-a", "ready");
    const b = bootingSession("chat-b", "ready");
    sessionStore.getState().hydrate([a, b]);
    seedWarm("chat-a");
    seedWarm("chat-b");
    const { getByTestId, rerender } = render(stageElement(a));
    await flushEffects();

    const wrapperA = getByTestId("conversation-keepalive-chat-a");
    expect(wrapperA.getAttribute("aria-hidden")).toBeNull(); // focused ⇒ visible
    const viewportA = within(wrapperA).getByTestId("conversation-viewport");
    // The operator had scrolled up in chat-a before switching away.
    viewportA.scrollTop = 456;

    rerender(stageElement(b));
    await flushEffects();
    expect(vi.mocked(focusConversation)).toHaveBeenLastCalledWith("chat-b");
    // chat-a stays MOUNTED (its timeline DOM included) but is hidden + out of the a11y tree;
    // chat-b is the visible surface.
    const hiddenA = getByTestId("conversation-keepalive-chat-a");
    expect(hiddenA.getAttribute("aria-hidden")).toBe("true");
    expect(within(hiddenA).queryByTestId("conversation-viewport")).not.toBeNull();
    expect(getByTestId("conversation-keepalive-chat-b").getAttribute("aria-hidden")).toBeNull();

    rerender(stageElement(a));
    await flushEffects();
    expect(vi.mocked(focusConversation)).toHaveBeenLastCalledWith("chat-a");
    const restoredA = getByTestId("conversation-keepalive-chat-a");
    expect(restoredA.getAttribute("aria-hidden")).toBeNull();
    const restoredViewport = within(restoredA).getByTestId("conversation-viewport");
    // The identity proof: the same DOM node round-tripped — React never unmounted the surface,
    // so nothing could jump to the top and yank back to the bottom.
    expect(restoredViewport).toBe(viewportA);
    expect(restoredViewport.scrollTop).toBe(456);
  });

  it("does not raise the 'N new updates' pill on re-show for a chat that was at the bottom", async () => {
    vi.mocked(hasWarmConversation).mockReturnValue(true);
    const a = bootingSession("chat-a", "ready");
    const b = bootingSession("chat-b", "ready");
    sessionStore.getState().hydrate([a, b]);
    seedWarm("chat-a");
    seedWarm("chat-b");
    const { queryByTestId, rerender } = render(stageElement(a));
    await flushEffects();

    // A fresh surface starts pinned to the bottom (nearBottomRef initial true): new items that
    // arrive while hidden are followed quietly, never counted as a stale "N new updates" pill.
    rerender(stageElement(b));
    await flushEffects();
    act(() => {
      activeConversationStore.getState().applyPage("chat-a", page("chat-a", 5), "initial");
    });
    rerender(stageElement(a));
    await flushEffects();
    expect(queryByTestId("conversation-new-updates")).toBeNull();
  });

  it("bounds the mounted pool by the warm-projection LRU limit and drops the oldest first", async () => {
    vi.mocked(hasWarmConversation).mockReturnValue(true);
    const sessions = Array.from({ length: LRU_LIMIT + 1 }, (_, index) =>
      bootingSession(`chat-${index + 1}`, "ready"),
    );
    sessionStore.getState().hydrate(sessions);
    for (const session of sessions) seedWarm(session.id);

    const { queryByTestId, rerender } = render(stageElement(sessions[0]));
    await flushEffects();
    for (const session of sessions.slice(1)) {
      rerender(stageElement(session));
      await flushEffects();
    }

    // The pool never exceeds the data layer's LRU bound; the least-recently-focused chat (chat-1)
    // is the first to go.
    expect(queryByTestId("conversation-keepalive-chat-1")).toBeNull();
    expect(queryByTestId("conversation-keepalive-chat-2")).not.toBeNull();
    expect(
      queryByTestId(`conversation-keepalive-chat-${LRU_LIMIT + 1}`),
    ).not.toBeNull();
    const mounted = sessions.filter(
      (session) => queryByTestId(`conversation-keepalive-${session.id}`) !== null,
    );
    expect(mounted).toHaveLength(LRU_LIMIT);
  });

  it("drops a kept surface once its session is no longer warm (evicted/terminated) instead of resurrecting it", async () => {
    vi.mocked(hasWarmConversation).mockReturnValue(true);
    const a = bootingSession("chat-a", "ready");
    const b = bootingSession("chat-b", "ready");
    sessionStore.getState().hydrate([a, b]);
    seedWarm("chat-a");
    seedWarm("chat-b");
    const { getByTestId, queryByTestId, rerender } = render(stageElement(a));
    await flushEffects();
    rerender(stageElement(b));
    await flushEffects();
    expect(queryByTestId("conversation-keepalive-chat-a")).not.toBeNull();

    // The data layer disconnects chat-a (LRU eviction or termination both land here): the next
    // warmth-affecting store change must drop its DOM — a dead session's chrome never lingers.
    vi.mocked(hasWarmConversation).mockImplementation((id: string) => id !== "chat-a");
    act(() => {
      activeConversationStore.getState().evict("chat-a");
    });
    await flushEffects();
    expect(queryByTestId("conversation-keepalive-chat-a")).toBeNull();
    expect(getByTestId("conversation-keepalive-chat-b")).not.toBeNull();

    // Refocusing the evicted chat is the ordinary cold connect (full re-resolve), not a reuse of
    // stale DOM — the warm-reuse contract is untouched.
    vi.mocked(readSubmissionAuthority).mockResolvedValue({ bridgeEpoch: "epoch-a2" });
    rerender(stageElement(a));
    await flushEffects();
    // The cold path always re-proves the bridge (refresh: true) — see the cache test below.
    expect(vi.mocked(readSubmissionAuthority)).toHaveBeenCalledWith("chat-a", undefined, {
      refresh: true,
    });
  });
});

// ── Two keep-alive-pool defects: epoch attribution + authority freshness ────────────────────────
// Two defects born where the keep-alive pool meets the warm short-circuit: an epoch
// outcome observed for one chat could paint another chat's surface, and the cold connect could
// assert a live bridge from a TTL-less cache that issued no request at all.

describe("ChatsStageBody epoch attribution (M3)", () => {
  it("lands a cold chat's projection-failed alarm on THAT chat, never on the chat holding the stage", async () => {
    vi.useFakeTimers();
    const a = bootingSession("chat-a", "ready");
    const b = bootingSession("chat-b", "ready");
    sessionStore.getState().hydrate([a, b]);
    seedWarm("chat-a");
    // chat-a is warm (the refocus short-circuit); chat-b is cold and its resolve is still in
    // flight when the operator switches back.
    vi.mocked(hasWarmConversation).mockImplementation((id: string) => id === "chat-a");
    let failColdResolve = (): void => {};
    vi.mocked(readSubmissionAuthority).mockImplementation(
      (id: string) =>
        new Promise<SubmissionAuthorityWire>((_resolve, reject) => {
          failColdResolve = () =>
            reject(new SubmissionLifecycleRouteError(404, "unknown-session", `no bridge for ${id}`));
        }),
    );

    const { getByTestId, queryByTestId, rerender } = render(stageElement(a));
    await flushEffects();
    const surfaceA = within(getByTestId("conversation-keepalive-chat-a")).getByTestId(
      "conversation-viewport",
    );

    rerender(stageElement(b)); // cold connect for chat-b starts; its authority answer stays pending
    await flushEffects();
    rerender(stageElement(a)); // …and the operator switches back before chat-b answers
    await flushEffects();

    // chat-b's resolve answers 404 — terminal, so it escalates to the fail-loud alarm.
    await act(async () => {
      failColdResolve();
      await vi.advanceTimersByTimeAsync(0);
    });

    // The alarm belongs to chat-b. chat-a shows no banner and — the keep-alive contract — its timeline was
    // never swapped out for one (a component-wide phase replaced this exact DOM node with the
    // ConversationReconnect strip, killing the scroll offset with it).
    expect(queryByTestId("conversation-retry")).toBeNull();
    expect(
      within(getByTestId("conversation-keepalive-chat-a")).getByTestId("conversation-viewport"),
    ).toBe(surfaceA);

    // And the alarm is not merely suppressed: chat-b still fails loud on its own surface.
    rerender(stageElement(b));
    await flushEffects();
    await act(async () => {
      failColdResolve();
      await vi.advanceTimersByTimeAsync(0);
    });
    const wrapperB = getByTestId("conversation-keepalive-chat-b");
    expect(within(wrapperB).getByTestId("conversation-reconnect").getAttribute("data-phase")).toBe(
      "projection-failed",
    );
  });

  it("a chat that failed while a terminal seat held the stage never blanks the next chat's surface", async () => {
    // The path no generation bump can close: focusing a legacy-raw seat returns from connect()
    // before any bump, so the cold chat's resolve stays "current" while a terminal owns the stage.
    // Only a per-session phase keeps its failure off the next controlled chat.
    vi.useFakeTimers();
    const cold = bootingSession("chat-cold", "ready");
    const warm = bootingSession("chat-warm", "ready");
    const term = terminalSession("term-t");
    sessionStore.getState().hydrate([cold, warm, term]);
    seedWarm("chat-warm");
    vi.mocked(hasWarmConversation).mockImplementation((id: string) => id === "chat-warm");
    let failColdResolve = (): void => {};
    vi.mocked(readSubmissionAuthority).mockImplementation(
      () =>
        new Promise<SubmissionAuthorityWire>((_resolve, reject) => {
          failColdResolve = () =>
            reject(new SubmissionLifecycleRouteError(404, "unknown-session", "no bridge"));
        }),
    );

    const { getByTestId, queryByTestId, rerender } = render(stageElement(warm));
    await flushEffects();
    const surfaceWarm = within(getByTestId("conversation-keepalive-chat-warm")).getByTestId(
      "conversation-viewport",
    );

    rerender(stageElement(cold)); // cold resolve in flight
    await flushEffects();
    rerender(stageElement(term)); // legacy-raw seat: connect() early-returns, nothing is bumped
    await flushEffects();
    await act(async () => {
      failColdResolve();
      await vi.advanceTimersByTimeAsync(0);
    });

    rerender(stageElement(warm));
    await flushEffects();
    // The identity assertion is the load-bearing one: with a component-wide phase the returning
    // chat renders the alarm for one commit (unmounting its timeline) before its own warm connect
    // flips the phase back to ready on the very next effect — invisible to a banner query, fatal to
    // the kept-alive scroll offset.
    expect(
      within(getByTestId("conversation-keepalive-chat-warm")).getByTestId("conversation-viewport"),
    ).toBe(surfaceWarm);
    expect(queryByTestId("conversation-retry")).toBeNull();
  });
});

describe("ChatsStageBody authority freshness (M9)", () => {
  it("re-proves the bridge on the cold connect instead of asserting ready from the TTL-less cache", async () => {
    // The authority cache is a bare Map: no TTL, invalidated only by bridge-epoch-mismatch
    // handlers. A bridge that simply died leaves a hit behind, and a hit issues NO request — so a
    // cold connect that accepted it would hand connectConversation a dead epoch and patch the
    // catalog row the composer gate reads to "ready" on the strength of a memory.
    const actual = await vi.importActual<typeof import("../../data/submissionLifecycleClient")>(
      "../../data/submissionLifecycleClient",
    );
    actual.clearSubmissionAuthorityCache();
    vi.useFakeTimers();
    let bridgeAlive = true;
    const transportCalls: string[] = [];
    const transport = {
      async authority(sessionId: string): Promise<SubmissionAuthorityWire> {
        transportCalls.push(sessionId);
        if (!bridgeAlive) {
          throw new SubmissionLifecycleRouteError(404, "unknown-session", "bridge is gone");
        }
        return { bridgeEpoch: "epoch-live" };
      },
    } as SubmissionLifecycleTransport;
    vi.mocked(readSubmissionAuthority).mockImplementation((sessionId, _transport, options) =>
      actual.readSubmissionAuthority(sessionId, transport, options),
    );

    // An earlier answer (a submit, a previous connect) populated the process-wide cache…
    await actual.readSubmissionAuthority("chat-cached", transport);
    expect(transportCalls).toEqual(["chat-cached"]);
    // …and then the bridge died. Nothing invalidates the entry.
    bridgeAlive = false;

    const session = bootingSession("chat-cached", "starting");
    sessionStore.getState().hydrate([session]);
    const { getByTestId } = render(stageElement(session));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });

    // The cold connect asked the daemon again and got the truth: fail loud, no projection, and the
    // composer gate's controlState is left exactly as the catalog reported it.
    expect(transportCalls).toEqual(["chat-cached", "chat-cached"]);
    expect(getByTestId("conversation-retry")).toBeDefined();
    expect(vi.mocked(connectConversation)).not.toHaveBeenCalled();
    expect(
      sessionStore.getState().sessions.find((row) => row.id === "chat-cached")?.controlState,
    ).toBe("starting");
    actual.clearSubmissionAuthorityCache();
  });
});

// ── view-switch scroll restore ──────────────────────────────────────────────────────────────────
// The bug this fixes: the active chat did not stick to the scroll position it was left at when
// switching cockpit views — it always went to the start. The keep-alive pool survives CHAT
// switches (visibility:hidden keeps layout), but the cockpit hides the whole Chats layer with
// display:none on a VIEW switch — and display:none destroys the scroll offset. These tests pin the
// contract through the stage's `viewActive` prop: the position is remembered continuously, and
// the re-show restores it — middle stays middle, top stays top, bottom lands at the CURRENT end.

// jsdom has no layout: pin the viewport's scroll geometry so the timeline's bottom-follow math
// (scrollHeight - scrollTop - clientHeight) sees real numbers.
function pinViewportGeometry(viewport: HTMLElement, scrollHeight: number, clientHeight: number): void {
  Object.defineProperty(viewport, "scrollHeight", { configurable: true, value: scrollHeight });
  Object.defineProperty(viewport, "clientHeight", { configurable: true, value: clientHeight });
}

function scrollViewportTo(viewport: HTMLElement, top: number): void {
  // The operator's scroll arrives with trusted input — the intent lock's disengagement is
  // input-gated, so a bare scroll event (content-driven semantics) would never flip the lock.
  fireEvent.wheel(viewport);
  viewport.scrollTop = top;
  fireEvent.scroll(viewport);
}

describe("ChatsStageBody view-switch scroll restore (F-ac)", () => {
  it("restores the exact mid-conversation offset after a view switch away and back — same DOM node, no remount", async () => {
    vi.mocked(hasWarmConversation).mockReturnValue(true);
    const a = bootingSession("chat-a", "ready");
    sessionStore.getState().hydrate([a]);
    seedWarm("chat-a");
    const { getByTestId, rerender } = render(stageElement(a));
    await flushEffects();

    const viewport = getByTestId("conversation-viewport");
    pinViewportGeometry(viewport, 2000, 600);
    // The operator reads mid-conversation; the scroll listener remembers the position per session.
    scrollViewportTo(viewport, 456);
    expect(readConversationScroll("chat-a")).toEqual({ scrollTop: 456, atBottom: false });

    // Away: the cockpit's display:none resets the DOM offset to 0 WITHOUT a scroll event — exactly
    // what the browser does (jsdom has no layout, so the destruction is applied by hand).
    rerender(stageElement(a, false));
    await flushEffects();
    expect(vi.mocked(focusConversation)).toHaveBeenLastCalledWith(null);
    viewport.scrollTop = 0;

    // Back: the remembered offset is restored on the SAME node (the pool never unmounted it).
    rerender(stageElement(a));
    await flushEffects();
    expect(vi.mocked(focusConversation)).toHaveBeenLastCalledWith("chat-a");
    const restored = getByTestId("conversation-viewport");
    expect(restored).toBe(viewport);
    expect(restored.scrollTop).toBe(456);

    // A middle position is not at-bottom: an arrival after the return never yanks — the
    // "N new updates" pill covers it.
    act(() => {
      activeConversationStore.getState().applyPage("chat-a", page("chat-a", 5), "initial");
    });
    await flushEffects();
    expect(restored.scrollTop).toBe(456);
    expect(getByTestId("conversation-new-updates").textContent).toContain("1 new");
  });

  it("a chat left at the top returns to the top and is NOT treated as at-bottom", async () => {
    vi.mocked(hasWarmConversation).mockReturnValue(true);
    const a = bootingSession("chat-a", "ready");
    sessionStore.getState().hydrate([a]);
    seedWarm("chat-a");
    const { getByTestId, rerender } = render(stageElement(a));
    await flushEffects();

    const viewport = getByTestId("conversation-viewport");
    pinViewportGeometry(viewport, 2000, 600);
    // Scrolled all the way up (the mount opened at the latest; the operator went to the start).
    scrollViewportTo(viewport, 0);
    expect(readConversationScroll("chat-a")).toEqual({ scrollTop: 0, atBottom: false });

    rerender(stageElement(a, false));
    await flushEffects();
    viewport.scrollTop = 0; // display:none destruction (already 0 here — pinned for honesty)

    rerender(stageElement(a));
    await flushEffects();
    expect(getByTestId("conversation-viewport").scrollTop).toBe(0);
    // Top is not the bottom band: the next arrival counts into the pill instead of following.
    act(() => {
      activeConversationStore.getState().applyPage("chat-a", page("chat-a", 4), "initial");
    });
    await flushEffects();
    expect(getByTestId("conversation-viewport").scrollTop).toBe(0);
    expect(getByTestId("conversation-new-updates")).not.toBeNull();
  });

  it("a chat left at the bottom returns to the CURRENT end — items arrived while away included — and keeps following", async () => {
    vi.useFakeTimers();
    try {
      vi.mocked(hasWarmConversation).mockReturnValue(true);
      const a = bootingSession("chat-a", "ready");
      sessionStore.getState().hydrate([a]);
      seedWarm("chat-a"); // 3 items
      const { getByTestId, queryByTestId, rerender } = render(stageElement(a));
      await flushEffects();

      const viewport = getByTestId("conversation-viewport");
      pinViewportGeometry(viewport, 2000, 600);
      // jsdom's Element.scrollTo is undefined (tanstack-virtual optional-chains through it); give
      // the viewport a working one so the follow's end-alignment stays observable.
      const alignedTops: number[] = [];
      viewport.scrollTo = (({ top }: { top?: number }) => {
        alignedTops.push(top ?? 0);
        viewport.scrollTop = top ?? 0;
      }) as typeof viewport.scrollTo;

      // Left at the very bottom (2000 - 600 ⇒ distance 0 ≤ the follow band).
      scrollViewportTo(viewport, 1400);
      expect(readConversationScroll("chat-a")).toEqual({ scrollTop: 1400, atBottom: true });

      // Away: the offset is destroyed, and two items arrive while the chat is hidden.
      rerender(stageElement(a, false));
      await flushEffects();
      viewport.scrollTop = 0;
      act(() => {
        activeConversationStore.getState().applyPage("chat-a", page("chat-a", 5), "initial");
      });
      await flushEffects();

      // Back: the armed atBottom restore re-drives the CURRENT DOM end across frames, never
      // a "N new updates" pill — then consumes once the feed settles.
      rerender(stageElement(a));
      await flushEffects();
      await act(async () => {
        await vi.advanceTimersByTimeAsync(250);
      });
      expect(queryByTestId("conversation-new-updates")).toBeNull();
      expect(getByTestId("conversation-viewport").scrollTop).toBe(1400); // 2000 - 600, the pinned end

      // The follow is re-armed: the next arrival is followed quietly too, to the new end.
      alignedTops.length = 0;
      act(() => {
        activeConversationStore.getState().applyPage("chat-a", page("chat-a", 6), "initial");
      });
      await flushEffects();
      expect(queryByTestId("conversation-new-updates")).toBeNull();
      expect(getByTestId("conversation-viewport").scrollTop).toBe(1400);
      // This fixture keeps scrollHeight fixed, so the current end is already 1400. The follow stays
      // armed without issuing TanStack's former no-op scroll/reconcile cycle.
      expect(alignedTops).toEqual([]);
    } finally {
      vi.useRealTimers();
    }
  });

  it("never lets the display:none collapse clobber the memory — the browser fires a scroll event on the box-less element (live Playwright repro)", async () => {
    // Live verification (Playwright, real browser): chat left at scrollTop=400 → Chats→Operations
    // →Chats settled at 0 and STAYED. The display:none teardown collapses the box (st=0/sh=0/ch=0)
    // and the browser CAN dispatch a scroll event for the clamp — the timeline must not report
    // that bogus geometry over the remembered position.
    vi.mocked(hasWarmConversation).mockReturnValue(true);
    const a = bootingSession("chat-a", "ready");
    sessionStore.getState().hydrate([a]);
    seedWarm("chat-a");
    const { getByTestId, rerender } = render(stageElement(a));
    await flushEffects();

    const viewport = getByTestId("conversation-viewport");
    pinViewportGeometry(viewport, 2000, 600);
    scrollViewportTo(viewport, 400);
    expect(readConversationScroll("chat-a")).toEqual({ scrollTop: 400, atBottom: false });

    // Away: the layer goes display:none. Mid/after teardown the element is box-less and its
    // offset is 0 — the collapse's clamp event must be ignored.
    rerender(stageElement(a, false));
    await flushEffects();
    pinViewportGeometry(viewport, 0, 0);
    viewport.scrollTop = 0;
    fireEvent.scroll(viewport);
    expect(readConversationScroll("chat-a")).toEqual({ scrollTop: 400, atBottom: false });
    // A mid-teardown variant with the geometry still reading non-zero must be ignored too — a
    // hidden timeline never reports, regardless of what the box claims.
    pinViewportGeometry(viewport, 2000, 600);
    viewport.scrollTop = 0;
    fireEvent.scroll(viewport);
    expect(readConversationScroll("chat-a")).toEqual({ scrollTop: 400, atBottom: false });

    // Back: the remembered 400 is restored (before the guard this was the clobbered 0).
    rerender(stageElement(a));
    await flushEffects();
    expect(getByTestId("conversation-viewport").scrollTop).toBe(400);
  });
});

// ── The PTY layer is a persistent sibling of the conversation pool ──────────────────────────────
// Switching from a harness seat to a terminal seat and back brought the
// redraw/rescroll glitch right back. The stage used to render EITHER PtySurface (terminal seats)
// OR the keep-alive conversation pool (harness seats) — mutually exclusive subtrees — so the
// archetype switch UNMOUNTED the whole PTY stack (xterm dispose + socket teardown) and the return
// paid full boot. Both layers now stay mounted; the non-matching one hides with the keptHidden
// pattern (visibility + aria-hidden). These tests pin the contract: layer persistence by DOM-node
// identity (the PtySurface/Terminal instances — and with them xterm + the socket — never unmount),
// hidden-layer a11y, lazy-on-first-terminal-focus, and the cols-truth reset.
describe("ChatsStageBody B1: persistent conversation + PTY layers", () => {
  it("harness → terminal → harness → terminal keeps the SAME PTY stack and conversation mounted — only layer visibility flips", async () => {
    vi.mocked(hasWarmConversation).mockReturnValue(true);
    const harness = bootingSession("chat-h", "ready");
    const terminal = terminalSession("term-t");
    sessionStore.getState().hydrate([harness, terminal]);
    seedWarm("chat-h");
    const { getByTestId, queryByTestId, rerender } = render(stageElement(harness));
    await flushEffects();

    // Harness only: NO pty layer at all — the stack is lazy-on-first-terminal-focus.
    expect(queryByTestId("pty-layer")).toBeNull();
    expect(queryByTestId("mock-pty-surface")).toBeNull();
    const conversationNode = getByTestId("conversation-keepalive-chat-h");

    // → terminal: the layer mounts for the focused seat; the conversation hides but stays mounted.
    rerender(stageElement(terminal));
    await flushEffects();
    expect(getByTestId("chats-stage-body").getAttribute("data-mode")).toBe("legacy-raw");
    expect(getByTestId("conversation-layer").getAttribute("aria-hidden")).toBe("true");
    expect(getByTestId("conversation-keepalive-chat-h")).toBe(conversationNode);
    const ptyLayer = getByTestId("pty-layer");
    expect(ptyLayer.getAttribute("aria-hidden")).toBeNull();
    const ptyNode = getByTestId("mock-pty-surface");
    expect(ptyNode.getAttribute("data-focused")).toBe("term-t");
    expect(ptyNode.getAttribute("data-hidden")).toBe("false");

    // → harness: THE FIX — the whole PTY stack survives the archetype switch (same DOM node ⇒
    // the PtySurface instance, its Terminal children, xterm, and the socket were never disposed),
    // hidden with the keptHidden pattern (aria-hidden, out of the a11y tree).
    rerender(stageElement(harness));
    await flushEffects();
    expect(getByTestId("chats-stage-body").getAttribute("data-mode")).toBe("active-conversation");
    expect(getByTestId("pty-layer").getAttribute("aria-hidden")).toBe("true");
    expect(getByTestId("mock-pty-surface")).toBe(ptyNode);
    expect(getByTestId("mock-pty-surface").getAttribute("data-hidden")).toBe("true");
    expect(getByTestId("conversation-layer").getAttribute("aria-hidden")).toBeNull();
    expect(getByTestId("conversation-keepalive-chat-h")).toBe(conversationNode);

    // → terminal again: the same stack re-shows — no boot, no recreate, no scrollback replay.
    rerender(stageElement(terminal));
    await flushEffects();
    expect(getByTestId("mock-pty-surface")).toBe(ptyNode);
    expect(getByTestId("mock-pty-surface").getAttribute("data-hidden")).toBe("false");
    expect(getByTestId("pty-layer").getAttribute("aria-hidden")).toBeNull();
    expect(getByTestId("conversation-keepalive-chat-h")).toBe(conversationNode);
  });

  it("freezes the hidden terminal at its last visible box, then accepts a real browser resize on re-show", async () => {
    vi.mocked(hasWarmConversation).mockReturnValue(true);
    const harness = bootingSession("chat-h", "ready");
    const terminal = terminalSession("term-t");
    sessionStore.getState().hydrate([harness, terminal]);
    seedWarm("chat-h");
    let ptyWidth = 800;
    let ptyHeight = 400;
    const rectSpy = vi
      .spyOn(HTMLElement.prototype, "getBoundingClientRect")
      .mockImplementation(function (this: HTMLElement) {
        if (this.dataset.testid === "pty-layer") {
          return new DOMRect(0, 0, ptyWidth, ptyHeight);
        }
        return new DOMRect();
      });

    try {
      const { getByTestId, rerender } = render(stageElement(harness));
      await flushEffects();

      rerender(stageElement(terminal));
      await flushEffects();
      // The terminal is visible/fluid and records its real box.
      expect(getByTestId("pty-layer").style.height).toBe("");

      rerender(stageElement(harness));
      await flushEffects();
      // Harness-only chrome appeared, but the hidden terminal keeps the exact box it had while
      // visible instead of shrinking behind the composer.
      expect(getByTestId("pty-layer").style.width).toBe("800px");
      expect(getByTestId("pty-layer").style.height).toBe("400px");

      // A browser resize while the terminal is hidden does not resize/refit it in the background.
      ptyWidth = 900;
      ptyHeight = 500;
      expect(getByTestId("pty-layer").style.width).toBe("800px");
      expect(getByTestId("pty-layer").style.height).toBe("400px");

      // Re-show releases the frozen box. The real 900×500 layout is measured once and becomes the
      // next hidden size — browser resizing remains supported without focus-switch churn.
      rerender(stageElement(terminal));
      await flushEffects();
      expect(getByTestId("pty-layer").style.width).toBe("");
      expect(getByTestId("pty-layer").style.height).toBe("");
      rerender(stageElement(harness));
      await flushEffects();
      expect(getByTestId("pty-layer").style.width).toBe("900px");
      expect(getByTestId("pty-layer").style.height).toBe("500px");
    } finally {
      rectSpy.mockRestore();
    }
  });

  it("terminal → harness does not arm scroll restoration for the visibility-hidden conversation", async () => {
    vi.useFakeTimers();
    const harness = bootingSession("chat-h", "ready");
    const terminal = terminalSession("term-t");
    sessionStore.getState().hydrate([harness, terminal]);
    vi.mocked(hasWarmConversation).mockReturnValue(true);
    seedWarm("chat-h");
    const { getByTestId, rerender } = render(stageElement(harness));
    await flushEffects();

    const viewport = getByTestId("conversation-viewport");
    pinViewportGeometry(viewport, 2_000, 600);
    scrollViewportTo(viewport, 1_400);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(250);
    });

    // Observe direct scrollTop writes after the initial mount driver has settled. The keep-alive
    // surface retains a real box under visibility:hidden, so neither focus edge needs a restore.
    let scrollTop = 1_400;
    const writes: number[] = [];
    Object.defineProperty(viewport, "scrollTop", {
      configurable: true,
      get: () => scrollTop,
      set: (value: number) => {
        writes.push(value);
        scrollTop = value;
      },
    });

    rerender(stageElement(terminal));
    await flushEffects();
    expect(getByTestId("conversation-layer").getAttribute("aria-hidden")).toBe("true");

    rerender(stageElement(harness));
    await flushEffects();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(250);
    });

    expect(getByTestId("conversation-viewport")).toBe(viewport);
    expect(viewport.scrollTop).toBe(1_400);
    expect(writes).toEqual([]);
  });

  it("never-focused terminal seats mount nothing — the layer is lazy-on-first-focus", async () => {
    vi.mocked(hasWarmConversation).mockReturnValue(true);
    const harness = bootingSession("chat-h", "ready");
    const termA = terminalSession("term-a");
    const termB = terminalSession("term-b");
    sessionStore.getState().hydrate([harness, termA, termB]);
    seedWarm("chat-h");
    const { getByTestId, queryByTestId, rerender } = render(stageElement(harness));
    await flushEffects();
    expect(queryByTestId("pty-layer")).toBeNull();
    expect(queryByTestId("mock-pty-surface")).toBeNull();

    // Focusing term-a mounts the layer for term-a only; term-b's PTY never exists.
    rerender(stageElement(termA));
    await flushEffects();
    expect(getByTestId("mock-pty-surface").getAttribute("data-focused")).toBe("term-a");
    expect(getByTestId("mock-pty-surface").getAttribute("data-hidden")).toBe("false");
  });

  it("terminal ↔ terminal keeps the same layer visible and follows focus (no hide in between)", async () => {
    const t1 = terminalSession("term-1");
    const t2 = terminalSession("term-2");
    sessionStore.getState().hydrate([t1, t2]);
    const { getByTestId, rerender } = render(stageElement(t1));
    await flushEffects();
    const layer = getByTestId("pty-layer");
    expect(layer.getAttribute("aria-hidden")).toBeNull();
    expect(getByTestId("mock-pty-surface").getAttribute("data-focused")).toBe("term-1");

    rerender(stageElement(t2));
    await flushEffects();
    expect(getByTestId("pty-layer")).toBe(layer);
    expect(getByTestId("pty-layer").getAttribute("aria-hidden")).toBeNull();
    expect(getByTestId("mock-pty-surface").getAttribute("data-focused")).toBe("term-2");
  });

  it("the visible-cols truth resets to null while the terminal layer is hidden (floor chip falls back to the pixel estimate)", async () => {
    const onVisibleCols = vi.fn();
    const harness = bootingSession("chat-h", "ready");
    const terminal = terminalSession("term-t");
    sessionStore.getState().hydrate([harness, terminal]);
    vi.mocked(hasWarmConversation).mockReturnValue(true);
    seedWarm("chat-h");
    const element = (session: OpenSession) => (
      <ChatsStageBody
        focused={session}
        onVisibleCols={onVisibleCols}
        libraryOpen={false}
        onCloseLibrary={() => {}}
        diagnosticsOpen={false}
        onToggleDiagnostics={() => {}}
        onSessionOpened={() => {}}
      />
    );
    const { rerender } = render(element(terminal));
    await flushEffects();
    expect(onVisibleCols).not.toHaveBeenCalledWith(null);

    rerender(element(harness));
    await flushEffects();
    expect(onVisibleCols).toHaveBeenCalledWith(null);
  });
});

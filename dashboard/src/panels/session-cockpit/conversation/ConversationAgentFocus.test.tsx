// ConversationSurface sub-agent focus: ArrowDown ANYWHERE on the surface (feed article AND
// scroll viewport) moves focus INTO the agents line and Enter opens the agent menu (the
// primary path); ArrowUp from the line returns focus to the timeline; ArrowLeft/ArrowRight
// cycle parent → agent 1 → … → agent N → parent as an additional path, Escape returns to the
// parent, the timeline filters to the focused lane (parent items + roster rows vs. one agent's
// items), and every switch is announced politely. A stored focus naming an agent the roster no
// longer carries (an LRU-evicted, rehydrated projection) recomputes to the parent — never
// re-applied blindly.

import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { announceAssertive, announcePolite } from "../../../data/announcer";
import type { ActiveConversationProjection } from "../../../data/conversation/reducer";
import { emptyProjection } from "../../../data/conversation/reducer";
import { activeConversationStore } from "../../../data/conversation/store";
import type {
  ActiveConversationRef,
  ConversationItem,
  ConversationStatus,
} from "../../../data/conversation/types";
import { ConversationSurface } from "./ConversationSurface";
import { OPERATOR_SCROLL_KEYS } from "./ConversationTimeline";

vi.mock("../../../data/announcer", () => ({
  announcePolite: vi.fn(),
  announceAssertive: vi.fn(),
}));
// AmbientTelemetry fetches on mount/revision; the ambient chips are never asserted here.
vi.mock("./AmbientTelemetry", () => ({ AmbientTelemetry: () => null }));

const SESSION_ID = "agent-focus-session";

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

function status(): ConversationStatus {
  return {
    identity: identity(),
    revision: 1,
    observedAt: "2026-07-21T00:00:00Z",
    freshness: {
      state: "fresh",
      lastEvidenceAt: null,
      ageMs: null,
      staleAfterMs: 1,
      observationBound: "poll",
    },
    process: { state: "connected", generation: "g" },
    turn: { state: "ready", turnId: null, stateSince: null },
    evidence: { strength: "exact", origin: "codex" },
  };
}

function item(overrides: Partial<ConversationItem> & { itemId: string; globalOrdinal: number }): ConversationItem {
  return {
    revision: 1,
    lane: "harness",
    source: "harness-live",
    provenance: { strength: "exact", origin: "codex" },
    role: "assistant",
    kind: "message",
    phase: "completed",
    blocks: [{ blockId: `${overrides.itemId}-b`, type: "text", text: overrides.itemId }],
    ...overrides,
  };
}

const PARENT_1 = item({ itemId: "parent-1", globalOrdinal: 1, role: "user" });
const PARENT_2 = item({ itemId: "parent-2", globalOrdinal: 2 });
const ROSTER = item({
  itemId: "codex-agent-t-1",
  globalOrdinal: 3,
  role: "system",
  kind: "notice",
  agent: { agentId: "t-1", nickname: "scout", status: "running" },
});
const AGENT_ITEM = item({
  itemId: "agent-msg-1",
  globalOrdinal: 4,
  agent: { agentId: "t-1", nickname: "scout", status: "running" },
});
const ALL_ITEMS = [PARENT_1, PARENT_2, ROSTER, AGENT_ITEM];

function seed(items: ConversationItem[] = ALL_ITEMS): void {
  const projection: ActiveConversationProjection = {
    ...emptyProjection(identity()),
    stream: "live",
    status: status(),
    lastAppliedDelivery: "live",
    orderedItemIds: items.map((entry) => entry.itemId),
    itemsById: Object.fromEntries(items.map((entry) => [entry.itemId, entry])),
  };
  activeConversationStore.setState((state) => ({
    bySession: { ...state.bySession, [SESSION_ID]: projection },
  }));
}

function surface() {
  return <ConversationSurface sessionId={SESSION_ID} onRetry={() => {}} onShowDiagnostics={() => {}} />;
}

function articleIds(): string[] {
  return [...document.querySelectorAll<HTMLElement>("[data-row-key]")].map(
    (el) => el.dataset.rowKey ?? "",
  );
}

describe("ConversationSurface agent focus", () => {
  // jsdom has no layout: pin a fixed geometry so the virtualizer renders rows.
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
    cleanup();
    activeConversationStore.getState().reset();
    vi.clearAllMocks();
  });

  it("parent view shows parent items + roster rows; the agents area stays one compact line", () => {
    seed();
    render(surface());

    expect(articleIds().sort()).toEqual(["codex-agent-t-1", "parent-1", "parent-2"]);
    expect(screen.getByTestId("conversation-agents-line").textContent).toContain("1 agent · 1 running");
    // The roster itself lives in the menu — the line never grows per-agent rows.
    expect(screen.queryByTestId("conversation-agent-option")).toBeNull();
    expect(screen.queryByTestId("conversation-agent-focus-note")).toBeNull();

    fireEvent.keyDown(screen.getByTestId("conversation-agents-line"), { key: "Enter" });
    expect(screen.getByTestId("conversation-agent-label").textContent).toBe("scout");
  });

  it("ArrowDown from the timeline moves focus into the agents line; Enter opens the menu; Enter selects", () => {
    seed();
    render(surface());
    const article = document.querySelector<HTMLElement>("[data-row-key='parent-1']");
    expect(article).not.toBeNull();

    fireEvent.keyDown(article as HTMLElement, { key: "ArrowDown" });
    const line = screen.getByTestId("conversation-agents-line");
    expect(document.activeElement).toBe(line);
    // Focus moved — the view did NOT switch yet.
    expect(activeConversationStore.getState().agentFocusBySession[SESSION_ID]).toBeUndefined();

    fireEvent.keyDown(line, { key: "Enter" });
    const menu = screen.getByTestId("conversation-agents-menu");
    expect(document.activeElement).toBe(menu);
    expect(announcePolite).not.toHaveBeenCalled();

    fireEvent.keyDown(menu, { key: "Enter" }); // the only agent is the initial active option
    expect(activeConversationStore.getState().agentFocusBySession[SESSION_ID]).toBe("t-1");
    expect(announcePolite).toHaveBeenCalledWith("viewing scout");
    expect(screen.queryByTestId("conversation-agents-menu")).toBeNull();
    expect(document.activeElement).toBe(line);
    // Agent view: the agent's own items — its roster row included — never the parent's.
    expect(articleIds().sort()).toEqual(["agent-msg-1", "codex-agent-t-1"]);
    expect(screen.getByTestId("conversation-agent-focus-note").textContent).toContain("scout");
  });

  it("ArrowDown from the scroll viewport ALSO moves focus into the agents line (uniform hijack)", () => {
    seed();
    render(surface());
    // The viewport origin used to fall through to a native scroll; the hijack is uniform now.
    fireEvent.keyDown(screen.getByTestId("conversation-viewport"), { key: "ArrowDown" });
    expect(document.activeElement).toBe(screen.getByTestId("conversation-agents-line"));
    expect(activeConversationStore.getState().agentFocusBySession[SESSION_ID]).toBeUndefined();
  });

  it("ArrowUp from the agents line returns focus to the timeline's tabbable row", () => {
    seed();
    render(surface());
    const line = screen.getByTestId("conversation-agents-line");
    const tabbableRow = document.querySelector<HTMLElement>("[data-conversation-item][tabindex='0']");
    expect(tabbableRow).not.toBeNull();

    fireEvent.keyDown(screen.getByTestId("conversation-viewport"), { key: "ArrowDown" });
    expect(document.activeElement).toBe(line);
    fireEvent.keyDown(line, { key: "ArrowUp" });
    expect(document.activeElement).toBe(tabbableRow);
  });

  it("the feed no longer documents ArrowDown as a scroll key (PageDown/]/wheel remain)", () => {
    expect(OPERATOR_SCROLL_KEYS.has("ArrowDown")).toBe(false);
    expect(OPERATOR_SCROLL_KEYS.has("PageDown")).toBe(true);
    expect(OPERATOR_SCROLL_KEYS.has("]")).toBe(true);
  });

  it("ArrowRight focuses the agent, filters the timeline, and announces; Escape returns", () => {
    seed();
    render(surface());
    const root = screen.getByTestId("conversation-surface");

    fireEvent.keyDown(root, { key: "ArrowRight" });
    expect(activeConversationStore.getState().agentFocusBySession[SESSION_ID]).toBe("t-1");
    expect(announcePolite).toHaveBeenCalledWith("viewing scout");
    // Agent view: the agent's own items — its roster row included — never the parent's.
    expect(articleIds().sort()).toEqual(["agent-msg-1", "codex-agent-t-1"]);
    expect(screen.getByTestId("conversation-agent-focus-note").textContent).toContain("scout");

    fireEvent.keyDown(root, { key: "Escape" });
    expect(activeConversationStore.getState().agentFocusBySession[SESSION_ID]).toBeUndefined();
    expect(announcePolite).toHaveBeenCalledWith("viewing parent conversation");
    expect(articleIds().sort()).toEqual(["codex-agent-t-1", "parent-1", "parent-2"]);
  });

  it("ArrowRight from the last agent wraps to the parent; ArrowLeft from the parent wraps to agent N", () => {
    seed();
    render(surface());
    const root = screen.getByTestId("conversation-surface");

    fireEvent.keyDown(root, { key: "ArrowLeft" });
    expect(activeConversationStore.getState().agentFocusBySession[SESSION_ID]).toBe("t-1");

    fireEvent.keyDown(root, { key: "ArrowRight" });
    expect(activeConversationStore.getState().agentFocusBySession[SESSION_ID]).toBeUndefined();
  });

  it("the back-to-parent affordance returns to the parent view", () => {
    seed();
    render(surface());
    fireEvent.keyDown(screen.getByTestId("conversation-surface"), { key: "ArrowRight" });
    fireEvent.click(screen.getByTestId("conversation-back-to-parent"));
    expect(activeConversationStore.getState().agentFocusBySession[SESSION_ID]).toBeUndefined();
    expect(articleIds().sort()).toEqual(["codex-agent-t-1", "parent-1", "parent-2"]);
  });

  it("ignores the focus keys from interactive/editable targets", () => {
    seed();
    render(surface());
    // A button (the agents line) owns its keys — arrows there must not cycle the focus.
    fireEvent.keyDown(screen.getByTestId("conversation-agents-line"), { key: "ArrowRight" });
    expect(activeConversationStore.getState().agentFocusBySession[SESSION_ID]).toBeUndefined();
  });

  it("recomputes a stale stored focus (agent gone after rehydrate) to the parent", () => {
    activeConversationStore.getState().setAgentFocus(SESSION_ID, "evicted-agent");
    seed();
    render(surface());

    expect(screen.queryByTestId("conversation-agent-focus-note")).toBeNull();
    expect(articleIds().sort()).toEqual(["codex-agent-t-1", "parent-1", "parent-2"]);
  });

  it("never voices a focus switch from a hidden keep-alive surface", () => {
    seed();
    render(
      <ConversationSurface
        sessionId={SESSION_ID}
        visible={false}
        onRetry={() => {}}
        onShowDiagnostics={() => {}}
      />,
    );
    act(() => {
      fireEvent.keyDown(screen.getByTestId("conversation-surface"), { key: "ArrowRight" });
    });
    expect(activeConversationStore.getState().agentFocusBySession[SESSION_ID]).toBe("t-1");
    expect(announcePolite).not.toHaveBeenCalled();
    expect(announceAssertive).not.toHaveBeenCalled();
  });
});

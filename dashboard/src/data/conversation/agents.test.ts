// Sub-agent roster derivation + timeline focus model. The roster/focus
// vocabulary is projection-evidence only: roster rows come from the notice/system/agent
// shape, labels fall back through bound identity evidence, and the focus cycle recomputes a stale
// survivor of an LRU eviction back to the parent instead of trusting it.

import { afterEach, describe, expect, it } from "vitest";

import {
  agentLabel,
  cycleAgentFocus,
  deriveAgents,
  effectiveAgentFocus,
  filterItemsForFocus,
  isAgentRosterItem,
  shortAgentId,
} from "./agents";
import { emptyProjection } from "./reducer";
import { activeConversationStore } from "./store";
import type { ConversationAgentRef, ConversationItem } from "./types";

function agent(overrides: Partial<ConversationAgentRef> & { agentId: string }): ConversationAgentRef {
  return { status: "running", ...overrides };
}

function item(overrides: Partial<ConversationItem> & { itemId: string }): ConversationItem {
  return {
    revision: 1,
    globalOrdinal: 1,
    lane: "harness",
    source: "harness-live",
    provenance: { strength: "exact", origin: "codex" },
    role: "assistant",
    kind: "message",
    phase: "completed",
    blocks: [],
    ...overrides,
  };
}

function rosterItem(
  agentRef: ConversationAgentRef,
  blocks: ConversationItem["blocks"] = [],
): ConversationItem {
  return item({
    itemId: `codex-agent-${agentRef.agentId}`,
    role: "system",
    kind: "notice",
    agent: agentRef,
    blocks,
  });
}

describe("isAgentRosterItem", () => {
  it("detects exactly the roster shape (notice + system + agent)", () => {
    expect(isAgentRosterItem(rosterItem(agent({ agentId: "t-1" })))).toBe(true);
    // A notice without an agent ref is an ordinary notice.
    expect(
      isAgentRosterItem(item({ itemId: "n-1", role: "system", kind: "notice" })),
    ).toBe(false);
    // An agent-tagged message is an agent item, not a roster row.
    expect(
      isAgentRosterItem(item({ itemId: "m-1", agent: agent({ agentId: "t-1" }) })),
    ).toBe(false);
    // An assistant-role notice with an agent ref is not the roster shape.
    expect(
      isAgentRosterItem(item({ itemId: "n-2", kind: "notice", agent: agent({ agentId: "t-1" }) })),
    ).toBe(false);
  });

  it("does not count child history or rebound system notices as roster seats", () => {
    expect(
      isAgentRosterItem(
        item({
          itemId: "agent-history:t-1",
          role: "system",
          kind: "notice",
          agent: agent({ agentId: "t-1" }),
        }),
      ),
    ).toBe(false);
    expect(
      isAgentRosterItem(
        item({
          itemId: "t-1:parent-system-notice",
          role: "system",
          kind: "notice",
          agent: agent({ agentId: "root" }),
        }),
      ),
    ).toBe(false);
  });
});

describe("agentLabel", () => {
  it("prefers nickname, then role, then the last agentPath segment, then agent <short-id>", () => {
    expect(
      agentLabel(agent({ agentId: "t-1", nickname: "scout", role: "explorer" })),
    ).toBe("scout");
    expect(agentLabel(agent({ agentId: "t-1", role: "explorer" }))).toBe("explorer");
    expect(agentLabel(agent({ agentId: "t-1", agentPath: "/tmp/agents/worker.md" }))).toBe(
      "worker.md",
    );
    expect(agentLabel(agent({ agentId: "abcdef1234567890" }))).toBe("agent abcdef12");
    expect(shortAgentId("abcdef1234567890")).toBe("abcdef12");
  });
});

describe("deriveAgents", () => {
  it("builds one row per roster agent, in evidence order, ignoring non-roster items", () => {
    const items = [
      item({ itemId: "user-1", role: "user" }),
      rosterItem(agent({ agentId: "t-1", nickname: "scout", status: "running" })),
      item({ itemId: "agent-msg", agent: agent({ agentId: "t-1", status: "running" }) }),
      rosterItem(agent({ agentId: "t-2", role: "reviewer", status: "registered" })),
    ];
    expect(deriveAgents(items)).toEqual([
      { agentId: "t-1", label: "scout", status: "running", finalMessage: undefined },
      { agentId: "t-2", label: "reviewer", status: "registered", finalMessage: undefined },
    ]);
  });

  it("surfaces the final-message preview only for a terminal roster row", () => {
    const running = rosterItem(agent({ agentId: "t-1", status: "running" }), [
      { blockId: "final-message", type: "text", text: "partial draft" },
    ]);
    expect(deriveAgents([running])[0]?.finalMessage).toBeUndefined();

    const done = rosterItem(agent({ agentId: "t-1", status: "completed" }), [
      { blockId: "final-message", type: "text", text: "the final report" },
    ]);
    expect(deriveAgents([done])[0]?.finalMessage).toBe("the final report");

    // The claude task_notification terminal summary is equally a report preview.
    const claudeDone = rosterItem(agent({ agentId: "task-9", status: "completed" }), [
      { blockId: "summary", type: "text", text: "claude summary" },
    ]);
    expect(deriveAgents([claudeDone])[0]?.finalMessage).toBe("claude summary");
  });
});

describe("cycleAgentFocus", () => {
  const ids = ["a-1", "a-2"];

  it("cycles parent → agent 1 → agent 2 → parent on ArrowRight", () => {
    const first = cycleAgentFocus(null, ids, 1);
    expect(first).toBe("a-1");
    expect(cycleAgentFocus(first, ids, 1)).toBe("a-2");
    expect(cycleAgentFocus("a-2", ids, 1)).toBeNull();
  });

  it("cycles parent → agent N → … → parent on ArrowLeft", () => {
    expect(cycleAgentFocus(null, ids, -1)).toBe("a-2");
    expect(cycleAgentFocus("a-2", ids, -1)).toBe("a-1");
    expect(cycleAgentFocus("a-1", ids, -1)).toBeNull();
  });

  it("treats a stale focus (agent gone from the roster) as the parent", () => {
    expect(cycleAgentFocus("gone", ids, 1)).toBe("a-1");
    expect(cycleAgentFocus("gone", ids, -1)).toBe("a-2");
    expect(cycleAgentFocus(null, [], 1)).toBeNull();
  });
});

describe("effectiveAgentFocus", () => {
  it("keeps a roster-backed focus and recomputes an unknown one to the parent", () => {
    const agents = deriveAgents([rosterItem(agent({ agentId: "t-1" }))]);
    expect(effectiveAgentFocus("t-1", agents)).toBe("t-1");
    expect(effectiveAgentFocus("evicted-agent", agents)).toBeNull();
    expect(effectiveAgentFocus(null, agents)).toBeNull();
    expect(effectiveAgentFocus(undefined, agents)).toBeNull();
  });
});

describe("filterItemsForFocus", () => {
  const parentMessage = item({ itemId: "p-1", role: "user" });
  const roster = rosterItem(agent({ agentId: "t-1", status: "completed" }));
  const agentMessage = item({ itemId: "a-m-1", agent: agent({ agentId: "t-1" }) });
  const otherAgentMessage = item({ itemId: "a-m-2", agent: agent({ agentId: "t-2" }) });
  const items = [parentMessage, roster, agentMessage, otherAgentMessage];

  it("parent view keeps parent items + roster rows, never agent-tagged items", () => {
    expect(filterItemsForFocus(items, null).map((entry) => entry.itemId)).toEqual([
      "p-1",
      roster.itemId,
    ]);
  });

  it("agent view keeps only that agent's items (its roster row included)", () => {
    expect(filterItemsForFocus(items, "t-1").map((entry) => entry.itemId)).toEqual([
      roster.itemId,
      "a-m-1",
    ]);
    expect(filterItemsForFocus(items, "t-2").map((entry) => entry.itemId)).toEqual(["a-m-2"]);
  });
});

describe("agent focus in the conversation store", () => {
  afterEach(() => {
    activeConversationStore.getState().reset();
  });

  it("setAgentFocus records/clears the focus OUTSIDE the projection so an LRU eviction keeps it", () => {
    const store = activeConversationStore.getState();
    store.setAgentFocus("s-1", "t-1");
    expect(activeConversationStore.getState().agentFocusBySession["s-1"]).toBe("t-1");

    // LRU eviction drops the projection but never the operator's focus — the surface recomputes
    // it against the rehydrated roster (effectiveAgentFocus) instead of re-applying it blindly.
    activeConversationStore.setState((state) => ({
      bySession: {
        ...state.bySession,
        "s-1": emptyProjection({
          harnessId: "codex",
          vendorConversationId: "v",
          projectScope: "/r",
          identityDigest: "d",
          arSessionId: "s-1",
          bridgeEpoch: "e",
        }),
      },
    }));
    activeConversationStore.getState().evict("s-1");
    expect(activeConversationStore.getState().bySession["s-1"]).toBeUndefined();
    expect(activeConversationStore.getState().agentFocusBySession["s-1"]).toBe("t-1");

    activeConversationStore.getState().setAgentFocus("s-1", null);
    expect(activeConversationStore.getState().agentFocusBySession["s-1"]).toBeUndefined();
  });

  it("reset clears the focus map with the rest of the store", () => {
    activeConversationStore.getState().setAgentFocus("s-1", "t-1");
    activeConversationStore.getState().reset();
    expect(activeConversationStore.getState().agentFocusBySession).toEqual({});
  });
});

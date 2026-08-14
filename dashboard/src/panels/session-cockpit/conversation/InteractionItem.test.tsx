// InteractionItem agent badge: an interaction-lane item carrying an agent
// ref (a sub-agent's multiplexed approval request) badges WHO is asking, from the bound evidence
// only; a parent-conversation interaction stays unbadged.

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import type { ConversationItem } from "../../../data/conversation/types";
import { InteractionItem } from "./InteractionItem";

function interactionItem(agent?: ConversationItem["agent"]): ConversationItem {
  return {
    itemId: "ix-1",
    revision: 1,
    globalOrdinal: 1,
    lane: "interaction",
    source: "harness-live",
    provenance: { strength: "exact", origin: "codex" },
    role: "system",
    kind: "interaction",
    phase: "waiting",
    blocks: [{ blockId: "prompt", type: "text", text: "allow this?" }],
    ...(agent !== undefined ? { agent } : {}),
  };
}

afterEach(() => {
  cleanup();
});

describe("InteractionItem agent badge", () => {
  it("badges the asking agent's label when the item carries an agent ref", () => {
    render(
      <InteractionItem
        item={interactionItem({ agentId: "t-1", nickname: "scout", status: "running" })}
      />,
    );
    expect(screen.getByTestId("interaction-agent-badge").textContent).toBe("scout");
  });

  it("falls back to agent <short-id> when no nickname/role/path is bound", () => {
    render(
      <InteractionItem item={interactionItem({ agentId: "abcdef1234567890", status: "unknown" })} />,
    );
    expect(screen.getByTestId("interaction-agent-badge").textContent).toBe("agent abcdef12");
  });

  it("stays unbadged for a parent-conversation interaction", () => {
    render(<InteractionItem item={interactionItem()} />);
    expect(screen.queryByTestId("interaction-agent-badge")).toBeNull();
  });
});

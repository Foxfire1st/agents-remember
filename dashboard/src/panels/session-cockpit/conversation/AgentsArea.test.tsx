// AgentsArea rendering states: one live row per roster-evidenced agent with
// label + status chip + terminal final-message preview; the collapsed summary line for the empty
// roster; expand/collapse on the summary toggle; row activation driving the focus callback.

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ConversationAgentView } from "../../../data/conversation/agents";
import { AgentsArea } from "./AgentsArea";

function agent(overrides: Partial<ConversationAgentView> & { agentId: string }): ConversationAgentView {
  return { label: `agent ${overrides.agentId}`, status: "running", ...overrides };
}

function renderArea(
  agents: ConversationAgentView[],
  focusedAgentId: string | null = null,
  onFocusAgent = vi.fn(),
) {
  const utils = render(
    <AgentsArea agents={agents} focusedAgentId={focusedAgentId} onFocusAgent={onFocusAgent} />,
  );
  return { ...utils, onFocusAgent };
}

afterEach(() => {
  cleanup();
});

describe("AgentsArea", () => {
  it("collapses to a static summary line when the roster is empty", () => {
    renderArea([]);
    const summary = screen.getByTestId("conversation-agents-summary");
    expect(summary.textContent).toBe("0 agents");
    expect(summary.tagName).toBe("SPAN"); // nothing to expand — no dead toggle
    expect(screen.queryByTestId("conversation-agents-rows")).toBeNull();
  });

  it("renders one row per agent with label, status chip, and terminal preview", () => {
    renderArea([
      agent({ agentId: "t-1", label: "scout", status: "running" }),
      agent({ agentId: "t-2", label: "reviewer", status: "completed", finalMessage: "all good" }),
      agent({ agentId: "t-3", label: "agent abcdef12", status: "failed" }),
    ]);

    // jsdom's ResizeObserver never fires, so the area is never narrow: expanded by default.
    const rows = screen.getAllByTestId("conversation-agent-row");
    expect(rows).toHaveLength(3);
    expect(screen.getByTestId("conversation-agents-summary").textContent).toBe(
      "3 agents · 1 running",
    );

    const statuses = screen.getAllByTestId("conversation-agent-status");
    expect(statuses.map((chip) => chip.textContent)).toEqual(["running", "completed", "failed"]);

    // The final-message preview renders only where terminal evidence carried it.
    const previews = screen.getAllByTestId("conversation-agent-final");
    expect(previews).toHaveLength(1);
    expect(previews[0]?.textContent).toBe("all good");
    expect(previews[0]?.getAttribute("title")).toBe("all good");
  });

  it("marks the focused agent row with aria-current", () => {
    renderArea([agent({ agentId: "t-1", label: "scout" })], "t-1");
    expect(screen.getByTestId("conversation-agent-row").getAttribute("aria-current")).toBe("true");
  });

  it("collapses/expands the rows through the summary toggle with honest aria-expanded", () => {
    renderArea([agent({ agentId: "t-1", label: "scout" })]);
    const summary = screen.getByTestId("conversation-agents-summary");
    expect(summary.getAttribute("aria-expanded")).toBe("true");

    fireEvent.click(summary);
    expect(summary.getAttribute("aria-expanded")).toBe("false");
    expect(screen.queryByTestId("conversation-agent-row")).toBeNull();

    fireEvent.click(summary);
    expect(summary.getAttribute("aria-expanded")).toBe("true");
    expect(screen.getByTestId("conversation-agent-row")).toBeTruthy();
  });

  it("activating a row focuses that agent; activating the focused row returns to the parent", () => {
    const { onFocusAgent } = renderArea([agent({ agentId: "t-1", label: "scout" })]);
    fireEvent.click(screen.getByTestId("conversation-agent-row"));
    expect(onFocusAgent).toHaveBeenCalledWith("t-1");

    cleanup();
    const focused = renderArea([agent({ agentId: "t-1", label: "scout" })], "t-1");
    fireEvent.click(screen.getByTestId("conversation-agent-row"));
    expect(focused.onFocusAgent).toHaveBeenCalledWith(null);
  });
});

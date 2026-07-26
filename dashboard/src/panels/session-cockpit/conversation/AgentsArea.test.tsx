// AgentsArea: ONE compact line always — never one row per agent (260718-CHATS-L7R R5). The line
// carries the tone-colored count chip plus the viewing note/back-to-parent affordance in an agent
// view; line activation opens the listbox menu (open, arrow navigation, Enter/click select,
// Esc/backdrop dismiss) with the listbox-aria + focus discipline asserted throughout.

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

function line(): HTMLElement {
  return screen.getByTestId("conversation-agents-line");
}

afterEach(() => {
  cleanup();
});

describe("AgentsArea", () => {
  it("shows a static '0 agents' line for the empty roster — no dead toggle", () => {
    renderArea([]);
    const summary = line();
    expect(summary.textContent).toBe("0 agents");
    expect(summary.tagName).toBe("SPAN");
    expect(summary.getAttribute("aria-haspopup")).toBeNull();
    expect(screen.queryByTestId("conversation-agents-menu")).toBeNull();
  });

  it("renders ONLY the compact count line, however large the roster (no per-agent rows)", () => {
    const agents = Array.from({ length: 20 }, (_, index) =>
      agent({ agentId: `t-${index}`, status: index % 2 === 0 ? "running" : "completed" }),
    );
    renderArea(agents);
    expect(screen.getByTestId("conversation-agents-count").textContent).toBe(
      "20 agents · 10 running",
    );
    expect(screen.queryByTestId("conversation-agent-option")).toBeNull();
    expect(screen.queryByTestId("conversation-agents-menu")).toBeNull();
  });

  it("opens the menu on Enter with listbox aria, option content, and focus on the listbox", () => {
    renderArea([
      agent({ agentId: "t-1", label: "scout", status: "running" }),
      agent({ agentId: "t-2", label: "reviewer", status: "completed", finalMessage: "all good" }),
      agent({ agentId: "t-3", label: "agent abcdef12", status: "failed" }),
    ]);
    const summary = line();
    expect(summary.getAttribute("aria-haspopup")).toBe("listbox");
    expect(summary.getAttribute("aria-expanded")).toBe("false");

    fireEvent.keyDown(summary, { key: "Enter" });
    expect(summary.getAttribute("aria-expanded")).toBe("true");

    const menu = screen.getByTestId("conversation-agents-menu");
    expect(menu.getAttribute("role")).toBe("listbox");
    expect(document.activeElement).toBe(menu);

    const options = screen.getAllByTestId("conversation-agent-option");
    expect(options).toHaveLength(3);
    expect(options.map((option) => option.getAttribute("role"))).toEqual([
      "option",
      "option",
      "option",
    ]);
    expect(screen.getAllByTestId("conversation-agent-status").map((chip) => chip.textContent)).toEqual(
      ["running", "completed", "failed"],
    );
    // The final-message preview renders only where terminal evidence carried it.
    const previews = screen.getAllByTestId("conversation-agent-final");
    expect(previews).toHaveLength(1);
    expect(previews[0]?.textContent).toBe("all good");
    expect(previews[0]?.getAttribute("title")).toBe("all good");

    // The first option is the initial active descendant.
    expect(menu.getAttribute("aria-activedescendant")).toBe("conversation-agent-option-t-1");
    expect(options[0]?.getAttribute("aria-selected")).toBe("true");
    expect(options[1]?.getAttribute("aria-selected")).toBe("false");
  });

  it("opens the menu on click; clicking an option selects like Enter", () => {
    const { onFocusAgent } = renderArea([
      agent({ agentId: "t-1", label: "scout" }),
      agent({ agentId: "t-2", label: "reviewer" }),
    ]);
    fireEvent.click(line());
    expect(screen.getByTestId("conversation-agents-menu")).toBeTruthy();

    fireEvent.click(screen.getAllByTestId("conversation-agent-option")[1] as HTMLElement);
    expect(onFocusAgent).toHaveBeenCalledWith("t-2");
    expect(screen.queryByTestId("conversation-agents-menu")).toBeNull();
    expect(document.activeElement).toBe(line());
  });

  it("navigates the menu with ArrowUp/ArrowDown (wrapping) and selects the active option on Enter", () => {
    const { onFocusAgent } = renderArea([
      agent({ agentId: "t-1", label: "scout" }),
      agent({ agentId: "t-2", label: "reviewer" }),
    ]);
    fireEvent.keyDown(line(), { key: "Enter" });
    const menu = screen.getByTestId("conversation-agents-menu");

    fireEvent.keyDown(menu, { key: "ArrowDown" });
    expect(menu.getAttribute("aria-activedescendant")).toBe("conversation-agent-option-t-2");

    fireEvent.keyDown(menu, { key: "ArrowDown" }); // wraps to the first option
    expect(menu.getAttribute("aria-activedescendant")).toBe("conversation-agent-option-t-1");

    fireEvent.keyDown(menu, { key: "ArrowUp" }); // wraps back to the last
    expect(menu.getAttribute("aria-activedescendant")).toBe("conversation-agent-option-t-2");
    expect(
      screen.getAllByTestId("conversation-agent-option")[1]?.getAttribute("aria-selected"),
    ).toBe("true");

    fireEvent.keyDown(menu, { key: "Enter" });
    expect(onFocusAgent).toHaveBeenCalledWith("t-2");
    expect(screen.queryByTestId("conversation-agents-menu")).toBeNull();
    expect(document.activeElement).toBe(line());
  });

  it("scrolls the active option into view on every active change (20-agent roster)", () => {
    const scrollSpy = vi.spyOn(Element.prototype, "scrollIntoView");
    try {
      const agents = Array.from({ length: 20 }, (_, index) => agent({ agentId: `t-${index}` }));
      renderArea(agents);
      fireEvent.keyDown(line(), { key: "Enter" });
      const menu = screen.getByTestId("conversation-agents-menu");

      scrollSpy.mockClear(); // the open itself already scrolled the initial active option
      fireEvent.keyDown(menu, { key: "ArrowDown" });
      expect(menu.getAttribute("aria-activedescendant")).toBe("conversation-agent-option-t-1");
      const activeOption = document.getElementById("conversation-agent-option-t-1");
      expect(scrollSpy).toHaveBeenCalled();
      expect(scrollSpy.mock.contexts).toContain(activeOption);

      scrollSpy.mockClear();
      fireEvent.keyDown(menu, { key: "ArrowUp" }); // back to the first option…
      expect(menu.getAttribute("aria-activedescendant")).toBe("conversation-agent-option-t-0");
      fireEvent.keyDown(menu, { key: "ArrowUp" }); // …then wraps to the last
      expect(menu.getAttribute("aria-activedescendant")).toBe("conversation-agent-option-t-19");
      expect(scrollSpy.mock.contexts).toContain(
        document.getElementById("conversation-agent-option-t-19"),
      );
    } finally {
      scrollSpy.mockRestore();
    }
  });

  it("Escape closes the menu without selecting and returns focus to the line", () => {
    const { onFocusAgent } = renderArea([agent({ agentId: "t-1", label: "scout" })]);
    fireEvent.keyDown(line(), { key: "Enter" });
    fireEvent.keyDown(screen.getByTestId("conversation-agents-menu"), { key: "Escape" });

    expect(onFocusAgent).not.toHaveBeenCalled();
    expect(screen.queryByTestId("conversation-agents-menu")).toBeNull();
    expect(line().getAttribute("aria-expanded")).toBe("false");
    expect(document.activeElement).toBe(line());
  });

  it("an outside (backdrop) click closes the menu without selecting", () => {
    const { onFocusAgent } = renderArea([agent({ agentId: "t-1", label: "scout" })]);
    fireEvent.click(line());
    fireEvent.click(screen.getByTestId("conversation-agents-backdrop"));

    expect(onFocusAgent).not.toHaveBeenCalled();
    expect(screen.queryByTestId("conversation-agents-menu")).toBeNull();
    expect(document.activeElement).toBe(line());
  });

  it("shows the viewing note + back-to-parent affordance on the line while an agent view is active", () => {
    const { onFocusAgent } = renderArea([agent({ agentId: "t-1", label: "scout" })], "t-1");
    expect(screen.getByTestId("conversation-agent-focus-note").textContent).toBe("viewing scout");

    fireEvent.click(screen.getByTestId("conversation-back-to-parent"));
    expect(onFocusAgent).toHaveBeenCalledWith(null);
  });

  it("starts the menu's active option on the currently viewed agent; re-selecting it just closes", () => {
    const { onFocusAgent } = renderArea(
      [agent({ agentId: "t-1", label: "scout" }), agent({ agentId: "t-2", label: "reviewer" })],
      "t-2",
    );
    fireEvent.keyDown(line(), { key: "Enter" });
    const menu = screen.getByTestId("conversation-agents-menu");
    expect(menu.getAttribute("aria-activedescendant")).toBe("conversation-agent-option-t-2");

    fireEvent.keyDown(menu, { key: "Enter" });
    expect(onFocusAgent).not.toHaveBeenCalled(); // no redundant focus write/announcement
    expect(screen.queryByTestId("conversation-agents-menu")).toBeNull();
  });

  it("Escape on the closed line returns an active agent view to the parent", () => {
    const { onFocusAgent } = renderArea([agent({ agentId: "t-1", label: "scout" })], "t-1");
    fireEvent.keyDown(line(), { key: "Escape" });
    expect(onFocusAgent).toHaveBeenCalledWith(null);
  });
});

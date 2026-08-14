// ConversationLibraryList sub-agent nesting: a row's `agents` render as
// indented child rows (label + suffix) that select through the exact same flow with the child's
// server-minted key; the page's `agentsNote` renders verbatim when the server reports (partial)
// agent unavailability.

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ConversationLibraryRow } from "../../../data/conversation-library/types";
import {
  conversationLibraryAgentRow,
  conversationLibraryRow as row,
} from "../../../test/fixtures/conversationWire";
import { ConversationLibraryList } from "./ConversationLibraryList";

const AGENT = conversationLibraryAgentRow({
  role: "explorer",
  safeNativeIdSuffix: "AG3NT1",
  lastActivityAt: "2026-07-21T00:00:00Z",
});

function renderList(rows: ConversationLibraryRow[], agentsNote?: string | null) {
  const onSelect = vi.fn<(selected: ConversationLibraryRow) => void>();
  const utils = render(
    <ConversationLibraryList
      harnessId="codex"
      rows={rows}
      selectedKey={null}
      nextCursor={null}
      loading={false}
      agentsNote={agentsNote}
      onSelect={onSelect}
      onLoadMore={() => {}}
    />,
  );
  return { ...utils, onSelect };
}

afterEach(() => {
  cleanup();
});

describe("ConversationLibraryList agent nesting", () => {
  it("renders agent children as indented rows with label + suffix under the parent", () => {
    renderList([row({ agents: [AGENT] })]);

    const child = screen.getByTestId("library-agent-row");
    expect(child.textContent).toContain("agent abcdef12");
    expect(child.textContent).toContain("…AG3NT1");
    expect(child.textContent).toContain("explorer");
    // The parent row renders unchanged, without the agent badge.
    expect(screen.getByTestId("library-row").textContent).toContain("the parent conversation");
  });

  it("selects a child through the same flow with the child's own server-minted key", () => {
    const { onSelect } = renderList([row({ agents: [AGENT] })]);

    fireEvent.click(screen.getByTestId("library-agent-row"));
    expect(onSelect).toHaveBeenCalledTimes(1);
    const selected = onSelect.mock.calls[0]?.[0];
    expect(selected.conversationKey).toBe("key-agent-1");
    expect(selected.identityDigest).toBe("digest-agent-1");
    // The child carries no nested agents of its own; capabilities follow the parent's read path.
    expect(selected.agents).toEqual([]);
    expect(selected.capabilities.completeness.state).toBe("supported");
  });

  it("renders no child rows when the row carries no agents", () => {
    renderList([row()]);
    expect(screen.queryByTestId("library-agent-row")).toBeNull();
  });

  it("renders the agentsNote verbatim when present, and nothing when absent", () => {
    const { unmount } = renderList(
      [row()],
      "sub-agent conversations unavailable: collab evidence truncated",
    );
    expect(screen.getByTestId("library-agents-note").textContent).toBe(
      "sub-agent conversations unavailable: collab evidence truncated",
    );
    unmount();

    renderList([row()], null);
    expect(screen.queryByTestId("library-agents-note")).toBeNull();
  });
});

import { cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { fromTerminalSessionInfo, sessionStore } from "../../data/sessions";
import { catalogRow } from "../../test/fixtures/catalogRows";
import type { TaskDocNode } from "../../types/projection";
import { ChatContextBar } from "./ChatContextBar";

const LEAF_KEY = "agents-remember/260628_operations-integration/260628-L5";

function leafDoc(): TaskDocNode {
  return {
    id: "260628-L5",
    repository: "agents-remember",
    kind: "subTask",
    docPath: "/tasks/agents-remember/260628_operations-integration/05_sidebar-chat-attachment.json",
    title: "Sidebar chat attachment",
  } as unknown as TaskDocNode;
}

function seedTerminal(overrides: Partial<ReturnType<typeof catalogRow>> = {}) {
  const session = fromTerminalSessionInfo(
    catalogRow({ id: "term-1", kind: "terminal", ...overrides }),
  );
  sessionStore.getState().hydrate([session]);
  return session;
}

afterEach(() => {
  cleanup();
  sessionStore.setState({ sessions: [], activeId: null, count: 0 });
  vi.unstubAllGlobals();
});

describe("canonical Chats duty bar", () => {
  it("preserves the legacy existing-row lifecycle attachment as explicitly local state", () => {
    const focused = seedTerminal();
    const { getByTestId } = render(
      <ChatContextBar
        focused={focused}
        selectedLifecycleId="LC-1"
        taskDocuments={[]}
        onLaunchChat={() => {}}
        onLaunchTerminal={() => {}}
      />,
    );

    const button = getByTestId("chats-attach-lifecycle");
    expect(button.textContent).toContain("Route locally");
    fireEvent.click(button);
    expect(sessionStore.getState().sessions[0]?.lifecycleId).toBe("LC-1");
  });

  it("moves a leaf only after server acceptance and broadcasts the authoritative change", async () => {
    const messages: unknown[] = [];
    vi.stubGlobal(
      "BroadcastChannel",
      class {
        onmessage = null;
        postMessage(message: unknown) {
          messages.push(message);
        }
        close() {}
      },
    );
    const focused = seedTerminal({ leafKey: "agents-remember/old-master/old-leaf" });
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 200 });
    vi.stubGlobal("fetch", fetchMock);
    const { getByTestId } = render(
      <ChatContextBar
        focused={focused}
        taskDocuments={[leafDoc()]}
        onLaunchChat={() => {}}
        onLaunchTerminal={() => {}}
      />,
    );

    expect(getByTestId("chats-attach-leaf-picker").textContent).toContain("Move leaf");
    fireEvent.click(getByTestId("chats-attach-leaf-picker"));
    fireEvent.click(getByTestId("chats-attach-leaf-picker-leaf"));

    await waitFor(() => expect(sessionStore.getState().sessions[0]?.leafKey).toBe(LEAF_KEY));
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/terminal/term-1/attach-leaf",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ leafKey: LEAF_KEY, role: "terminal" }),
      }),
    );
    expect(messages).toContainEqual(
      expect.objectContaining({ reason: "leaf", sessionId: "term-1" }),
    );
  });

  it("surfaces the server's same-role refusal without changing the local row", async () => {
    const focused = seedTerminal();
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: false, status: 409 }));
    const { getByTestId, findByRole } = render(
      <ChatContextBar
        focused={focused}
        taskDocuments={[leafDoc()]}
        onLaunchChat={() => {}}
        onLaunchTerminal={() => {}}
      />,
    );

    fireEvent.click(getByTestId("chats-attach-leaf-picker"));
    fireEvent.click(getByTestId("chats-attach-leaf-picker-leaf"));

    expect((await findByRole("alert")).textContent).toContain("leaf already has a terminal seat");
    expect(sessionStore.getState().sessions[0]?.leafKey).toBeUndefined();
  });
});

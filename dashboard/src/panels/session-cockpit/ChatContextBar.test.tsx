import { cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { fromTerminalSessionInfo, sessionStore } from "../../data/sessions";
import { catalogRow } from "../../test/fixtures/catalogRows";
import { taskDoc } from "../../test/fixtures/wire";
import type { TaskDocNode } from "../../types/projection";
import { ChatContextBar, ChatSessionActions } from "./ChatContextBar";

const LEAF_KEY = "agents-remember/260628_operations-integration/260628-L5";

function leafDoc(): TaskDocNode {
  return taskDoc({
    id: "260628-L5",
    repository: "agents-remember",
    kind: "subTask",
    docPath: "/tasks/agents-remember/260628_operations-integration/05_sidebar-chat-attachment.json",
    title: "Sidebar chat attachment",
  });
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
  it("keeps creation in the rail and selected-session actions in the stage", () => {
    const rail = render(
      <ChatContextBar
        onLaunchChat={() => {}}
        onSessionOpened={() => {}}
      />,
    );

    expect(rail.getByTestId("chats-new-chat")).toBeTruthy();
    expect(rail.queryByTestId("chats-browse-history")).toBeNull();
    expect(rail.queryByTestId("chats-attach-leaf-picker")).toBeNull();
    rail.unmount();

    const focused = seedTerminal({ harness: "codex", controlState: "ready" });
    const stage = render(
      <ChatSessionActions
        focused={focused}
        taskDocuments={[leafDoc()]}
        onBrowseHistory={() => {}}
      />,
    );

    expect(stage.getByTestId("chats-browse-history")).toBeTruthy();
    expect(stage.getByTestId("chats-attach-leaf-picker")).toBeTruthy();
    expect(stage.queryByTestId("chats-new-chat")).toBeNull();
  });

  it("does not expose the legacy client-only lifecycle route in selected-session chrome", () => {
    const focused = seedTerminal({ lifecycleId: undefined });
    const { queryByTestId } = render(
      <ChatSessionActions
        focused={focused}
        taskDocuments={[]}
        onBrowseHistory={() => {}}
      />,
    );

    expect(queryByTestId("chats-attach-lifecycle")).toBeNull();
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
      <ChatSessionActions
        focused={focused}
        taskDocuments={[leafDoc()]}
        onBrowseHistory={() => {}}
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
      <ChatSessionActions
        focused={focused}
        taskDocuments={[leafDoc()]}
        onBrowseHistory={() => {}}
      />,
    );

    fireEvent.click(getByTestId("chats-attach-leaf-picker"));
    fireEvent.click(getByTestId("chats-attach-leaf-picker-leaf"));

    expect((await findByRole("alert")).textContent).toContain("leaf already has a terminal seat");
    expect(sessionStore.getState().sessions[0]?.leafKey).toBeUndefined();
  });

  it("focuses an exact accepted raw session once", async () => {
    vi.stubGlobal("crypto", { randomUUID: () => "raw-1" });
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          session: "raw-1",
          label: "Terminal 1",
          kind: "terminal",
          lifecycleId: "LC-1",
          leafKey: null,
          seatRole: "terminal",
          status: "running",
        }),
        { status: 200 },
      ),
    );
    vi.stubGlobal("fetch", fetchMock);
    const onSessionOpened = vi.fn();
    const { getByTestId } = render(
      <ChatContextBar
        selectedLifecycleId="LC-1"
        onLaunchChat={() => {}}
        onSessionOpened={onSessionOpened}
      />,
    );

    fireEvent.click(getByTestId("chats-new-terminal"));

    await waitFor(() => expect(onSessionOpened).toHaveBeenCalledWith("raw-1"));
    expect(sessionStore.getState().sessions).toEqual([
      expect.objectContaining({ id: "raw-1", kind: "terminal", status: "running" }),
    ]);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("projects a raw open failure without a row or focus callback", async () => {
    vi.stubGlobal("crypto", { randomUUID: () => "ghost-raw" });
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("offline")));
    const onSessionOpened = vi.fn();
    const { getByTestId, findByTestId } = render(
      <ChatContextBar
        onLaunchChat={() => {}}
        onSessionOpened={onSessionOpened}
      />,
    );

    fireEvent.click(getByTestId("chats-new-terminal"));

    expect((await findByTestId("chats-session-open-error")).textContent).toContain(
      "session open network",
    );
    expect(sessionStore.getState().sessions).toEqual([]);
    expect(onSessionOpened).not.toHaveBeenCalled();
  });

  it("projects a contradictory raw harness response as protocol failure without focus", async () => {
    vi.stubGlobal("crypto", { randomUUID: () => "ghost-raw" });
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            session: "ghost-raw",
            label: "Terminal 1",
            kind: "terminal",
            harness: "claude",
            status: "running",
            controlState: "ready",
          }),
          { status: 200 },
        ),
      ),
    );
    const onSessionOpened = vi.fn();
    const { getByTestId, findByTestId } = render(
      <ChatContextBar
        onLaunchChat={() => {}}
        onSessionOpened={onSessionOpened}
      />,
    );

    fireEvent.click(getByTestId("chats-new-terminal"));

    expect((await findByTestId("chats-session-open-error")).textContent).toContain(
      "session open protocol",
    );
    expect(sessionStore.getState().sessions).toEqual([]);
    expect(onSessionOpened).not.toHaveBeenCalled();
  });
});

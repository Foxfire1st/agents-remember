import { act, fireEvent, render, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { fromTerminalSessionInfo, sessionStore } from "../../../data/sessions";
import { sessionCockpitStore } from "../../../data/sessionCockpitStore";
import { catalogRow, FLEET } from "../../../test/fixtures/catalogRows";
import { SessionsView } from "./SessionsView";
// Registers the shared afterEach (store/localStorage reset) for this split file.
import "./test-utils";

vi.mock("../../Terminal", async () => {
  const { useEffect } = await import("react");
  const { mockTerminalMounts, mockTerminalUnmounts } = await import(
    "./test-utils"
  );
  return {
    Terminal: ({ sessionId, readOnly }: { sessionId: string; readOnly?: boolean }) => {
      useEffect(() => {
        mockTerminalMounts.push(sessionId);
        return () => {
          mockTerminalUnmounts.push(sessionId);
        };
      }, [sessionId]);
      return (
        <div
          data-testid={`mock-terminal-${sessionId}`}
          data-read-only={String(readOnly ?? false)}
        />
      );
    },
  };
});

describe("S5 legacy duty parity", () => {
  it("launches a raw terminal with selected-lifecycle inheritance, then focuses and persists it", async () => {
    const id = "00000000-0000-4000-8000-000000000005";
    vi.spyOn(globalThis.crypto, "randomUUID").mockReturnValue(id);
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          session: id,
          label: "Terminal 1",
          kind: "terminal",
          lifecycleId: "LC-S5",
          leafKey: null,
          seatRole: "terminal",
          status: "running",
        }),
        { status: 200 },
      ),
    );
    vi.stubGlobal("fetch", fetchMock);
    const { getByTestId } = render(
      <SessionsView active selectedLifecycleId="LC-S5" />,
    );

    fireEvent.click(getByTestId("chats-new-terminal"));

    await waitFor(() =>
      expect(sessionCockpitStore.getState().focusedSessionId).toBe(id),
    );
    expect(sessionStore.getState().activeId).toBe(id);
    expect(
      window.localStorage.getItem("ar-dashboard:last-active-chat-session"),
    ).toBe(id);
    expect(fetchMock).toHaveBeenCalledWith(
      `/api/terminal/${id}`,
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({
          kind: "terminal",
          label: "Terminal 1",
          lifecycleId: "LC-S5",
        }),
      }),
    );
  });

  it("restores an explicitly persisted live chat instead of replacing it with smart-default focus", async () => {
    window.localStorage.setItem(
      "ar-dashboard:last-active-chat-session",
      "worker-l4",
    );
    sessionStore
      .getState()
      .hydrate(FLEET.map(fromTerminalSessionInfo), "worker-l4");
    sessionCockpitStore.setState({ focusedSessionId: null });

    render(<SessionsView active />);

    await waitFor(() =>
      expect(sessionCockpitStore.getState().focusedSessionId).toBe("worker-l4"),
    );
    expect(
      window.localStorage.getItem("ar-dashboard:last-active-chat-session"),
    ).toBe("worker-l4");
  });

  it("does not let a catalog hydrate steal focus from a deliberately inspected landed row", async () => {
    const rows = FLEET.map(fromTerminalSessionInfo);
    sessionStore.getState().hydrate(rows);
    sessionCockpitStore.setState({ focusedSessionId: null });
    render(<SessionsView active />);
    await waitFor(() =>
      expect(sessionCockpitStore.getState().focusedSessionId).toBe(
        "worker-tui",
      ),
    );

    const landed = rows.find((session) => session.status === "landed");
    expect(landed).toBeDefined();
    act(() => sessionCockpitStore.getState().setFocusedSession(landed!.id));
    expect(sessionCockpitStore.getState().focusedSessionId).toBe(landed!.id);
    expect(sessionStore.getState().activeId).toBe("worker-tui");
    expect(
      window.localStorage.getItem("ar-dashboard:last-active-chat-session"),
    ).toBe("worker-tui");
    act(() =>
      sessionStore
        .getState()
        .hydrate(
          rows,
          window.localStorage.getItem("ar-dashboard:last-active-chat-session"),
        ),
    );

    await waitFor(() =>
      expect(sessionCockpitStore.getState().focusedSessionId).toBe(landed!.id),
    );
    expect(sessionStore.getState().activeId).toBe("worker-tui");
    expect(
      window.localStorage.getItem("ar-dashboard:last-active-chat-session"),
    ).toBe("worker-tui");
  });
});

describe("smart-default focus + handoff + session cycling (L2: R9, F17)", () => {
  beforeEach(() => {
    sessionStore.getState().hydrate(FLEET.map(fromTerminalSessionInfo));
    sessionCockpitStore.setState({ focusedSessionId: null });
  });

  it("view entry focuses the awaiting-input seat first — never an empty landing (R9)", async () => {
    const { getByTestId } = render(<SessionsView active />);
    await waitFor(() =>
      expect(
        getByTestId("rail-row-worker-tui").getAttribute("data-selected"),
      ).toBe("true"),
    );
    // The stage shows the focused seat's HeaderStrip.
    expect(getByTestId("header-strip").textContent).toContain(
      "worker-tui-shell",
    );
  });

  it("hands focus off with a note when the FOCUSED seat lands under us (F17)", async () => {
    const { getByTestId, queryByTestId } = render(<SessionsView active />);
    await waitFor(() =>
      expect(sessionCockpitStore.getState().focusedSessionId).toBe(
        "worker-tui",
      ),
    );
    fireEvent.click(getByTestId("rail-row-worker-l4"));
    await waitFor(() =>
      expect(
        getByTestId("rail-row-worker-l4").getAttribute("data-selected"),
      ).toBe("true"),
    );
    expect(queryByTestId("stage-handoff-note")).toBeNull();

    act(() => {
      sessionStore.getState().patch("worker-l4", {
        status: "landed",
        landedReason: "leaf integrated",
      });
    });
    await waitFor(() =>
      expect(queryByTestId("stage-handoff-note")).not.toBeNull(),
    );
    expect(queryByTestId("stage-handoff-note")?.textContent).toContain(
      "leaf integrated",
    );
    // Focus moved by the smart-default priority (awaiting-input first).
    expect(sessionCockpitStore.getState().focusedSessionId).toBe("worker-tui");
  });

  it("alt+↓ / alt+↑ cycle the rail order from the chrome zone", async () => {
    render(<SessionsView active />);
    await waitFor(() =>
      expect(sessionCockpitStore.getState().focusedSessionId).toBe(
        "worker-tui",
      ),
    );
    fireEvent.keyDown(document.body, {
      key: "ArrowDown",
      code: "ArrowDown",
      altKey: true,
    });
    expect(sessionCockpitStore.getState().focusedSessionId).toBe("scout");
    fireEvent.keyDown(document.body, {
      key: "ArrowUp",
      code: "ArrowUp",
      altKey: true,
    });
    expect(sessionCockpitStore.getState().focusedSessionId).toBe("worker-tui");
  });
});

describe("authoritative landed cleanup through rail and palette callers (F5-S5-2)", () => {
  it("surfaces a sprint network failure with exact targets, then preserves partial success truth", async () => {
    const rows = [
      catalogRow({
        id: "cleanup-live",
        label: "live operator",
        controlState: "ready",
        turnState: "working",
      }),
      catalogRow({ id: "cleanup-a", label: "landed alpha", status: "landed" }),
      catalogRow({ id: "cleanup-b", label: "landed beta", status: "landed" }),
    ];
    sessionStore.getState().hydrate(rows.map(fromTerminalSessionInfo));
    sessionCockpitStore.setState({ focusedSessionId: null });
    const cleanupCalls: string[][] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init?: RequestInit) => {
        const target = String(url);
        if (target.includes("landed-cleanup")) {
          const ids = (
            JSON.parse(String(init?.body)) as { sessionIds: string[] }
          ).sessionIds;
          cleanupCalls.push(ids);
          if (cleanupCalls.length === 1)
            throw new Error("network disconnected");
          return {
            ok: true,
            json: async () => ({
              closed: 1,
              skipped: 1,
              closedSessions: ["cleanup-a"],
              skippedSessions: [
                { session: "cleanup-b", reason: "status:running" },
              ],
            }),
          } as Response;
        }
        if (target.includes("/api/terminal/sessions")) {
          return {
            ok: true,
            json: async () => ({ sessions: rows }),
          } as Response;
        }
        return { ok: true, json: async () => ({}) } as Response;
      }),
    );
    const { findByTestId, getByTestId, queryByTestId } = render(
      <SessionsView active />,
    );
    await waitFor(() =>
      expect(sessionCockpitStore.getState().focusedSessionId).toBe(
        "cleanup-live",
      ),
    );

    fireEvent.click(getByTestId("rail-bulk-sprint"));
    fireEvent.click(getByTestId("rail-bulk-execute"));
    const failure = await findByTestId("landed-cleanup-failure");
    expect(failure.getAttribute("role")).toBe("alert");
    expect(failure.textContent).toContain("landed alpha (cleanup-a)");
    expect(failure.textContent).toContain("landed beta (cleanup-b)");
    expect(cleanupCalls).toEqual([["cleanup-a", "cleanup-b"]]);

    fireEvent.click(getByTestId("landed-cleanup-retry"));
    const outcome = await findByTestId("landed-cleanup-outcome");
    expect(outcome.textContent).toContain(
      "ended 1 · skipped 1 (cleanup-b: status:running)",
    );
    expect(cleanupCalls).toEqual([
      ["cleanup-a", "cleanup-b"],
      ["cleanup-a", "cleanup-b"],
    ]);
    await waitFor(() => expect(queryByTestId("rail-row-cleanup-a")).toBeNull());
    expect(getByTestId("rail-row-cleanup-b")).not.toBeNull();
  });

  it("keeps a focused landed row on palette failure, then hands it to the smart live default after retry", async () => {
    const master = "repo/cleanup-master";
    const rows = [
      catalogRow({
        id: "cleanup-smart-live",
        label: "live awaiting input",
        controlState: "ready",
        turnState: "awaiting-input",
        controlPendingInteraction: { prompt: "continue?" },
      }),
      catalogRow({
        id: "cleanup-focused-landed",
        label: "focused landed row",
        status: "landed",
        leafKey: `${master}/leaf-1`,
        landedReason: "leaf integrated",
      }),
    ];
    sessionStore.getState().hydrate(rows.map(fromTerminalSessionInfo));
    sessionCockpitStore.setState({ focusedSessionId: null });
    let cleanupAttempt = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        const target = String(url);
        if (target.includes("landed-cleanup")) {
          cleanupAttempt += 1;
          if (cleanupAttempt === 1) {
            return { ok: false, status: 503 } as Response;
          }
          return {
            ok: true,
            json: async () => ({
              closed: 1,
              skipped: 0,
              closedSessions: ["cleanup-focused-landed"],
              skippedSessions: [],
            }),
          } as Response;
        }
        if (target.includes("/api/terminal/sessions")) {
          return {
            ok: true,
            json: async () => ({ sessions: rows }),
          } as Response;
        }
        return { ok: true, json: async () => ({}) } as Response;
      }),
    );
    const { findByTestId, getByTestId } = render(<SessionsView active />);
    await waitFor(() =>
      expect(sessionCockpitStore.getState().focusedSessionId).toBe(
        "cleanup-smart-live",
      ),
    );
    // A controlled seat's live body is now the structured ConversationSurface, not
    // the PTY, so this test asserts the focus-handoff subject only; conversation keep-alive across
    // focus is the LRU'd active-conversation store (covered by its own store tests).
    fireEvent.click(getByTestId(`rail-done-toggle-${master}`));
    fireEvent.click(getByTestId("rail-row-cleanup-focused-landed"));
    await waitFor(() =>
      expect(sessionCockpitStore.getState().focusedSessionId).toBe(
        "cleanup-focused-landed",
      ),
    );
    expect(sessionStore.getState().activeId).toBe("cleanup-smart-live");

    fireEvent.keyDown(getByTestId("rail-row-cleanup-focused-landed"), {
      key: "k",
      code: "KeyK",
      ctrlKey: true,
    });
    fireEvent.click(
      await findByTestId(`palette-cmd-sessions.endDone.${master}`),
    );
    await findByTestId("landed-cleanup-failure");
    expect(sessionCockpitStore.getState().focusedSessionId).toBe(
      "cleanup-focused-landed",
    );
    expect(getByTestId("rail-row-cleanup-focused-landed")).not.toBeNull();

    fireEvent.click(getByTestId("landed-cleanup-retry"));
    await findByTestId("landed-cleanup-outcome");
    await waitFor(() =>
      expect(sessionCockpitStore.getState().focusedSessionId).toBe(
        "cleanup-smart-live",
      ),
    );
    expect(sessionStore.getState().activeId).toBe("cleanup-smart-live");
    expect(
      window.localStorage.getItem("ar-dashboard:last-active-chat-session"),
    ).toBe("cleanup-smart-live");
    expect(getByTestId("stage-handoff-note").textContent).toContain(
      "focus handed off",
    );
  });
});

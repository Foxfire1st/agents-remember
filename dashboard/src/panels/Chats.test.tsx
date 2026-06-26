import { act, cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { sessionStore } from "../data/sessions";
import { Chats } from "./Chats";

// Mock the lazy Terminal so opening a session never pulls xterm (a canvas probe) into jsdom; the stub
// just marks its sessionId so a test can assert which session terminals stay mounted.
vi.mock("./Terminal", () => ({
  Terminal: ({ sessionId }: { sessionId: string }) => <div data-testid={`term-${sessionId}`} />,
}));

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.localStorage.clear();
  sessionStore.setState({ sessions: [], activeId: null, count: 0 });
});

// These render-only tests deliberately never click a launch button: opening a session would
// Suspense-load the lazy `Terminal` and pull xterm (a canvas probe) into jsdom. The 6e-2b contract
// under test is purely "a button appears per *detected* harness", which needs no live terminal.
describe("Chats harness launch buttons (6e-2b)", () => {
  it("renders a launch button only for detected harnesses", async () => {
    const harnesses = [
      { id: "claude", name: "Claude Code", detected: true },
      { id: "codex", name: "Codex", detected: true },
      { id: "pi", name: "Pi.dev", detected: false },
    ];
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: true, json: () => Promise.resolve({ harnesses }) }),
    );

    const { findByTestId, queryByTestId, getByTestId } = render(<Chats />);

    // Detection resolves async, so await the first detected button, then assert the rest synchronously.
    const claude = await findByTestId("chats-new-harness-claude");
    expect(claude.textContent).toContain("Claude Code");
    expect(getByTestId("chats-new-harness-codex")).not.toBeNull();

    // The undetected harness gets no button; the always-present ＋ Terminal control stays.
    expect(queryByTestId("chats-new-harness-pi")).toBeNull();
    expect(getByTestId("chats-new-terminal")).not.toBeNull();
  });

  it("shows only ＋ Terminal when no backend reports harnesses", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("no backend")));
    const { findByTestId, queryByTestId } = render(<Chats />);
    expect(await findByTestId("chats-new-terminal")).not.toBeNull();
    expect(queryByTestId("chats-new-harness-claude")).toBeNull();
  });
});

// 6e-4: a session tab carries a live xterm + WebSocket. Switching tabs must NOT unmount it (that
// "bricks" the session) — every open session stays mounted, only the active layer is shown.
describe("Chats session-tab persistence (6e-4)", () => {
  it("keeps every open session mounted; switching only flips which layer is shown", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("no backend")));
    sessionStore.getState().add("Terminal", "s1");
    sessionStore.getState().add("Terminal", "s2"); // the last added is the active session

    const { findByTestId, getByTestId } = render(<Chats />);

    // Both terminals are mounted — the inactive one is hidden via CSS, not torn down.
    expect(await findByTestId("term-s1")).not.toBeNull();
    expect(getByTestId("term-s2")).not.toBeNull();
    expect(getByTestId("chats-terminal-layer-s2").style.display).toBe("flex");
    expect(getByTestId("chats-terminal-layer-s1").style.display).toBe("none");
    expect(getByTestId("chats-terminal-layer-s1").getAttribute("aria-hidden")).toBe("true");

    // Switching back to s1 flips `display` only; both stay mounted (the bricking cure).
    act(() => {
      sessionStore.getState().setActive("s1");
    });
    expect(getByTestId("term-s1")).not.toBeNull();
    expect(getByTestId("term-s2")).not.toBeNull();
    expect(getByTestId("chats-terminal-layer-s1").style.display).toBe("flex");
    expect(getByTestId("chats-terminal-layer-s2").style.display).toBe("none");
  });

  it("attaches the active untagged session to the selected lifecycle", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("no backend")));
    sessionStore.getState().add("Terminal", "s1");

    const { findByTestId } = render(<Chats selectedLifecycleId="LC1" />);
    fireEvent.click(await findByTestId("chats-attach-lifecycle"));

    expect(sessionStore.getState().sessions[0]?.lifecycleId).toBe("LC1");
  });

  it("hydrates durable sessions from the backend and mounts the restored active terminal", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const url = String(input);
        if (url.endsWith("/api/harnesses")) {
          return Promise.resolve({ ok: true, json: () => Promise.resolve({ harnesses: [] }) });
        }
        if (url.endsWith("/api/terminal/sessions")) {
          return Promise.resolve({
            ok: true,
            json: () =>
              Promise.resolve({
                sessions: [
                  {
                    id: "s1",
                    label: "Terminal 1",
                    kind: "terminal",
                    cwd: "/ws",
                    tmuxName: "ar-s1",
                    createdAt: "2026-06-26T00:00:00Z",
                    lastAttachedAt: "2026-06-26T00:00:00Z",
                    status: "running",
                  },
                ],
              }),
          });
        }
        return Promise.reject(new Error(`unexpected url ${url}`));
      }),
    );

    const { findByTestId, getByTestId } = render(<Chats />);

    expect(await findByTestId("term-s1")).not.toBeNull();
    expect(getByTestId("chats-terminal-layer-s1").style.display).toBe("flex");
    expect(sessionStore.getState().activeId).toBe("s1");
  });

  it("renders an exited restored session as status, not a terminal attachment", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const url = String(input);
        if (url.endsWith("/api/harnesses")) {
          return Promise.resolve({ ok: true, json: () => Promise.resolve({ harnesses: [] }) });
        }
        if (url.endsWith("/api/terminal/sessions")) {
          return Promise.resolve({
            ok: true,
            json: () =>
              Promise.resolve({
                sessions: [
                  {
                    id: "s1",
                    label: "Terminal 1",
                    kind: "terminal",
                    cwd: "/ws",
                    tmuxName: "ar-s1",
                    createdAt: "2026-06-26T00:00:00Z",
                    lastAttachedAt: "2026-06-26T00:00:00Z",
                    status: "exited",
                  },
                ],
              }),
          });
        }
        return Promise.reject(new Error(`unexpected url ${url}`));
      }),
    );

    const { findByTestId, queryByTestId } = render(<Chats />);

    expect(await findByTestId("chats-session-status-s1")).not.toBeNull();
    expect(queryByTestId("term-s1")).toBeNull();
  });

  it("detaches a session locally without calling the terminate endpoint", async () => {
    const fetchMock = vi.fn().mockRejectedValue(new Error("no backend"));
    vi.stubGlobal("fetch", fetchMock);
    sessionStore.getState().add("Terminal", "s1");

    const { findByLabelText } = render(<Chats />);
    fireEvent.click(await findByLabelText("Detach Terminal 1"));

    expect(sessionStore.getState().sessions).toEqual([]);
    expect(fetchMock).not.toHaveBeenCalledWith(
      "/api/terminal/s1/terminate",
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("terminates a session through the backend before removing it locally", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const url = String(input);
        if (url.endsWith("/api/terminal/s1/terminate")) return Promise.resolve({ ok: true });
        return Promise.reject(new Error("no backend"));
      }),
    );
    sessionStore.getState().add("Terminal", "s1");

    const { findByLabelText } = render(<Chats />);
    fireEvent.click(await findByLabelText("Terminate Terminal 1"));

    await waitFor(() => expect(sessionStore.getState().sessions).toEqual([]));
  });
});

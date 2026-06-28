import { act, cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { sessionStore } from "../data/sessions";
import { Chats } from "./Chats";

// Mock the lazy Terminal so opening a session never pulls xterm (a canvas probe) into jsdom; the stub
// just marks its sessionId so a test can assert which session terminals stay mounted.
vi.mock("./Terminal", () => ({
  Terminal: ({ sessionId }: { sessionId: string }) => <div data-testid={`term-${sessionId}`} />,
}));

class FakeBroadcastChannel {
  static instances: FakeBroadcastChannel[] = [];
  static messages: unknown[] = [];

  onmessage: ((event: MessageEvent) => void) | null = null;
  closed = false;

  constructor(public name: string) {
    FakeBroadcastChannel.instances.push(this);
  }

  postMessage(data: unknown): void {
    FakeBroadcastChannel.messages.push(data);
    for (const instance of FakeBroadcastChannel.instances) {
      if (instance === this || instance.closed || instance.name !== this.name) continue;
      instance.onmessage?.({ data } as MessageEvent);
    }
  }

  close(): void {
    this.closed = true;
  }

  static dispatch(data: unknown): void {
    for (const instance of FakeBroadcastChannel.instances) {
      if (!instance.closed) instance.onmessage?.({ data } as MessageEvent);
    }
  }

  static reset(): void {
    FakeBroadcastChannel.instances = [];
    FakeBroadcastChannel.messages = [];
  }
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.localStorage.clear();
  sessionStore.setState({ sessions: [], activeId: null, count: 0 });
  FakeBroadcastChannel.reset();
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

// 6e-4 + task 22: tmux/catalog own refresh persistence. The UI initially attaches only the active
// restored terminal; once a row has been selected in this page, it stays mounted while hidden so its
// xterm buffer survives tab switches.
describe("Chats session-tab persistence (6e-4)", () => {
  it("mounts restored sessions on first selection and keeps visited terminals mounted", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("no backend")));
    sessionStore.getState().add("Terminal", "s1");
    sessionStore.getState().add("Terminal", "s2"); // the last added is the active session

    const { findByTestId, getByTestId, queryByTestId } = render(<Chats />);

    expect(await findByTestId("term-s2")).not.toBeNull();
    expect(queryByTestId("term-s1")).toBeNull();
    expect(getByTestId("chats-terminal-layer-s2")).not.toBeNull();

    act(() => {
      sessionStore.getState().setActive("s1");
    });
    expect(await findByTestId("term-s1")).not.toBeNull();
    expect(getByTestId("term-s2")).not.toBeNull();
    expect(getByTestId("chats-terminal-layer-s1")).not.toBeNull();
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
    expect(getByTestId("chats-terminal-layer-s1")).not.toBeNull();
    expect(sessionStore.getState().activeId).toBe("s1");
  });

  it("refreshes this tab when another tab ends the last catalog session", async () => {
    vi.stubGlobal("BroadcastChannel", FakeBroadcastChannel);
    let catalogSessions = [
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
    ];
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
            json: () => Promise.resolve({ sessions: catalogSessions }),
          });
        }
        return Promise.reject(new Error(`unexpected url ${url}`));
      }),
    );

    const { findByTestId, queryByTestId } = render(<Chats />);
    expect(await findByTestId("term-s1")).not.toBeNull();

    catalogSessions = [];
    act(() => {
      FakeBroadcastChannel.dispatch({
        type: "terminal-catalog-changed",
        source: "other-tab",
        reason: "terminate",
        sessionId: "s1",
      });
    });

    await waitFor(() => expect(sessionStore.getState().sessions).toEqual([]));
    expect(queryByTestId("term-s1")).toBeNull();
  });

  it("does not resurrect another tab's terminated session from a stale catalog echo", async () => {
    vi.stubGlobal("BroadcastChannel", FakeBroadcastChannel);
    const staleSession = {
      id: "s1",
      label: "Terminal 1",
      kind: "terminal",
      cwd: "/ws",
      tmuxName: "ar-s1",
      createdAt: "2026-06-26T00:00:00Z",
      lastAttachedAt: "2026-06-26T00:00:00Z",
      status: "running",
    };
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
            json: () => Promise.resolve({ sessions: [staleSession] }),
          });
        }
        return Promise.reject(new Error(`unexpected url ${url}`));
      }),
    );

    const { findByTestId, queryByTestId } = render(<Chats />);
    expect(await findByTestId("term-s1")).not.toBeNull();

    act(() => {
      FakeBroadcastChannel.dispatch({
        type: "terminal-catalog-changed",
        source: "other-tab",
        reason: "terminate",
        sessionId: "s1",
      });
    });

    await waitFor(() => expect(sessionStore.getState().sessions).toEqual([]));
    await act(async () => {
      await Promise.resolve();
    });
    expect(sessionStore.getState().sessions).toEqual([]);
    expect(queryByTestId("term-s1")).toBeNull();
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

  it("terminates a session through the backend before removing it locally", async () => {
    vi.stubGlobal("BroadcastChannel", FakeBroadcastChannel);
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
    expect(FakeBroadcastChannel.messages).toEqual([
      expect.objectContaining({
        type: "terminal-catalog-changed",
        reason: "terminate",
        sessionId: "s1",
      }),
    ]);
  });
});

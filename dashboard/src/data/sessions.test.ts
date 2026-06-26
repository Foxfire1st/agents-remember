import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  createSession,
  deliverToSession,
  findSessionForLifecycle,
  fromTerminalSessionInfo,
  registerConnection,
  sendToSession,
  sessionStore,
} from "./sessions";
import { bracketedPaste, sanitizeForInjection, type TerminalConnection } from "./terminal";

// A controllable TerminalConnection: records injected input and exposes a settable output clock so the
// submitAndConfirm loop can be driven to a "responded" state under fake timers.
function fakeConn(): TerminalConnection & { inputs: string[]; outputAt: number } {
  return {
    inputs: [] as string[],
    outputAt: 0,
    sendInput(data: string) {
      this.inputs.push(data);
    },
    sendResize() {},
    whenReady() {
      return Promise.resolve();
    },
    lastOutputAt() {
      return this.outputAt;
    },
    dispose() {},
  };
}

// The session registry store (slice 6e hardening) — the state that now survives a cockpit view
// switch. Reset it between cases since the store is module-level (the whole point).
beforeEach(() => sessionStore.setState({ sessions: [], activeId: null, count: 0 }));

describe("sessionStore (6e hardening)", () => {
  it("add appends a labelled session, bumps the ordinal, and activates it", () => {
    sessionStore.getState().add("Terminal", "a");
    sessionStore.getState().add("Claude Code", "b");
    const state = sessionStore.getState();
    expect(state.sessions).toEqual([
      { id: "a", label: "Terminal 1" },
      { id: "b", label: "Claude Code 2" },
    ]);
    expect(state.activeId).toBe("b");
  });

  it("close removes the session and clears activeId only when it was the active one", () => {
    sessionStore.getState().add("Terminal", "a");
    sessionStore.getState().add("Terminal", "b"); // active = b
    sessionStore.getState().close("a"); // not active → activeId stays b
    expect(sessionStore.getState().activeId).toBe("b");
    sessionStore.getState().close("b"); // active → cleared
    expect(sessionStore.getState().sessions).toEqual([]);
    expect(sessionStore.getState().activeId).toBeNull();
  });

  it("setActive switches the active session without touching the list", () => {
    sessionStore.getState().add("Terminal", "a");
    sessionStore.getState().add("Terminal", "b");
    sessionStore.getState().setActive("a");
    expect(sessionStore.getState().activeId).toBe("a");
    expect(sessionStore.getState().sessions).toHaveLength(2);
  });

  it("attaches a lifecycle to a hosted session and resolves it for gate routing", () => {
    sessionStore.getState().add("Claude Code", "agent-1", "LC1");
    expect(findSessionForLifecycle("LC1")?.id).toBe("agent-1");
    expect(sessionStore.getState().sessions[0]).toEqual({
      id: "agent-1",
      label: "Claude Code 1",
      lifecycleId: "LC1",
    });
  });

  it("keeps one owning session per lifecycle and clears tags explicitly", () => {
    sessionStore.getState().add("Claude Code", "agent-1", "LC1");
    sessionStore.getState().add("Codex", "agent-2");
    sessionStore.getState().setLifecycle("agent-2", "LC1");
    expect(sessionStore.getState().sessions).toEqual([
      { id: "agent-1", label: "Claude Code 1" },
      { id: "agent-2", label: "Codex 2", lifecycleId: "LC1" },
    ]);
    sessionStore.getState().setLifecycle("agent-2", null);
    expect(findSessionForLifecycle("LC1")).toBeUndefined();
    expect(sessionStore.getState().sessions[1]).toEqual({ id: "agent-2", label: "Codex 2" });
  });

  it("hydrates server-owned sessions and prefers the last active live session", () => {
    sessionStore.getState().hydrate(
      [
        { id: "old", label: "Terminal 1", status: "exited" },
        { id: "live", label: "Claude Code 2", lifecycleId: "LC1", status: "running" },
      ],
      "live",
    );
    expect(sessionStore.getState().activeId).toBe("live");
    expect(findSessionForLifecycle("LC1")?.id).toBe("live");
    expect(sessionStore.getState().count).toBe(2);
  });

  it("does not route lifecycle injections to exited or terminated sessions", () => {
    sessionStore
      .getState()
      .hydrate([{ id: "dead", label: "Claude Code 1", lifecycleId: "LC1", status: "exited" }]);
    expect(findSessionForLifecycle("LC1")).toBeUndefined();
  });

  it("moves active focus away when the active session exits", () => {
    sessionStore.getState().hydrate([
      { id: "a", label: "Terminal 1", status: "running" },
      { id: "b", label: "Terminal 2", status: "running" },
    ]);
    sessionStore.getState().setActive("a");
    sessionStore.getState().setStatus("a", "exited");
    expect(sessionStore.getState().activeId).toBe("b");
  });

  it("converts terminal catalog rows into store sessions", () => {
    expect(
      fromTerminalSessionInfo({
        id: "s1",
        label: "Claude Code 2",
        kind: "harness",
        harness: "claude",
        lifecycleId: "LC1",
        cwd: "/ws",
        tmuxName: "ar-s1",
        createdAt: "2026-06-26T00:00:00Z",
        lastAttachedAt: "2026-06-26T00:00:00Z",
        status: "running",
      }),
    ).toEqual({
      id: "s1",
      label: "Claude Code 2",
      kind: "harness",
      harness: "claude",
      lifecycleId: "LC1",
      status: "running",
    });
  });

  it("passes the generated label and lifecycle to the opener before registering the session", async () => {
    vi.stubGlobal("crypto", { randomUUID: () => "generated-id" });
    const fetchMock = vi.fn().mockResolvedValue({ ok: true });
    vi.stubGlobal("fetch", fetchMock);
    expect(await createSession("Claude Code", "harness", "claude", "LC1")).toBe("generated-id");
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/terminal/generated-id",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({
          kind: "harness",
          harness: "claude",
          label: "Claude Code 1",
          lifecycleId: "LC1",
        }),
      }),
    );
    expect(sessionStore.getState().sessions[0]).toEqual({
      id: "generated-id",
      label: "Claude Code 1",
      kind: "harness",
      harness: "claude",
      lifecycleId: "LC1",
      status: "running",
    });
    vi.unstubAllGlobals();
  });
});

describe("connection registry + deliverToSession (6f hardening)", () => {
  it("queues sendToSession into pending and flushes in order once the terminal registers", () => {
    const conn = fakeConn();
    sendToSession("q1", "one");
    sendToSession("q1", "two");
    expect(conn.inputs).toEqual([]); // not registered yet → queued, not lost
    registerConnection("q1", conn);
    expect(conn.inputs).toEqual(["one", "two"]); // flushed in order on register
    registerConnection("q1", null); // teardown clears the connection
  });

  it("waits for a late-registering terminal, then injects ONE sanitized bracketed paste and confirms", async () => {
    vi.useFakeTimers();
    try {
      const conn = fakeConn();
      const raw = "msg\x1abody\x1b[201~tail"; // a 0x1a suspend byte + a stray paste-end marker
      const done = deliverToSession("race-1", raw);
      await vi.advanceTimersByTimeAsync(0);
      expect(conn.inputs).toEqual([]); // nothing sent before the terminal exists (the create-then-send race)
      registerConnection("race-1", conn);
      await vi.advanceTimersByTimeAsync(0); // whenReady resolves → the package is injected
      expect(conn.inputs[0]).toBe(bracketedPaste(sanitizeForInjection(raw))); // sanitized AND wrapped, composed
      await vi.advanceTimersByTimeAsync(500); // paste-settle + CR-echo-settle → baseline captured
      conn.outputAt = 1; // the harness responds (advances past the baseline of 0)
      await vi.advanceTimersByTimeAsync(1800);
      expect(await done).toBe("delivered");
      registerConnection("race-1", null);
    } finally {
      vi.useRealTimers();
    }
  });

  it("resolves 'unconfirmed' (never hangs) when a terminal never registers", async () => {
    vi.useFakeTimers();
    try {
      const done = deliverToSession("never", "hi");
      await vi.advanceTimersByTimeAsync(12_000); // CONNECTION_TIMEOUT_MS elapses with no registration
      expect(await done).toBe("unconfirmed");
    } finally {
      vi.useRealTimers();
    }
  });
});

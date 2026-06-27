import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  createSession,
  deliverToSession,
  findSessionForLifecycle,
  fromTerminalSessionInfo,
  notifySessionCatalogChanged,
  registerConnection,
  sendToSession,
  sessionStore,
  subscribeSessionCatalogChanges,
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

// The session registry store (slice 6e hardening) — the state that now survives a cockpit view
// switch. Reset it between cases since the store is module-level (the whole point).
beforeEach(() => {
  sessionStore.setState({ sessions: [], activeId: null, count: 0 });
  FakeBroadcastChannel.reset();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("sessionStore (6e hardening)", () => {
  it("add appends the lowest available per-prefix label and activates it", () => {
    sessionStore.getState().add("Terminal", "a");
    sessionStore.getState().add("Claude Code", "b");
    sessionStore.getState().add("Claude Code", "c");
    const state = sessionStore.getState();
    expect(state.sessions).toEqual([
      { id: "a", label: "Terminal 1" },
      { id: "b", label: "Claude Code 1" },
      { id: "c", label: "Claude Code 2" },
    ]);
    expect(state.activeId).toBe("c");
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
      { id: "agent-2", label: "Codex 1", lifecycleId: "LC1" },
    ]);
    sessionStore.getState().setLifecycle("agent-2", null);
    expect(findSessionForLifecycle("LC1")).toBeUndefined();
    expect(sessionStore.getState().sessions[1]).toEqual({ id: "agent-2", label: "Codex 1" });
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

  it("reuses a harness label after terminated sessions are removed", () => {
    sessionStore.getState().add("Claude Code", "a");
    sessionStore.getState().add("Claude Code", "b");
    sessionStore.getState().add("Claude Code", "c");

    for (const id of ["a", "b", "c"]) {
      sessionStore.getState().setStatus(id, "terminated");
      sessionStore.getState().close(id);
    }
    sessionStore.getState().add("Claude Code", "d");

    expect(sessionStore.getState().sessions).toEqual([{ id: "d", label: "Claude Code 1" }]);
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
  });
});

describe("session catalog cross-tab sync", () => {
  it("receives remote catalog-change notifications and ignores this tab's own broadcast", () => {
    vi.stubGlobal("BroadcastChannel", FakeBroadcastChannel);
    const seen: string[] = [];
    const unsubscribe = subscribeSessionCatalogChanges((reason, sessionId) =>
      seen.push(`${reason}:${sessionId ?? ""}`),
    );

    FakeBroadcastChannel.dispatch({
      type: "terminal-catalog-changed",
      source: "other-tab",
      reason: "terminate",
      sessionId: "gone",
    });
    notifySessionCatalogChanged("create", "created");
    unsubscribe();

    expect(seen).toEqual(["terminate:gone"]);
    expect(FakeBroadcastChannel.messages).toEqual([
      expect.objectContaining({
        type: "terminal-catalog-changed",
        reason: "create",
        sessionId: "created",
      }),
    ]);
  });

  it("broadcasts create only after the backend opener persists the catalog row", async () => {
    vi.stubGlobal("BroadcastChannel", FakeBroadcastChannel);
    vi.stubGlobal("crypto", { randomUUID: () => "generated-id" });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true }));

    await createSession("Claude Code", "harness", "claude");
    expect(FakeBroadcastChannel.messages).toEqual([
      expect.objectContaining({
        type: "terminal-catalog-changed",
        reason: "create",
        sessionId: "generated-id",
      }),
    ]);

    FakeBroadcastChannel.reset();
    sessionStore.setState({ sessions: [], activeId: null, count: 0 });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: false }));
    await createSession("Claude Code", "harness", "claude");
    expect(FakeBroadcastChannel.messages).toEqual([]);
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

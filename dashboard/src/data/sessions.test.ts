import { beforeEach, describe, expect, it } from "vitest";

import { sessionStore } from "./sessions";

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
});

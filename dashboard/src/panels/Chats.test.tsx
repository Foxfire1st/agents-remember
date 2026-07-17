import { act, cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { sessionCockpitStore } from "../data/sessionCockpitStore";
import { fromTerminalSessionInfo, sessionStore } from "../data/sessions";
import { dashboardStore } from "../data/store";
import { L6_INTERACTION_FREETEXT } from "../test/fixtures/catalogRows";
import type { LifecycleProjection, TaskDocNode } from "../types/projection";
import { Chats } from "./Chats";

const LEAF_KEY = "agents-remember/260628_operations-integration/260628-L5";
const SECOND_LEAF_KEY = "agents-remember/260628_operations-integration/260628-L9";

// A minimal task-doc whose qualified leaf id (`repo / dir(docPath) basename / id`) equals LEAF_KEY.
// `kind: "subTask"` marks it as a leaf so the "Attach to leaf" picker lists it.
function leafDoc(): TaskDocNode {
  return {
    id: "260628-L5",
    repository: "agents-remember",
    kind: "subTask",
    docPath: "/tasks/agents-remember/260628_operations-integration/05_sidebar-chat-attachment.json",
    title: "Sidebar chat attachment",
  } as unknown as TaskDocNode;
}

function secondLeafDoc(): TaskDocNode {
  return {
    id: "260628-L9",
    repository: "agents-remember",
    kind: "subTask",
    docPath: "/tasks/agents-remember/260628_operations-integration/09_chat-leaf-reassignment-and-live-catalog-sync.json",
    title: "Chat leaf reassignment",
  } as unknown as TaskDocNode;
}

function pointerEvent(type: string, clientX: number, pointerId = 1): Event {
  const event = new Event(type, { bubbles: true });
  Object.defineProperties(event, {
    clientX: { value: clientX },
    pointerId: { value: pointerId },
  });
  return event;
}

// Mock the lazy Terminal so opening a session never pulls xterm (a canvas probe) into jsdom; the stub
// just marks its sessionId so a test can assert which session terminals stay mounted.
vi.mock("./Terminal", () => ({
  Terminal: ({ sessionId, readOnly }: { sessionId: string; readOnly?: boolean }) => (
    <div data-testid={`term-${sessionId}`} data-readonly={readOnly ? "true" : "false"} />
  ),
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
  sessionCockpitStore.setState({ perSession: {} });
  dashboardStore.setState({ lifecycles: {} });
  FakeBroadcastChannel.reset();
});

describe("Chats shared-composer interaction invariant (L5 F3)", () => {
  it("routes a pending non-choice answer through the gate and never /submit", async () => {
    const session = fromTerminalSessionInfo({
      ...L6_INTERACTION_FREETEXT,
      id: "chats-answer",
      lifecycleId: "lc-chats-answer",
      controlPendingInteraction: {
        ...L6_INTERACTION_FREETEXT.controlPendingInteraction,
        interactionId: "ix-chats-answer",
      },
    });
    sessionStore.getState().hydrate([session]);
    dashboardStore.setState({
      lifecycles: {
        "lc-chats-answer": {
          id: "lc-chats-answer",
          gate: {
            id: "gate-chats-answer",
            kind: "agent-question",
            state: "open",
            decisions: [],
            ts: "2026-07-17T09:00:00Z",
            packet: {
              adapterInteraction: {
                sessionId: session.id,
                interactionId: "ix-chats-answer",
              },
            },
          },
        } as unknown as LifecycleProjection,
      },
    });
    const urls: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        urls.push(url);
        if (url === "/api/harnesses") {
          return { ok: true, json: async () => ({ harnesses: [] }) } as Response;
        }
        if (url === "/api/terminal/sessions") {
          return { ok: true, json: async () => ({ sessions: [] }) } as Response;
        }
        if (url === "/api/actions/approve") {
          return { status: 202, text: async () => "" } as Response;
        }
        throw new Error(`unexpected URL ${url}`);
      }),
    );
    const { findByTestId } = render(<Chats />);
    await findByTestId("session-composer-answer-mode");
    act(() => sessionCockpitStore.getState().setComposerDraft(session.id, "start from ar/base"));
    fireEvent.click(await findByTestId("session-composer-send"));
    await waitFor(() => expect(urls).toContain("/api/actions/approve"));
    expect(urls.some((url) => url.endsWith("/submit"))).toBe(false);
  });
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

describe("Chats sidebar resize", () => {
  it("restores the persisted width and exposes the bounded separator value", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("no backend")));
    window.localStorage.setItem("chats.sidebar-width", "420");
    sessionStore.getState().add("Terminal", "s1");

    const { findByTestId } = render(<Chats />);

    expect((await findByTestId("chats-sidebar")).style.width).toBe("420px");
    expect((await findByTestId("chats-sidebar-resize")).getAttribute("aria-valuenow")).toBe(
      "420",
    );
  });

  it("resizes with pointer drag and keyboard arrows, persisting each width", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("no backend")));
    sessionStore.getState().add("Terminal", "s1");

    const { findByTestId } = render(<Chats />);
    const sidebar = await findByTestId("chats-sidebar");
    const handle = await findByTestId("chats-sidebar-resize");
    handle.setPointerCapture = vi.fn();

    fireEvent(handle, pointerEvent("pointerdown", 300));
    fireEvent(window, pointerEvent("pointermove", 360));
    fireEvent(window, pointerEvent("pointerup", 360));
    expect(sidebar.style.width).toBe("316px");
    expect(window.localStorage.getItem("chats.sidebar-width")).toBe("316");

    fireEvent.keyDown(handle, { key: "ArrowRight" });
    expect(sidebar.style.width).toBe("340px");
    expect(window.localStorage.getItem("chats.sidebar-width")).toBe("340");

    fireEvent.keyDown(handle, { key: "ArrowLeft" });
    expect(sidebar.style.width).toBe("316px");
    expect(window.localStorage.getItem("chats.sidebar-width")).toBe("316");
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

  it("renders a landed restored session as a read-only terminal attachment", async () => {
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
                    status: "landed",
                    landedReason: "leaf integrated",
                  },
                ],
              }),
          });
        }
        return Promise.reject(new Error(`unexpected url ${url}`));
      }),
    );

    const { findByTestId, queryByTestId } = render(<Chats />);

    const terminal = await findByTestId("term-s1");
    expect(terminal.getAttribute("data-readonly")).toBe("true");
    expect(queryByTestId("chats-session-status-s1")).toBeNull();
    expect(queryByTestId("chats-composer")).toBeNull();
  });

  it("cleans up the landed archive group and reports closed/skipped counts", async () => {
    vi.stubGlobal("BroadcastChannel", FakeBroadcastChannel);
    let catalogSessions = [
      {
        id: "landed",
        label: "Worker",
        kind: "harness",
        harness: "claude",
        leafKey: LEAF_KEY,
        cwd: "/ws",
        tmuxName: "ar-landed",
        createdAt: "2026-07-02T00:00:00Z",
        lastAttachedAt: "2026-07-02T00:00:00Z",
        status: "landed",
      },
      {
        id: "active",
        label: "Active",
        kind: "harness",
        harness: "claude",
        leafKey: SECOND_LEAF_KEY,
        cwd: "/ws",
        tmuxName: "ar-active",
        createdAt: "2026-07-02T00:01:00Z",
        lastAttachedAt: "2026-07-02T00:01:00Z",
        status: "running",
      },
    ];
    let cleanupPayload: unknown;
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
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
        if (url.endsWith("/api/terminal/landed-cleanup")) {
          cleanupPayload = JSON.parse(String(init?.body ?? "{}"));
          catalogSessions = catalogSessions.filter((session) => session.id !== "landed");
          return Promise.resolve({
            ok: true,
            json: () =>
              Promise.resolve({
                closed: 1,
                skipped: 0,
                closedSessions: ["landed"],
                skippedSessions: [],
              }),
          });
        }
        return Promise.reject(new Error(`unexpected url ${url}`));
      }),
    );
    sessionStore.getState().hydrate([
      { id: "landed", label: "Worker", leafKey: LEAF_KEY, status: "landed" },
      { id: "active", label: "Active", leafKey: SECOND_LEAF_KEY, status: "running" },
    ]);

    const { findByTestId } = render(
      <Chats taskDocuments={[leafDoc(), secondLeafDoc()]} />,
    );

    fireEvent.click(await findByTestId("chats-group-cleanup-landed"));

    await waitFor(() =>
      expect(sessionStore.getState().sessions.map((session) => session.id)).toEqual(["active"]),
    );
    expect(cleanupPayload).toEqual({ sessionIds: ["landed"] });
    expect((await findByTestId("chats-landed-cleanup-status")).textContent).toContain(
      "1 closed · 0 skipped",
    );
    expect(FakeBroadcastChannel.messages).toEqual([
      expect.objectContaining({
        type: "terminal-catalog-changed",
        reason: "terminate",
      }),
    ]);
  });

  it("lists projected leaves in the picker and binds the picked leaf on 200 with NO leaf selected", async () => {
    // The decoupling contract: an unattached chat made anywhere (no `selectedLeafKey`) can still be
    // attached to ANY projected leaf through the picker — not only the leaf currently being viewed.
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const url = String(input);
        if (url.endsWith("/api/terminal/s1/attach-leaf")) {
          return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({}) });
        }
        return Promise.reject(new Error(`unexpected url ${url}`));
      }),
    );
    sessionStore.getState().add("Chat", "s1");

    const { findByTestId } = render(<Chats taskDocuments={[leafDoc()]} />);
    // Drill-down picker: open it, then pick the leaf (a lone leaf with no master doc shows at top level),
    // labelled by its task-doc title.
    fireEvent.click(await findByTestId("chats-attach-leaf-picker"));
    fireEvent.click(await findByTestId("chats-attach-leaf-picker-role-worker"));
    const leaf = await findByTestId("chats-attach-leaf-picker-leaf");
    expect(leaf.getAttribute("data-leaf-key")).toBe(LEAF_KEY);
    expect(leaf.textContent).toContain("Sidebar chat attachment");

    fireEvent.click(leaf);

    await waitFor(() => expect(sessionStore.getState().sessions[0]?.leafKey).toBe(LEAF_KEY));
  });

  it("keeps the leaf picker visible for an attached chat and moves it on 200", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const url = String(input);
        if (url.endsWith("/api/terminal/s1/attach-leaf")) {
          return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({}) });
        }
        return Promise.reject(new Error(`unexpected url ${url}`));
      }),
    );
    sessionStore.getState().hydrate([
      { id: "s1", label: "Chat 1", leafKey: LEAF_KEY, status: "running" },
    ]);

    const { findByTestId, findAllByTestId } = render(
      <Chats taskDocuments={[leafDoc(), secondLeafDoc()]} />,
    );

    expect(await findByTestId("chats-leaf-badge")).not.toBeNull();
    fireEvent.click(await findByTestId("chats-attach-leaf-picker"));
    fireEvent.click(await findByTestId("chats-attach-leaf-picker-role-worker"));
    const leaves = await findAllByTestId("chats-attach-leaf-picker-leaf");
    const next = leaves.find((leaf) => leaf.getAttribute("data-leaf-key") === SECOND_LEAF_KEY);
    expect(next).not.toBeUndefined();
    fireEvent.click(next as HTMLElement);

    await waitFor(() => expect(sessionStore.getState().sessions[0]?.leafKey).toBe(SECOND_LEAF_KEY));
  });

  it("does not bind and surfaces a note when the picked leaf is already taken (409)", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const url = String(input);
        if (url.endsWith("/api/terminal/s1/attach-leaf")) {
          return Promise.resolve({ ok: false, status: 409, json: () => Promise.resolve({}) });
        }
        return Promise.reject(new Error(`unexpected url ${url}`));
      }),
    );
    sessionStore.getState().add("Chat", "s1");

    const { findByTestId } = render(<Chats taskDocuments={[leafDoc()]} />);
    fireEvent.click(await findByTestId("chats-attach-leaf-picker"));
    fireEvent.click(await findByTestId("chats-attach-leaf-picker-role-worker"));
    fireEvent.click(await findByTestId("chats-attach-leaf-picker-leaf"));

    const note = await findByTestId("chats-leaf-attach-error");
    expect(note.textContent).toContain("leaf already has a worker seat");
    expect(sessionStore.getState().sessions[0]?.leafKey).toBeUndefined();
  });

  it("rehydrates leaf moves announced by another tab's catalog invalidation", async () => {
    vi.stubGlobal("BroadcastChannel", FakeBroadcastChannel);
    let catalogSessions = [
      {
        id: "s1",
        label: "Chat 1",
        kind: "harness",
        harness: "claude",
        leafKey: LEAF_KEY,
        cwd: "/ws",
        tmuxName: "ar-s1",
        createdAt: "2026-07-02T00:00:00Z",
        lastAttachedAt: "2026-07-02T00:00:00Z",
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

    render(<Chats taskDocuments={[leafDoc(), secondLeafDoc()]} />);
    await waitFor(() => expect(sessionStore.getState().sessions[0]?.leafKey).toBe(LEAF_KEY));

    catalogSessions = [{ ...catalogSessions[0], leafKey: SECOND_LEAF_KEY }];
    act(() => {
      FakeBroadcastChannel.dispatch({
        type: "terminal-catalog-changed",
        source: "other-tab",
        reason: "leaf",
        sessionId: "s1",
      });
    });

    await waitFor(() => expect(sessionStore.getState().sessions[0]?.leafKey).toBe(SECOND_LEAF_KEY));
  });

  it("keeps the launch buttons enabled for free-chat creation with nothing selected", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        json: () =>
          Promise.resolve({ harnesses: [{ id: "claude", name: "Claude Code", detected: true }] }),
      }),
    );

    const { findByTestId, getByTestId } = render(<Chats />);

    expect((getByTestId("chats-new-terminal") as HTMLButtonElement).disabled).toBe(false);
    expect(((await findByTestId("chats-new-harness-claude")) as HTMLButtonElement).disabled).toBe(
      false,
    );
  });

  it("labels a leaf-bound session row with the resolved leaf title", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("no backend")));
    sessionStore.getState().hydrate([
      { id: "s1", label: "Chat 1", leafKey: LEAF_KEY, status: "running" },
    ]);

    const { findByTestId } = render(<Chats taskDocuments={[leafDoc()]} />);

    const leafLabel = await findByTestId("chats-session-leaf-s1");
    expect(leafLabel.textContent).toContain("Sidebar chat attachment");
  });

  it("falls back to the leaf id when no task-doc title resolves", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("no backend")));
    sessionStore.getState().hydrate([
      { id: "s1", label: "Chat 1", leafKey: LEAF_KEY, status: "running" },
    ]);

    const { findByTestId } = render(<Chats taskDocuments={[]} />);

    const leafLabel = await findByTestId("chats-session-leaf-s1");
    expect(leafLabel.textContent).toContain("260628-L5");
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

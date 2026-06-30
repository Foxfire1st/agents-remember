import { act, cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { deliverToSession, findSessionForLeaf, sessionStore } from "../data/sessions";
import type { EngineProcessNode, TaskDocNode } from "../types/projection";
import { RailChat } from "./RailChat";

const LEAF_KEY = "agents-remember/260628_operations-integration/260628-L5";

vi.mock("../data/sessions", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../data/sessions")>();
  return { ...actual, deliverToSession: vi.fn() };
});

// A minimal task-doc whose qualified leaf id (`repo / dir(docPath) basename / id`) equals LEAF_KEY.
// `kind: "subTask"` marks it as a leaf so the "Attach to leaf ▾" picker lists it.
function leafDoc(): TaskDocNode {
  return {
    id: "260628-L5",
    lifecycleId: "lc-l5",
    repository: "agents-remember",
    kind: "subTask",
    status: "planning",
    docPath: "/tasks/agents-remember/260628_operations-integration/05_sidebar-chat-attachment.json",
    title: "Sidebar chat attachment",
    objective: "Bind a chat to a durable leaf identity.",
    requirements: ["Start from the selected leaf.", "Do not inject on rejected attach."],
    steps: [
      { id: "S1", title: "Wire the leaf registry", status: "done", substeps: [] },
      { id: "S2", title: "Add the rail chat", status: "inProgress", substeps: [] },
    ],
  } as unknown as TaskDocNode;
}

function engineProcess(): EngineProcessNode {
  return {
    id: "enc",
    enclosure: "enc",
    worktreeGroup: "/worktrees/sidebar-chat-ar",
    taskId: "260628_OPERATIONS-INTEGRATION",
    leafId: "260628-l5",
    taskName: "260628_operations-integration",
    repoName: "agents-remember",
    lifecycleId: "lc-l5",
    phase: "worktree-started",
    health: "nominal",
    codeSource: { factState: "observed" },
    codeWorktree: { path: "/worktrees/sidebar-chat-ar/sidebar-chat", factState: "observed" },
    memoryMode: "external",
    memoryWorktree: { path: "/worktrees/sidebar-chat-ar/memory-sidebar-chat", factState: "observed" },
    ledgerRows: [],
    ledgerRowCount: 0,
    humanReviewStatus: "pending-review",
    closeoutStatus: "not-started",
    integrationStatus: "not-started",
    cleanup: "pending",
    completedPhases: [],
    failedPhases: [],
    seedFallback: false,
    providers: [],
    edges: [],
    actions: [],
    summary: "ready",
    missingFacts: [],
    sourceFiles: [],
  } as unknown as EngineProcessNode;
}

// Mock the lazy Terminal so opening a session never pulls xterm (a canvas probe) into jsdom; the stub
// just marks its sessionId so a test can assert which session terminals are mounted.
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
  }
  close(): void {
    this.closed = true;
  }
  static reset(): void {
    FakeBroadcastChannel.instances = [];
    FakeBroadcastChannel.messages = [];
  }
}

beforeEach(() => {
  vi.mocked(deliverToSession).mockResolvedValue("delivered");
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  vi.unstubAllGlobals();
  sessionStore.setState({ sessions: [], activeId: null, count: 0 });
  FakeBroadcastChannel.reset();
});

describe("RailChat start affordances (L5 fix 2)", () => {
  it("offers a harness choice (Claude/Codex) plus an open-terminal control for an empty leaf", async () => {
    const harnesses = [
      { id: "claude", name: "Claude Code", detected: true },
      { id: "codex", name: "Codex", detected: true },
      { id: "pi", name: "Pi.dev", detected: false },
    ];
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: true, json: () => Promise.resolve({ harnesses }) }),
    );

    const { findByTestId, queryByTestId, getByTestId } = render(<RailChat leafKey={LEAF_KEY} />);

    expect(await findByTestId("rail-start-chat-claude")).not.toBeNull();
    expect(getByTestId("rail-start-chat-codex")).not.toBeNull();
    expect(queryByTestId("rail-start-chat-pi")).toBeNull(); // undetected → no button
    expect(getByTestId("rail-open-terminal")).not.toBeNull();
  });

  it("starts a leaf chat as a harness session keyed to the leaf", async () => {
    vi.stubGlobal("crypto", { randomUUID: () => "chat-id" });
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const url = String(input);
        if (url.endsWith("/api/harnesses")) {
          return Promise.resolve({
            ok: true,
            json: () => Promise.resolve({ harnesses: [{ id: "claude", name: "Claude Code", detected: true }] }),
          });
        }
        return Promise.resolve({ ok: true }); // the opener POST
      }),
    );

    const { findByTestId } = render(<RailChat leafKey={LEAF_KEY} />);
    fireEvent.click(await findByTestId("rail-start-chat-claude"));

    await waitFor(() => {
      const session = findSessionForLeaf(LEAF_KEY, "chat");
      expect(session?.kind).toBe("harness");
      expect(session?.harness).toBe("claude");
    });
  });

  it("delivers leaf context after starting an agent chat on the viewed leaf", async () => {
    vi.stubGlobal("crypto", { randomUUID: () => "chat-id" });
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const url = String(input);
        if (url.endsWith("/api/harnesses")) {
          return Promise.resolve({
            ok: true,
            json: () => Promise.resolve({ harnesses: [{ id: "claude", name: "Claude Code", detected: true }] }),
          });
        }
        return Promise.resolve({ ok: true });
      }),
    );

    const { findByTestId } = render(
      <RailChat leafKey={LEAF_KEY} taskDocuments={[leafDoc()]} engineProcesses={[engineProcess()]} />,
    );
    fireEvent.click(await findByTestId("rail-start-chat-claude"));

    await waitFor(() => expect(deliverToSession).toHaveBeenCalledWith("chat-id", expect.any(String)));
    const packet = vi.mocked(deliverToSession).mock.calls[0]?.[1] ?? "";
    expect(packet).toContain("Task: 260628-L5 -- Sidebar chat attachment");
    expect(packet).toContain(`Leaf key: ${LEAF_KEY}`);
    expect(packet).toContain("Lifecycle: lc-l5");
    expect(packet).toContain("Code worktree: /worktrees/sidebar-chat-ar/sidebar-chat");
    expect(packet).toContain("- [done] S1 -- Wire the leaf registry");
  });
});

describe("RailChat create from anywhere (L5)", () => {
  it("offers start affordances with NO leaf selected — never blocks on a leaf", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        json: () => Promise.resolve({ harnesses: [{ id: "claude", name: "Claude Code", detected: true }] }),
      }),
    );

    const { findByTestId, getByTestId, queryByTestId } = render(<RailChat />); // NO leafKey

    expect(await findByTestId("rail-start-chat-claude")).not.toBeNull();
    expect(getByTestId("rail-open-terminal")).not.toBeNull();
    // The old "open a task leaf to start a chat" block must be gone.
    expect(queryByTestId("rail-chat-no-leaf")).toBeNull();
  });

  it("starts a chat off-leaf and leaves it unattached (free chat)", async () => {
    vi.stubGlobal("crypto", { randomUUID: () => "free-chat" });
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const url = String(input);
        if (url.endsWith("/api/harnesses")) {
          return Promise.resolve({
            ok: true,
            json: () => Promise.resolve({ harnesses: [{ id: "claude", name: "Claude Code", detected: true }] }),
          });
        }
        return Promise.resolve({ ok: true }); // the opener POST
      }),
    );

    const { findByTestId } = render(<RailChat />); // NO leafKey
    fireEvent.click(await findByTestId("rail-start-chat-claude"));

    await waitFor(() => {
      const free = sessionStore.getState().sessions.find((s) => s.kind === "harness" && !s.leafKey);
      expect(free?.harness).toBe("claude");
    });
    expect(deliverToSession).not.toHaveBeenCalled();
  });

  it("offers an attach-to-leaf picker for a free chat, binds the picked leaf, and delivers context", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const url = String(input);
        if (url.endsWith("/api/terminal/f1/attach-leaf")) {
          return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({}) });
        }
        return Promise.reject(new Error("no backend")); // harnesses fetch tolerated → []
      }),
    );
    sessionStore.getState().hydrate([
      { id: "f1", label: "Claude Code 1", kind: "harness", harness: "claude", status: "running" },
    ]);

    const { findByTestId } = render(
      <RailChat taskDocuments={[leafDoc()]} engineProcesses={[engineProcess()]} />,
    ); // NO leafKey
    // Drill-down picker: open it, then pick the leaf (a lone leaf with no master doc shows at top level).
    fireEvent.click(await findByTestId("rail-attach-leaf-picker"));
    const leaf = await findByTestId("rail-attach-leaf-picker-leaf");
    expect(leaf.getAttribute("data-leaf-key")).toBe(LEAF_KEY);

    fireEvent.click(leaf);

    await waitFor(() => expect(sessionStore.getState().sessions[0]?.leafKey).toBe(LEAF_KEY));
    await waitFor(() => expect(deliverToSession).toHaveBeenCalledWith("f1", expect.any(String)));
    expect(vi.mocked(deliverToSession).mock.calls[0]?.[1]).toContain("Memory worktree: /worktrees/sidebar-chat-ar/memory-sidebar-chat");
  });

  it("surfaces a note when the picked leaf is already taken (409) and does not bind", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const url = String(input);
        if (url.endsWith("/api/terminal/f1/attach-leaf")) {
          return Promise.resolve({ ok: false, status: 409, json: () => Promise.resolve({}) });
        }
        return Promise.reject(new Error("no backend"));
      }),
    );
    sessionStore.getState().hydrate([
      { id: "f1", label: "Claude Code 1", kind: "harness", harness: "claude", status: "running" },
    ]);

    const { findByTestId } = render(<RailChat taskDocuments={[leafDoc()]} />); // NO leafKey
    fireEvent.click(await findByTestId("rail-attach-leaf-picker"));
    fireEvent.click(await findByTestId("rail-attach-leaf-picker-leaf"));

    const note = await findByTestId("rail-leaf-attach-error");
    expect(note.textContent).toContain("leaf already has a chat");
    expect(sessionStore.getState().sessions[0]?.leafKey).toBeUndefined();
    expect(deliverToSession).not.toHaveBeenCalled();
  });

  it("surfaces unconfirmed context delivery after a successful leaf bind", async () => {
    vi.mocked(deliverToSession).mockResolvedValue("unconfirmed");
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const url = String(input);
        if (url.endsWith("/api/terminal/f1/attach-leaf")) {
          return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({}) });
        }
        return Promise.reject(new Error("no backend"));
      }),
    );
    sessionStore.getState().hydrate([
      { id: "f1", label: "Claude Code 1", kind: "harness", harness: "claude", status: "running" },
    ]);

    const { findByTestId } = render(<RailChat taskDocuments={[leafDoc()]} />);
    fireEvent.click(await findByTestId("rail-attach-leaf-picker"));
    fireEvent.click(await findByTestId("rail-attach-leaf-picker-leaf"));

    const note = await findByTestId("rail-leaf-context-note");
    expect(note.textContent).toContain("context delivery unconfirmed");
  });
});

describe("RailChat chat + terminal split (L5 fix 2)", () => {
  it("splits into a chat pane and a terminal pane when the leaf has both", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("no backend")));
    sessionStore.getState().hydrate([
      { id: "c1", label: "Claude Code 1", kind: "harness", harness: "claude", leafKey: LEAF_KEY, status: "running" },
      { id: "t1", label: "Terminal 1", kind: "terminal", leafKey: LEAF_KEY, status: "running" },
    ]);

    const { findByTestId, getByTestId } = render(<RailChat leafKey={LEAF_KEY} />);

    expect(await findByTestId("rail-pane-chat")).not.toBeNull();
    expect(getByTestId("rail-pane-terminal")).not.toBeNull();
    expect(getByTestId("term-c1")).not.toBeNull();
    expect(getByTestId("term-t1")).not.toBeNull();
  });

  it("offers open-terminal beside an existing chat (no terminal yet)", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("no backend")));
    sessionStore.getState().hydrate([
      { id: "c1", label: "Claude Code 1", kind: "harness", leafKey: LEAF_KEY, status: "running" },
    ]);

    const { findByTestId, getByTestId, queryByTestId } = render(<RailChat leafKey={LEAF_KEY} />);

    expect(await findByTestId("rail-pane-chat")).not.toBeNull();
    expect(getByTestId("rail-open-terminal")).not.toBeNull();
    expect(queryByTestId("rail-pane-terminal")).toBeNull();
  });
});

describe("RailChat terminate (L5 fix 3)", () => {
  it("ends the chat through the backend and frees the leaf's chat slot", async () => {
    vi.stubGlobal("BroadcastChannel", FakeBroadcastChannel);
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const url = String(input);
        if (url.endsWith("/api/terminal/c1/terminate")) return Promise.resolve({ ok: true });
        return Promise.reject(new Error("no backend"));
      }),
    );
    sessionStore.getState().hydrate([
      { id: "c1", label: "Claude Code 1", kind: "harness", leafKey: LEAF_KEY, status: "running" },
    ]);

    const { findByTestId, queryByTestId } = render(<RailChat leafKey={LEAF_KEY} />);
    fireEvent.click(await findByTestId("rail-terminate-chat"));

    await waitFor(() => expect(findSessionForLeaf(LEAF_KEY, "chat")).toBeUndefined());
    // The chat slot is freed → the start affordance returns; the pane is gone.
    await waitFor(() => expect(queryByTestId("rail-pane-chat")).toBeNull());
    expect(FakeBroadcastChannel.messages).toEqual([
      expect.objectContaining({ type: "terminal-catalog-changed", reason: "terminate", sessionId: "c1" }),
    ]);
  });

  it("ends the terminal pane independently of the chat", async () => {
    vi.stubGlobal("BroadcastChannel", FakeBroadcastChannel);
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const url = String(input);
        if (url.endsWith("/api/terminal/t1/terminate")) return Promise.resolve({ ok: true });
        return Promise.reject(new Error("no backend"));
      }),
    );
    sessionStore.getState().hydrate([
      { id: "c1", label: "Claude Code 1", kind: "harness", leafKey: LEAF_KEY, status: "running" },
      { id: "t1", label: "Terminal 1", kind: "terminal", leafKey: LEAF_KEY, status: "running" },
    ]);

    const { findByTestId, getByTestId, queryByTestId } = render(<RailChat leafKey={LEAF_KEY} />);
    fireEvent.click(await findByTestId("rail-terminate-terminal"));

    await waitFor(() => expect(findSessionForLeaf(LEAF_KEY, "terminal")).toBeUndefined());
    // The chat survives the terminal's termination.
    await act(async () => {
      await Promise.resolve();
    });
    expect(getByTestId("rail-pane-chat")).not.toBeNull();
    expect(queryByTestId("rail-pane-terminal")).toBeNull();
  });
});

import { act, cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { sessionCockpitStore } from "../data/sessionCockpitStore";
import { findSessionForTask, fromTerminalSessionInfo, sessionStore } from "../data/sessions";
import { submitSessionText, waitForSubmissionReady } from "../data/submitClient";
import { startSubmitRecord } from "../data/submitMachine";
import { dashboardStore } from "../data/store";
import { L6_INTERACTION_FREETEXT } from "../test/fixtures/catalogRows";
import { engineProcess, taskDoc } from "../test/fixtures/wire";
import type { EngineProcessNode, TaskDocNode } from "../types/projection";
import { RailChat } from "./RailChat";

const LEAF_KEY = "agents-remember/260628_operations-integration/260628-L5";
const LEAF_REF = {
  repository: "agents-remember",
  path: "260628_operations-integration/05_sidebar-chat-attachment.json",
};
const SPRINT_REF = {
  repository: "agents-remember",
  path: "260628_operations-sprint/task.json",
};

vi.mock("../data/submitClient", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../data/submitClient")>();
  return {
    ...actual,
    submitSessionText: vi.fn(),
    waitForSubmissionReady: vi.fn(),
  };
});

// A minimal task-doc whose qualified leaf id (`repo / dir(docPath) basename / id`) equals LEAF_KEY.
// `kind: "subTask"` marks it as a leaf so the "Attach to leaf ▾" picker lists it.
function leafDoc(): TaskDocNode {
  return taskDoc({
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
      {
        id: "S2",
        title: "Add the rail chat",
        status: "done",
        disposition: {
          kind: "intentionalSkip",
          reason: "The existing rail already covers it.",
          recordedAt: "2026-08-03T12:00:00+00:00",
          recordedVia: "task_doc.skip_step",
        },
        substeps: [
          {
            id: "S2.1",
            title: "Add a duplicate control",
            status: "done",
            disposition: {
              kind: "intentionalSkip",
              reason: "Duplicate control is unnecessary.",
              recordedAt: "2026-08-03T12:00:00+00:00",
              recordedVia: "task_doc.skip_step",
            },
          },
        ],
      },
    ],
  });
}

function sprintDoc(): TaskDocNode {
  return taskDoc({
    id: "260628-OPERATIONS-SPRINT",
    repository: "agents-remember",
    kind: "master",
    status: "planning",
    docPath: "/tasks/agents-remember/260628_operations-sprint/task.json",
    title: "Operations sprint",
    objective: "Coordinate the sprint portfolio.",
    orchestrates: ["260628_operations-integration"],
  });
}

// The process the leaf doc joins to (`lifecycleId`) — it is where the leaf-context packet reads the
// worktree group and the two worktree paths from.
function leafProcess(): EngineProcessNode {
  return engineProcess({
    id: "enc",
    enclosure: "enc",
    worktreeGroup: "/worktrees/sidebar-chat-ar",
    taskId: "260628_OPERATIONS-INTEGRATION",
    leafId: "260628-l5",
    taskName: "260628_operations-integration",
    repoName: "agents-remember",
    lifecycleId: "lc-l5",
    codeWorktree: { path: "/worktrees/sidebar-chat-ar/sidebar-chat", factState: "observed" },
    memoryWorktree: { path: "/worktrees/sidebar-chat-ar/memory-sidebar-chat", factState: "observed" },
  });
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

function openedHarnessResponse(
  id: string,
  taskDocumentRef?: typeof LEAF_REF,
  seatRole?: string,
): Response {
  return new Response(
    JSON.stringify({
      session: id,
      label: "Claude Code 1",
      kind: "harness",
      harness: "claude",
      lifecycleId: null,
      taskDocumentRef: taskDocumentRef ?? null,
      seatRole: seatRole ?? null,
      status: "running",
      controlState: "starting",
    }),
    { status: 200 },
  );
}

beforeEach(() => {
  vi.mocked(waitForSubmissionReady).mockResolvedValue({ ready: true, editable: true });
  vi.mocked(submitSessionText).mockImplementation(async (_sessionId, text) => ({
    status: "started",
    record: {
      ...startSubmitRecord({
        requestId: "context-request",
        text,
        expectedBridgeEpoch: "bridge-epoch-l5",
        submittedRevision: 0,
        at: 1,
      }),
      phase: "accepted",
    },
  }));
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  vi.unstubAllGlobals();
  sessionStore.setState({ sessions: [], activeId: null, count: 0 });
  sessionCockpitStore.setState({ perSession: {} });
  dashboardStore.setState({ lifecycles: {} });
  FakeBroadcastChannel.reset();
});

describe("RailChat task-projected role controls (EFA-L19)", () => {
  it("does not expose generic chat or terminal creation from a leaf task", async () => {
    const harnesses = [
      { id: "claude", name: "Claude Code", detected: true },
      { id: "codex", name: "Codex", detected: true },
      { id: "pi", name: "Pi.dev", detected: false },
    ];
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: true, json: () => Promise.resolve({ harnesses }) }),
    );

    const { findByTestId, queryByTestId } = render(
      <RailChat leafKey={LEAF_KEY} taskDocumentRef={LEAF_REF} taskDocuments={[leafDoc()]} />,
    );

    expect((await findByTestId("rail-chat-empty")).textContent).toContain(
      "No worker, reviewer, or curator chat occupies this leaf yet.",
    );
    expect(queryByTestId("rail-start-chat-claude")).toBeNull();
    expect(queryByTestId("rail-start-chat-codex")).toBeNull();
    expect(queryByTestId("rail-open-terminal")).toBeNull();
  });

  it("switches among existing sprint-role chats and offers creation only for missing roles", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("no backend")));
    sessionStore.getState().hydrate([
      {
        id: "architect",
        label: "Architect",
        kind: "harness",
        seatRole: "architect",
        taskDocumentRef: SPRINT_REF,
        status: "running",
      },
      {
        id: "strategist",
        label: "Strategist",
        kind: "harness",
        seatRole: "strategist",
        taskDocumentRef: SPRINT_REF,
        status: "running",
      },
    ]);

    const { findByTestId, getByRole, queryByTestId } = render(
      <RailChat taskDocumentRef={SPRINT_REF} taskDocuments={[sprintDoc()]} />,
    );

    expect((await findByTestId("rail-pane-chat")).textContent).toContain("architect · Architect");
    fireEvent.click(getByRole("button", { name: "Strategist" }));
    await waitFor(async () =>
      expect((await findByTestId("rail-pane-chat")).textContent).toContain(
        "strategist · Strategist",
      ),
    );
    expect(queryByTestId("rail-create-sprint-role-architect")).toBeNull();
    expect(queryByTestId("rail-create-sprint-role-strategist")).toBeNull();
    expect(await findByTestId("rail-create-sprint-role-orchestrator")).not.toBeNull();
    expect(await findByTestId("rail-create-sprint-role-designer")).not.toBeNull();
    expect(await findByTestId("rail-create-sprint-role-system-specialist")).not.toBeNull();
  });

  it("creates a missing sprint role on the sprint document and removes its create button", async () => {
    vi.stubGlobal("crypto", { randomUUID: () => "orchestrator-id" });
    const requestBodies: Record<string, unknown>[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url.endsWith("/api/harnesses")) {
          return Promise.resolve({
            ok: true,
            json: () => Promise.resolve({ harnesses: [{ id: "claude", name: "Claude Code", detected: true }] }),
          });
        }
        requestBodies.push(JSON.parse(String(init?.body)) as Record<string, unknown>);
        return Promise.resolve(openedHarnessResponse("orchestrator-id", SPRINT_REF, "orchestrator"));
      }),
    );

    const { findByTestId, queryByTestId } = render(
      <RailChat taskDocumentRef={SPRINT_REF} taskDocuments={[sprintDoc()]} />,
    );
    fireEvent.click(await findByTestId("rail-create-sprint-role-orchestrator"));

    await waitFor(() => {
      const session = sessionStore.getState().sessions.find((candidate) => candidate.id === "orchestrator-id");
      expect(session).toMatchObject({
        taskDocumentRef: SPRINT_REF,
        seatRole: "orchestrator",
        kind: "harness",
      });
    });
    expect(requestBodies).toContainEqual(
      expect.objectContaining({ taskDocumentRef: SPRINT_REF, role: "orchestrator" }),
    );
    expect(queryByTestId("rail-create-sprint-role-orchestrator")).toBeNull();
    expect((await findByTestId("rail-pane-chat")).textContent).toContain("orchestrator");
  });

  it("surfaces a rejected sprint-role open without a ghost row", async () => {
    vi.stubGlobal("crypto", { randomUUID: () => "rejected-chat" });
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/harnesses")) {
        return Promise.resolve(
          new Response(
            JSON.stringify({
              harnesses: [{ id: "claude", name: "Claude Code", detected: true }],
            }),
            { status: 200 },
          ),
        );
      }
      return Promise.resolve(
        new Response(JSON.stringify({ status: "bad-kind", detail: "claude not installed" }), {
          status: 400,
        }),
      );
    });
    vi.stubGlobal("fetch", fetchMock);

    const { findByTestId } = render(
      <RailChat taskDocumentRef={SPRINT_REF} taskDocuments={[sprintDoc()]} />,
    );
    fireEvent.click(await findByTestId("rail-create-sprint-role-architect"));

    expect((await findByTestId("rail-session-open-error")).textContent).toContain(
      "session open harness",
    );
    expect(sessionStore.getState().sessions).toEqual([]);
    expect(submitSessionText).not.toHaveBeenCalled();
    expect(
      fetchMock.mock.calls.filter(([input]) => String(input).includes("/api/terminal/rejected-chat")),
    ).toHaveLength(1);
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
        return Promise.resolve(openedHarnessResponse("free-chat"));
      }),
    );

    const { findByTestId } = render(<RailChat />); // NO leafKey
    fireEvent.click(await findByTestId("rail-start-chat-claude"));

    await waitFor(() => {
      const free = sessionStore.getState().sessions.find((s) => s.kind === "harness" && !s.taskDocumentRef);
      expect(free?.harness).toBe("claude");
    });
    expect(submitSessionText).not.toHaveBeenCalled();
  });

  it("offers an attach-to-leaf picker for a free chat, binds the picked leaf, and delivers context", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const url = String(input);
        if (url.endsWith("/api/terminal/f1/attach-task")) {
          return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({}) });
        }
        return Promise.reject(new Error("no backend")); // harnesses fetch tolerated → []
      }),
    );
    sessionStore.getState().hydrate([
      { id: "f1", label: "Claude Code 1", kind: "harness", harness: "claude", status: "running" },
    ]);

    const { findByTestId } = render(
      <RailChat taskDocuments={[leafDoc()]} engineProcesses={[leafProcess()]} />,
    ); // NO leafKey
    // Drill-down picker: open it, then pick the leaf (a lone leaf with no master doc shows at top level).
    fireEvent.click(await findByTestId("rail-attach-leaf-picker"));
    fireEvent.click(await findByTestId("rail-attach-leaf-picker-role-worker"));
    const leaf = await findByTestId("rail-attach-leaf-picker-leaf");
    expect(leaf.getAttribute("data-leaf-key")).toBe(LEAF_KEY);

    fireEvent.click(leaf);

    await waitFor(() => expect(sessionStore.getState().sessions[0]?.taskDocumentRef).toEqual(LEAF_REF));
    await waitFor(() =>
      expect(submitSessionText).toHaveBeenCalledWith("f1", expect.any(String), {
        source: "leaf-context",
        clearDraftOnAccept: false,
      }),
    );
    expect(vi.mocked(submitSessionText).mock.calls[0]?.[1]).toContain("Memory worktree: /worktrees/sidebar-chat-ar/memory-sidebar-chat");
  });

  it("surfaces a note when the picked leaf is already taken (409) and does not bind", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const url = String(input);
        if (url.endsWith("/api/terminal/f1/attach-task")) {
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
    fireEvent.click(await findByTestId("rail-attach-leaf-picker-role-worker"));
    fireEvent.click(await findByTestId("rail-attach-leaf-picker-leaf"));

    const note = await findByTestId("rail-leaf-attach-error");
    expect(note.textContent).toContain("task document already has a worker seat");
    expect(sessionStore.getState().sessions[0]?.taskDocumentRef).toBeUndefined();
    expect(submitSessionText).not.toHaveBeenCalled();
  });

  it("surfaces a rejected context receipt after a successful leaf bind", async () => {
    vi.mocked(submitSessionText).mockResolvedValue({
      status: "started",
      record: {
        ...startSubmitRecord({
          requestId: "context-rejected",
          text: "context",
          expectedBridgeEpoch: "bridge-epoch-l5",
          submittedRevision: 0,
          at: 1,
        }),
        phase: "rejected",
        detail: "context delivery rejected: queue full",
      },
    });
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const url = String(input);
        if (url.endsWith("/api/terminal/f1/attach-task")) {
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
    fireEvent.click(await findByTestId("rail-attach-leaf-picker-role-worker"));
    fireEvent.click(await findByTestId("rail-attach-leaf-picker-leaf"));

    const note = await findByTestId("rail-leaf-context-note");
    expect(note.textContent).toContain("context delivery rejected: queue full");
  });
});

describe("RailChat chat + terminal split (L5 fix 2)", () => {
  it("routes a pane's lifecycle-free non-choice answer by exact session and never /submit", async () => {
    const session = fromTerminalSessionInfo({
      ...L6_INTERACTION_FREETEXT,
      id: "rail-answer",
      lifecycleId: undefined,
      taskDocumentRef: LEAF_REF,
      controlPendingInteraction: {
        ...L6_INTERACTION_FREETEXT.controlPendingInteraction,
        interactionId: "ix-rail-answer",
      },
    });
    sessionStore.getState().hydrate([session]);
    const urls: string[] = [];
    const responseBodies: Record<string, unknown>[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        urls.push(url);
        if (url.endsWith("/submission-authority")) {
          return {
            ok: true,
            json: async () => ({ bridgeEpoch: "bridge-rail-answer" }),
          } as Response;
        }
        if (url === "/api/terminal/rail-answer/interaction-response") {
          responseBodies.push(JSON.parse(String(init?.body)) as Record<string, unknown>);
          return {
            ok: true,
            status: 200,
            json: async () => ({ status: "accepted" }),
          } as Response;
        }
        throw new Error(`no backend for ${url}`);
      }),
    );
    const { findByTestId } = render(
      <RailChat leafKey={LEAF_KEY} taskDocumentRef={LEAF_REF} taskDocuments={[leafDoc()]} />,
    );
    await findByTestId("session-composer-answer-mode");
    act(() => sessionCockpitStore.getState().setComposerDraft(session.id, "use ar/base"));
    fireEvent.click(await findByTestId("session-composer-send"));
    await waitFor(() => expect(responseBodies).toHaveLength(1));
    expect(responseBodies[0]).toEqual({
      interactionId: "ix-rail-answer",
      expectedBridgeEpoch: "bridge-rail-answer",
      response: "use ar/base",
    });
    expect(submitSessionText).not.toHaveBeenCalled();
    expect(urls.some((url) => url.endsWith("/submit"))).toBe(false);
  });

  it("shows the current leaf binding role instead of stale spawn provenance", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("no backend")));
    sessionStore.getState().hydrate([
      {
        id: "c1",
        label: "Claude Code 1",
        kind: "harness",
        spawnRole: "worker",
        seatRole: "reviewer",
        taskDocumentRef: LEAF_REF,
        status: "running",
      },
    ]);

    const { findByTestId } = render(
      <RailChat leafKey={LEAF_KEY} taskDocumentRef={LEAF_REF} taskDocuments={[leafDoc()]} />,
    );

    expect((await findByTestId("rail-pane-chat")).textContent).toContain("reviewer · Claude Code 1");
  });

  it("splits an unattached chat and raw terminal without assigning either to a task seat", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("no backend")));
    sessionStore.getState().hydrate([
      { id: "c1", label: "Claude Code 1", kind: "harness", harness: "claude", status: "running" },
      { id: "t1", label: "Terminal 1", kind: "terminal", status: "running" },
    ]);

    const { findByTestId, getByTestId } = render(<RailChat />);

    expect(await findByTestId("rail-pane-chat")).not.toBeNull();
    expect(getByTestId("rail-pane-terminal")).not.toBeNull();
    expect(getByTestId("term-c1")).not.toBeNull();
    expect(getByTestId("term-t1")).not.toBeNull();
  });

  it("does not offer a raw terminal beside a task-bound role chat", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("no backend")));
    sessionStore.getState().hydrate([
      { id: "c1", label: "Claude Code 1", kind: "harness", seatRole: "worker", taskDocumentRef: LEAF_REF, status: "running" },
    ]);

    const { findByTestId, queryByTestId } = render(
      <RailChat leafKey={LEAF_KEY} taskDocumentRef={LEAF_REF} taskDocuments={[leafDoc()]} />,
    );

    expect(await findByTestId("rail-pane-chat")).not.toBeNull();
    expect(queryByTestId("rail-open-terminal")).toBeNull();
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
      { id: "c1", label: "Claude Code 1", kind: "harness", seatRole: "worker", taskDocumentRef: LEAF_REF, status: "running" },
    ]);

    const { findByTestId, queryByTestId } = render(
      <RailChat leafKey={LEAF_KEY} taskDocumentRef={LEAF_REF} taskDocuments={[leafDoc()]} />,
    );
    fireEvent.click(await findByTestId("rail-terminate-chat"));

    await waitFor(() => expect(findSessionForTask(LEAF_REF, "chat")).toBeUndefined());
    // The canonical worker seat is free and the pane is gone; task-bound generic launch stays hidden.
    await waitFor(() => expect(queryByTestId("rail-pane-chat")).toBeNull());
    expect(FakeBroadcastChannel.messages).toEqual([
      expect.objectContaining({ type: "terminal-catalog-changed", reason: "terminate", sessionId: "c1" }),
    ]);
  });

  it("ends an unattached raw terminal independently of an unattached chat", async () => {
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
      { id: "c1", label: "Claude Code 1", kind: "harness", status: "running" },
      { id: "t1", label: "Terminal 1", kind: "terminal", status: "running" },
    ]);

    const { findByTestId, getByTestId, queryByTestId } = render(<RailChat />);
    fireEvent.click(await findByTestId("rail-terminate-terminal"));

    await waitFor(() =>
      expect(sessionStore.getState().sessions.find((session) => session.id === "t1")).toBeUndefined(),
    );
    // The chat survives the terminal's termination.
    await act(async () => {
      await Promise.resolve();
    });
    expect(getByTestId("rail-pane-chat")).not.toBeNull();
    expect(queryByTestId("rail-pane-terminal")).toBeNull();
  });
});

import { act, cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { sessionStore } from "../data/sessions";
import { dashboardStore } from "../data/store";
import { GALLERY } from "../dev/fixtures";
import type {
  EnclosureNode,
  LifecycleProjection,
  TaskDocNode,
  WorkspaceProjection,
} from "../types/projection";
import { CockpitShell } from "./Cockpit";

function seed(stateName: string) {
  const fixture = GALLERY.find((entry) => entry.name === stateName);
  if (!fixture) throw new Error(`fixture not found: ${stateName}`);
  dashboardStore.getState().applySnapshot(fixture.projection);
}

// Mock the lazy Terminal so toggling the rail to chat never pulls xterm (a canvas probe) into jsdom.
vi.mock("../panels/Terminal", () => ({
  Terminal: ({ sessionId }: { sessionId: string }) => <div data-testid={`term-${sessionId}`} />,
}));

function taskDoc(over: Partial<TaskDocNode> & Pick<TaskDocNode, "kind" | "docPath" | "id">): TaskDocNode {
  return {
    lifecycleId: "ROOT",
    repository: "repo-a",
    title: "doc",
    status: "inProgress",
    createdAt: "2026-06-20T09:00:00+00:00",
    stepsDone: 0,
    stepsTotal: 0,
    steps: [],
    objective: "",
    requirements: [],
    codeExamples: [],
    decisions: [],
    openQuestions: [],
    references: [],
    subTasks: [],
    sections: [],
    ...over,
  } as TaskDocNode;
}

// A lifecycle-bound master with one authored, drillable leaf — the drilled-leaf fixture for fix 1.
function seedDrillableMaster() {
  const lc: LifecycleProjection = {
    id: "ROOT",
    state: "running",
    phase: "build",
    fleeting: false,
    repoId: "repo-a",
    tokens: 0,
    startedAt: "2026-06-20T09:00:00+00:00",
    lastEventTs: "2026-06-20T09:00:30+00:00",
    inferred: false,
    actions: [],
    tokenSeries: [],
  };
  const master = taskDoc({
    id: "master-x",
    kind: "master",
    title: "Ops Master",
    docPath: "/tasks/repo-a/ops/task.json",
    objective: "Master objective.",
    subTasks: [
      {
        number: "1",
        name: "Leaf One",
        file: "01_leaf.md",
        status: "inProgress",
        scope: "",
        createdAt: "2026-06-20T09:00:00+00:00",
      },
    ],
  });
  const leaf = taskDoc({
    id: "leaf-one",
    kind: "subTask",
    title: "Leaf One",
    docPath: "/tasks/repo-a/ops/01_leaf.json",
    objective: "Leaf objective.",
  });
  const projection: WorkspaceProjection = {
    version: 2,
    generatedAt: "2026-06-20T09:01:00+00:00",
    lifecycles: [lc],
    enclosures: [],
    providers: [],
    activeWorktreeGroups: [],
    metrics: {
      lifecycleCount: 1,
      runningCount: 1,
      blockedCount: 0,
      pausedCount: 0,
      totalTokens: 0,
      stalenessHistogram: {},
    },
    analytics: {
      driftSnapshots: [],
      stalestSidecars: [],
      setupSummaries: [],
      setupProgress: [],
      routeCoverage: [],
      toolReports: [],
      ledgers: [],
      taskDocuments: [master, leaf],
      series: [],
      attentionQueue: [],
      engineProcesses: [],
    },
  };
  dashboardStore.getState().applySnapshot(projection);
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  sessionStore.setState({ sessions: [], activeId: null, count: 0 });
  dashboardStore.getState().reset();
  window.localStorage.clear(); // the rail toggle now persists its choice; isolate it between tests
});

function deferred() {
  let resolve!: () => void;
  const promise = new Promise<void>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

function liveEnclosure(
  leafId: string,
  lifecycleId: string,
  enclosureId = leafId,
): EnclosureNode {
  return {
    enclosure: `/contracts/${enclosureId}`,
    enclosureId,
    leafId,
    taskRoot: "/tasks/repo-a/ops",
    taskId: "ops-master",
    taskName: "ops",
    repoName: "repo-a",
    lifecycleId,
    worktreeGroup: `/worktrees/${enclosureId}`,
    humanReviewStatus: "pending-review",
    closeoutStatus: "not-started",
    integrationStatus: "not-started",
    cleanup: "pending",
    codeWorktreeExists: true,
    memoryWorktreeExists: true,
    actions: [],
  };
}

function taskReaderProjection(): WorkspaceProjection {
  const lifecycle = (id: string, enclosure: string): LifecycleProjection => ({
    id,
    state: "running",
    phase: "build",
    fleeting: false,
    repoId: "repo-a",
    enclosure: `/contracts/${enclosure}`,
    tokens: 0,
    startedAt: "2026-07-12T10:00:00+00:00",
    lastEventTs: "2026-07-12T10:00:30+00:00",
    inferred: false,
    actions: [],
    tokenSeries: [],
  });
  const master = taskDoc({
    id: "ops-master",
    kind: "master",
    title: "Body Priority Master",
    docPath: "/tasks/repo-a/ops/task.json",
    bodyRevision: "master-r1",
    objective: "Master summary.",
    subTasks: [
      {
        number: "2",
        name: "Drilled Reader",
        file: "02_drilled.json",
        status: "inProgress",
        scope: "",
      },
    ],
  });
  const directLeaf = taskDoc({
    id: "direct-leaf",
    kind: "subTask",
    lifecycleId: "LC-DIRECT",
    title: "Direct Leaf Reader",
    docPath: "/tasks/repo-a/ops/01_direct-leaf.json",
    bodyRevision: "direct-r1",
    objective: "Direct leaf summary.",
  });
  const drilledLeaf = taskDoc({
    id: "drilled-leaf",
    kind: "subTask",
    title: "Drilled Reader",
    docPath: "/tasks/repo-a/ops/02_drilled.json",
    bodyRevision: "drilled-r1",
    objective: "Drilled summary.",
  });
  const lifecycleBound = taskDoc({
    id: "bound-doc",
    kind: "subTask",
    lifecycleId: "LC-BOUND",
    title: "Lifecycle Bound Reader",
    docPath: "/tasks/repo-a/ops/03_lifecycle-bound.json",
    bodyRevision: "bound-r1",
    objective: "Lifecycle-bound summary.",
  });
  return {
    version: 2,
    generatedAt: "2026-07-12T10:01:00+00:00",
    lifecycles: [lifecycle("LC-DIRECT", "direct-leaf"), lifecycle("LC-BOUND", "runtime-only")],
    enclosures: [
      liveEnclosure("direct-leaf", "LC-DIRECT"),
      liveEnclosure("runtime-only", "LC-BOUND"),
    ],
    providers: [],
    activeWorktreeGroups: ["direct-leaf", "runtime-only"],
    metrics: {
      lifecycleCount: 2,
      runningCount: 2,
      blockedCount: 0,
      pausedCount: 0,
      totalTokens: 0,
      stalenessHistogram: {},
    },
    analytics: {
      driftSnapshots: [],
      stalestSidecars: [],
      setupSummaries: [],
      setupProgress: [],
      routeCoverage: [],
      toolReports: [],
      ledgers: [],
      taskDocuments: [master, directLeaf, drilledLeaf, lifecycleBound],
      series: [
        {
          seriesId: "ops",
          repository: "repo-a",
          title: "Body Priority Master",
          status: "inProgress",
          objective: "Master summary.",
          subTasks: master.subTasks,
          doneCount: 0,
          totalCount: 1,
          seriesTokenTotal: 0,
          sections: [],
          decisions: [],
          docPath: master.docPath,
        },
      ],
      attentionQueue: [],
      engineProcesses: [],
    },
  };
}

function refreshTaskSummaries(): void {
  const analytics = dashboardStore.getState().analytics;
  if (!analytics) throw new Error("analytics missing");
  dashboardStore.getState().applyDelta("analytics", {
    ...analytics,
    taskDocuments: analytics.taskDocuments.map((doc) => ({
      ...doc,
      stepsDone: doc.stepsDone + 1,
    })),
  });
}

function stubTaskReaderFetch(
  projection: WorkspaceProjection,
  gates: Map<string, ReturnType<typeof deferred>>,
  completeObjective: Map<string, string>,
) {
  const fetchMock = vi.fn(async (url: string) => {
    if (url.startsWith("/api/task-document")) {
      const path = new URL(url, "http://dashboard.test").searchParams.get("path") ?? "";
      await gates.get(path)?.promise;
      const summary = projection.analytics.taskDocuments.find((doc) => doc.docPath === path);
      return {
        ok: true,
        status: 200,
        json: async () => ({ ...summary, objective: completeObjective.get(path) }),
      } as Response;
    }
    if (url === "/api/files/repos") {
      return { ok: true, status: 200, json: async () => ({ repos: [] }) } as Response;
    }
    if (url === "/api/harnesses") {
      return { ok: true, status: 200, json: async () => ({ harnesses: [] }) } as Response;
    }
    if (url === "/api/terminal/sessions") {
      return { ok: true, status: 200, json: async () => ({ sessions: [] }) } as Response;
    }
    if (url.startsWith("/api/changeset/")) {
      return {
        ok: true,
        status: 200,
        json: async () => ({
          counters: {
            code: { files: 0, insertions: 0, deletions: 0 },
            memory: { files: 0, insertions: 0, deletions: 0 },
          },
        }),
      } as Response;
    }
    if (url.startsWith("/api/notes/list")) {
      return {
        ok: true,
        status: 200,
        json: async () => ({ repo: "repo-a", master: "ops", notes: [], truncated: false }),
      } as Response;
    }
    return { ok: true, status: 200, json: async () => ({}) } as Response;
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

describe("Operations click-to-detail body hydration", () => {
  it("renders complete bodies for direct, master, drilled, and lifecycle-bound paths during analytics refreshes", async () => {
    const projection = taskReaderProjection();
    dashboardStore.getState().applySnapshot(projection);
    const gates = new Map(
      projection.analytics.taskDocuments.map((doc) => [doc.docPath, deferred()]),
    );
    const completeObjective = new Map(
      projection.analytics.taskDocuments.map((doc) => [
        doc.docPath,
        `Complete ${doc.title} objective.`,
      ]),
    );
    const fetchMock = stubTaskReaderFetch(projection, gates, completeObjective);

    const view = render(<CockpitShell />);
    const assertClickHydratesOnce = async (label: string, path: string) => {
      fireEvent.click(view.getByText(label));
      await waitFor(() =>
        expect(
          fetchMock.mock.calls.filter(([url]) =>
            String(url).includes(encodeURIComponent(path)),
          ),
        ).toHaveLength(1),
      );
      act(refreshTaskSummaries);
      await Promise.resolve();
      expect(
        fetchMock.mock.calls.filter(([url]) => String(url).includes(encodeURIComponent(path))),
      ).toHaveLength(1);
      gates.get(path)?.resolve();
      await waitFor(() => expect(view.getByText(completeObjective.get(path) ?? "")).toBeTruthy());
    };

    await assertClickHydratesOnce(
      "Direct Leaf Reader",
      "/tasks/repo-a/ops/01_direct-leaf.json",
    );
    await assertClickHydratesOnce("Body Priority Master", "/tasks/repo-a/ops/task.json");

    fireEvent.click(view.getByTestId("subtask-open-1"));
    const drilledPath = "/tasks/repo-a/ops/02_drilled.json";
    await waitFor(() =>
      expect(
        fetchMock.mock.calls.filter(([url]) => String(url).includes(encodeURIComponent(drilledPath))),
      ).toHaveLength(1),
    );
    act(refreshTaskSummaries);
    await Promise.resolve();
    expect(
      fetchMock.mock.calls.filter(([url]) => String(url).includes(encodeURIComponent(drilledPath))),
    ).toHaveLength(1);
    gates.get(drilledPath)?.resolve();
    await waitFor(() =>
      expect(view.getByText(completeObjective.get(drilledPath) ?? "")).toBeTruthy(),
    );

    await assertClickHydratesOnce(
      "runtime-only",
      "/tasks/repo-a/ops/03_lifecycle-bound.json",
    );
  });

  it("discards task A's late body after selecting task B and hydrates B exactly once", async () => {
    const projection = taskReaderProjection();
    dashboardStore.getState().applySnapshot(projection);
    const gates = new Map(
      projection.analytics.taskDocuments.map((doc) => [doc.docPath, deferred()]),
    );
    const completeObjective = new Map(
      projection.analytics.taskDocuments.map((doc) => [
        doc.docPath,
        `Complete ${doc.title} objective.`,
      ]),
    );
    const fetchMock = stubTaskReaderFetch(projection, gates, completeObjective);
    const view = render(<CockpitShell />);
    const taskAPath = "/tasks/repo-a/ops/01_direct-leaf.json";
    const taskBPath = "/tasks/repo-a/ops/03_lifecycle-bound.json";
    const requestsFor = (path: string) =>
      fetchMock.mock.calls.filter(([url]) => String(url).includes(encodeURIComponent(path)));

    fireEvent.click(view.getByText("Direct Leaf Reader"));
    await waitFor(() => expect(requestsFor(taskAPath)).toHaveLength(1));

    fireEvent.click(view.getByText("runtime-only"));
    await waitFor(() => expect(requestsFor(taskBPath)).toHaveLength(1));
    expect(view.getByText("Lifecycle-bound summary.")).toBeTruthy();

    await act(async () => {
      gates.get(taskAPath)?.resolve();
      await Promise.resolve();
    });

    expect(view.queryByText(completeObjective.get(taskAPath) ?? "")).toBeNull();
    expect(view.queryByText(completeObjective.get(taskBPath) ?? "")).toBeNull();
    expect(view.getByText("Lifecycle-bound summary.")).toBeTruthy();
    expect(requestsFor(taskBPath)).toHaveLength(1);

    gates.get(taskBPath)?.resolve();
    await waitFor(() =>
      expect(view.getByText(completeObjective.get(taskBPath) ?? "")).toBeTruthy(),
    );
    expect(view.queryByText(completeObjective.get(taskAPath) ?? "")).toBeNull();
    expect(requestsFor(taskBPath)).toHaveLength(1);
  });
});

describe("serving-build stamp (260703-L15 — the July-4 ghost-process lesson)", () => {
  it("renders the muted stamp from the snapshot's servingBuild (commit-first)", () => {
    const fixture = GALLERY.find((entry) => entry.name === "engine-fleet");
    if (!fixture) throw new Error("fixture not found: engine-fleet");
    dashboardStore.getState().applySnapshot({
      ...fixture.projection,
      servingBuild: { version: "9.9.9", commit: "abc1234", bootedAt: "2026-07-07T05:00:00Z" },
    });
    const { getByTestId } = render(<CockpitShell />);
    expect(getByTestId("serving-build").textContent).toContain("abc1234");
    expect(getByTestId("serving-build").textContent).toContain("up ");
  });

  it("falls back to the package version off-checkout; renders nothing without a stamp", () => {
    const fixture = GALLERY.find((entry) => entry.name === "engine-fleet");
    if (!fixture) throw new Error("fixture not found: engine-fleet");
    dashboardStore.getState().applySnapshot({
      ...fixture.projection,
      servingBuild: { version: "9.9.9", bootedAt: "2026-07-07T05:00:00Z" },
    });
    const first = render(<CockpitShell />);
    expect(first.getByTestId("serving-build").textContent).toContain("v9.9.9");
    first.unmount();

    dashboardStore.getState().reset();
    seed("engine-fleet"); // no servingBuild on the wire (a pre-L15 server)
    const second = render(<CockpitShell />);
    expect(second.queryByTestId("serving-build")).toBeNull(); // absent stamp: nothing, never faked
  });
});

describe("CockpitShell full-bleed machine-map views (5f S1)", () => {
  it("rails the Operations view but goes full-bleed (no rails) for the Engine Room", () => {
    seed("engine-fleet");
    const { container, getByRole } = render(<CockpitShell />);

    // Operations (default): the railed 3-column shell.
    expect(container.querySelector(".shell__body")?.getAttribute("data-fullbleed")).toBe("false");
    expect(container.querySelector(".rail--left")).not.toBeNull();
    expect(container.querySelector(".rail--right")).not.toBeNull();

    // Switch to the Engine Room machine-map view via the mode bar.
    fireEvent.click(getByRole("radio", { name:"Engine Room" }));

    // Full-bleed: both rails gone, single full-width column, and the room's own 3-zone layout
    // (header + boot/diagnostics zone) is present.
    expect(container.querySelector(".shell__body")?.getAttribute("data-fullbleed")).toBe("true");
    expect(container.querySelector(".rail--left")).toBeNull();
    expect(container.querySelector(".rail--right")).toBeNull();
    expect(container.querySelector('[data-testid="engine-room-header"]')).not.toBeNull();
    expect(container.querySelector('[data-testid="engine-room-diagnostics"]')).not.toBeNull();
  });

  it("keeps the rails for the Operations and Memory views", () => {
    seed("engine-fleet");
    const { container, getByRole } = render(<CockpitShell />);

    fireEvent.click(getByRole("radio", { name:"Memory" }));
    expect(container.querySelector(".shell__body")?.getAttribute("data-fullbleed")).toBe("false");
    expect(container.querySelector(".rail--left")).not.toBeNull();
    expect(container.querySelector(".rail--right")).not.toBeNull();
  });
});

describe("right-rail River⇄Chat toggle (L5 S2)", () => {
  it("swaps the rail--right content between the Event River and the single-instance chat", () => {
    seed("engine-fleet");
    const { container, getByTestId } = render(<CockpitShell />);

    const railRight = container.querySelector(".rail--right");
    expect(railRight).not.toBeNull();
    // Default = the Event River; the chat surface is not mounted.
    expect(railRight?.querySelector('[data-testid="event-river"]')).not.toBeNull();
    expect(railRight?.querySelector('[data-testid="rail-chat"]')).toBeNull();

    // Toggle to Chat: the river is gone, the single-instance chat is mounted in its place.
    fireEvent.click(getByTestId("rail-toggle-chat"));
    expect(railRight?.querySelector('[data-testid="rail-chat"]')).not.toBeNull();
    expect(railRight?.querySelector('[data-testid="event-river"]')).toBeNull();

    // Toggle back to River restores it.
    fireEvent.click(getByTestId("rail-toggle-river"));
    expect(railRight?.querySelector('[data-testid="event-river"]')).not.toBeNull();
    expect(railRight?.querySelector('[data-testid="rail-chat"]')).toBeNull();
  });

  it("remembers the rail choice across a window refresh (localStorage)", () => {
    seed("engine-fleet");
    const first = render(<CockpitShell />);
    // Default = River, then switch to Chat.
    expect(first.container.querySelector('.rail--right [data-testid="event-river"]')).not.toBeNull();
    fireEvent.click(first.getByTestId("rail-toggle-chat"));
    expect(window.localStorage.getItem("cockpit.rail-chat")).toBe("1");
    first.unmount();

    // A fresh mount (the window refresh) restores Chat from localStorage — the river is not shown.
    const second = render(<CockpitShell />);
    const railRight = second.container.querySelector(".rail--right");
    expect(railRight?.querySelector('[data-testid="rail-chat"]')).not.toBeNull();
    expect(railRight?.querySelector('[data-testid="event-river"]')).toBeNull();
  });
});

describe("Operations rails are resizable + persisted", () => {
  it("renders a resize gutter on each rail and applies the persisted widths to the grid", () => {
    window.localStorage.setItem("cockpit.rail-left-w", "430");
    window.localStorage.setItem("cockpit.rail-right-w", "250");
    seed("engine-fleet");
    const { container, getByTestId } = render(<CockpitShell />);

    // Each rail owns a drag gutter, and the railed grid uses the stored widths (centre takes the rest).
    expect(getByTestId("rail-resize-left")).not.toBeNull();
    expect(getByTestId("rail-resize-right")).not.toBeNull();
    const body = container.querySelector(".shell__body") as HTMLElement;
    expect(body.style.gridTemplateColumns).toBe("430px minmax(380px, 1fr) 250px");
  });

  it("nudges a rail width with the keyboard and persists the new width", () => {
    seed("engine-fleet"); // no stored width -> default 340
    const { getByTestId } = render(<CockpitShell />);

    fireEvent.keyDown(getByTestId("rail-resize-left"), { key: "ArrowRight" });
    expect(window.localStorage.getItem("cockpit.rail-left-w")).toBe("364"); // 340 + 24

    // The right rail's gutter is mirror-imaged: ArrowLeft grows it.
    fireEvent.keyDown(getByTestId("rail-resize-right"), { key: "ArrowLeft" });
    expect(window.localStorage.getItem("cockpit.rail-right-w")).toBe("324"); // 300 + 24
  });
});

describe("rail chat keys by the drilled leaf, not the master (L5 fix 1)", () => {
  it("keys the rail chat by the leaf once a master's sub-task is drilled open", () => {
    seedDrillableMaster();
    const { getByText, getByTestId } = render(<CockpitShell />);

    // Select the master, then toggle the rail to the chat surface.
    fireEvent.click(getByText("Ops Master"));
    fireEvent.click(getByTestId("rail-toggle-chat"));

    // Master overview shown (no leaf drilled): the rail is NOT blocked — it offers the
    // create-from-anywhere empty state, and the heading carries no leaf id yet (not the master's).
    expect(getByTestId("rail-chat-empty")).not.toBeNull();
    expect(getByTestId("rail-chat-heading").textContent).not.toContain("master-x");

    // Drill into the master's sub-task → the rail keys by THAT leaf id, not the master.
    fireEvent.click(getByTestId("subtask-open-1"));
    const heading = getByTestId("rail-chat-heading");
    expect(heading.textContent).toContain("leaf-one");
    expect(heading.textContent).not.toContain("master-x");
  });
});

describe("Operations drill survives a view switch (DetailPanel mount preservation)", () => {
  it("keeps the drilled sub-task open after switching to another tab and back", () => {
    seedDrillableMaster();
    const { getByText, getByRole, getByTestId, queryByTestId } = render(<CockpitShell />);

    // Select the master, then drill into its sub-task → the leaf reader (a breadcrumb back to the
    // master) replaces the sub-task index.
    fireEvent.click(getByText("Ops Master"));
    fireEvent.click(getByTestId("subtask-open-1"));
    expect(getByTestId("series-breadcrumb")).not.toBeNull();
    expect(queryByTestId("subtask-open-1")).toBeNull(); // we're in the reader, not the index

    // Leave Operations for another tab, then come back: the drill is preserved (the panel was hidden,
    // not unmounted), so it does NOT reset to the master overview.
    fireEvent.click(getByRole("radio", { name: "Memory" }));
    fireEvent.click(getByRole("radio", { name: "Operations" }));
    expect(getByTestId("series-breadcrumb")).not.toBeNull();
    expect(queryByTestId("subtask-open-1")).toBeNull();
  });
});

describe("canonical Chats route: full-bleed keep-alive cockpit (S5)", () => {
  it("defaults to Operations, exposes no Sessions route, and keeps one Chats cockpit mounted", () => {
    seed("engine-fleet");
    const { container, getByRole, queryByRole } = render(<CockpitShell />);

    expect(getByRole("radio", { name: "Operations" }).getAttribute("aria-checked")).toBe("true");
    expect(queryByRole("radio", { name: "Sessions" })).toBeNull();

    // The internal sessions-* markers remain the WebTUI/keyboard implementation scope, but there is
    // only one product route and one mounted PTY owner.
    const chats = container.querySelector('[data-testid="sessions-view"]');
    expect(chats).not.toBeNull();
    const layer = chats?.parentElement as HTMLElement;
    expect(layer.style.display).toBe("none");
    expect(layer.getAttribute("aria-hidden")).toBe("true");

    fireEvent.click(getByRole("radio", { name: "Chats" }));
    expect(container.querySelector(".shell__body")?.getAttribute("data-fullbleed")).toBe("true");
    expect(container.querySelector(".rail--left")).toBeNull();
    expect(container.querySelector('[data-testid="sessions-view"]')).toBe(chats);
    expect(layer.style.display).toBe("flex");
    expect(layer.getAttribute("aria-hidden")).toBe("false");

    fireEvent.click(getByRole("radio", { name: "Operations" }));
    expect(container.querySelector('[data-testid="sessions-view"]')).toBe(chats);
    expect(layer.style.display).toBe("none");
    expect(layer.getAttribute("aria-hidden")).toBe("true");
  });
});

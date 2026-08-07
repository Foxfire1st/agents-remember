import { act, cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import axe from "axe-core";
import { afterEach, describe, expect, it, vi } from "vitest";

import { sessionStore } from "../data/sessions";
import { dashboardStore } from "../data/store";
import { GALLERY } from "../dev/fixtures";
import { AttentionQueue } from "../panels/AttentionQueue";
import { DetailPanel } from "../panels/detail-panel/DetailPanel";
import { EngineRoom } from "../panels/EngineRoom";
import { EventRiver } from "../panels/EventRiver";
import { FileViewer } from "../panels/file-viewer/FileViewer";
import { HighlightComposer } from "../panels/HighlightComposer";
import { LifecycleList } from "../panels/lifecycle-list/LifecycleList";
import { NotesReaderViewer } from "../panels/notes-reader/NotesReaderViewer";
import { RailChat } from "../panels/RailChat";
import { SessionsView } from "../panels/session-cockpit/sessions-view/SessionsView";
import { taskDoc as wireTaskDoc } from "../test/fixtures/wire";
import { metricsFor } from "../types/projection";
import type {
  Analytics,
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
  return wireTaskDoc({
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
  });
}

// A lifecycle-bound master with one authored, drillable leaf — the drilled-leaf fixture.
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
    stateEnteredAt: "2026-06-20T09:00:00+00:00",
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
    metrics: metricsFor([lc]),
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
      agentPickups: [],
      expectationRows: [],
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
    stateEnteredAt: "2026-07-12T10:00:00+00:00",
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
    metrics: metricsFor([lifecycle("LC-DIRECT", "direct-leaf"), lifecycle("LC-BOUND", "runtime-only")]),
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
          createdAt: master.createdAt,
        },
      ],
      attentionQueue: [],
      engineProcesses: [],
      agentPickups: [],
      expectationRows: [],
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

describe("workspace rollup — the handoff reaches the header", () => {
  // The served rollup grew an `awaitingDeveloperCount` bucket, but the header read only
  // running/blocked/tokens. A lifecycle that had stopped and handed the turn back was inside
  // `lifecycleCount` and `totalTokens` and inside none of the numbers on the bar — the workflow's
  // own handoff surface was countable on the wire and invisible in the UI.
  const withStates = (...states: LifecycleProjection["state"][]): WorkspaceProjection => {
    const fixture = GALLERY.find((entry) => entry.name === "calm");
    if (!fixture) throw new Error("fixture not found: calm");
    const lifecycles = states.map((state, index) => ({
      ...fixture.projection.lifecycles[0],
      id: `LC-${index}`,
      state,
    }));
    return { ...fixture.projection, lifecycles, metrics: metricsFor(lifecycles) };
  };

  it("counts handed-back lifecycles on the bar", () => {
    dashboardStore
      .getState()
      .applySnapshot(withStates("awaiting-developer", "awaiting-developer", "running"));
    const { getByTestId } = render(<CockpitShell />);
    expect(getByTestId("task-metrics").textContent).toContain("2 awaiting you");
  });

  it("says nothing when nothing is handed back (no reassurance zero)", () => {
    dashboardStore.getState().applySnapshot(withStates("running", "blocked"));
    const { getByTestId } = render(<CockpitShell />);
    const text = getByTestId("task-metrics").textContent ?? "";
    expect(text).not.toContain("awaiting");
    // the standing rhythm is unchanged — the segment is appended, it does not displace anything
    expect(text).toContain("1 running");
    expect(text).toContain("1 blocked");
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

  it("marks a serving checkout with uncommitted code as hash* (additive wire flag)", () => {
    const fixture = GALLERY.find((entry) => entry.name === "engine-fleet");
    if (!fixture) throw new Error("fixture not found: engine-fleet");
    dashboardStore.getState().applySnapshot({
      ...fixture.projection,
      servingBuild: {
        version: "9.9.9",
        commit: "38c3fd8",
        bootedAt: "2026-07-07T05:00:00Z",
        dirty: true,
      },
    });
    const { getByTestId } = render(<CockpitShell />);
    expect(getByTestId("serving-build").textContent).toContain("38c3fd8*");
    expect(getByTestId("serving-build").textContent).not.toContain("dirty");
    expect(getByTestId("serving-build").title).toContain("@ 38c3fd8*");
    expect(getByTestId("serving-build").title).toContain("dirty — serving uncommitted code");
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
    seed("engine-fleet"); // no servingBuild on the wire (a legacy server)
    const second = render(<CockpitShell />);
    expect(second.queryByTestId("serving-build")).toBeNull(); // absent stamp: nothing, never faked
  });

  it("surfaces a stale client bundle in the tooltip (no redundant reload action)", () => {
    const fixture = GALLERY.find((entry) => entry.name === "engine-fleet");
    if (!fixture) throw new Error("fixture not found: engine-fleet");
    dashboardStore.getState().applySnapshot({
      ...fixture.projection,
      servingBuild: {
        version: "9.9.9",
        bootedAt: "2026-07-07T05:00:00Z",
        dashboardBuild: "different-dashboard-build", // ≠ test CLIENT_DASHBOARD_BUILD ("test-dashboard-build")
      },
    });
    const stale = render(<CockpitShell />);
    expect(stale.getByTestId("serving-build").dataset.clientBuildCurrent).toBe("false");
    // A proven client/serving mismatch must be visible to the operator, not silently swallowed
    // into the invisible data attribute — the hoverable stamp tooltip carries the reload cue.
    expect(stale.getByTestId("serving-build").title).toContain(
      "client bundle differs from the serving build — reload",
    );
    expect(stale.queryByTestId("reload-dashboard-client")).toBeNull();
    stale.unmount();

    dashboardStore.getState().reset();
    dashboardStore.getState().applySnapshot({
      ...fixture.projection,
      servingBuild: {
        version: "9.9.9",
        bootedAt: "2026-07-07T05:01:00Z",
        dashboardBuild: "test-dashboard-build",
      },
    });
    const current = render(<CockpitShell />);
    expect(current.getByTestId("serving-build").dataset.clientBuildCurrent).toBe("true");
    // A real match renders no mismatch cue — never fabricate a discrepancy that is not there.
    expect(current.getByTestId("serving-build").title).not.toContain("client bundle differs");
    expect(current.queryByTestId("reload-dashboard-client")).toBeNull();
  });

  it("adds no client-mismatch cue when a legacy server advertises no comparable bundle identity", () => {
    // `dashboardBuild` absent → clientMatchesServingBuild returns null (unknown). Honesty doctrine:
    // an unknown state must not assert a mismatch any more than it may fabricate a match.
    const fixture = GALLERY.find((entry) => entry.name === "engine-fleet");
    if (!fixture) throw new Error("fixture not found: engine-fleet");
    dashboardStore.getState().applySnapshot({
      ...fixture.projection,
      servingBuild: { version: "9.9.9", bootedAt: "2026-07-07T05:00:00Z" },
    });
    const { getByTestId } = render(<CockpitShell />);
    expect(getByTestId("serving-build").dataset.clientBuildCurrent).toBe("unknown");
    expect(getByTestId("serving-build").title).not.toContain("client bundle differs");
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

    // Full-bleed: both rails stay mounted but hidden (display:none + aria-hidden), the grid drops
    // to a single full-width column, and the room's own 3-zone layout (header + boot/diagnostics
    // zone) is present. Keep-alive: re-entry no longer remounts the rails.
    expect(container.querySelector(".shell__body")?.getAttribute("data-fullbleed")).toBe("true");
    const railLeft = container.querySelector(".rail--left") as HTMLElement;
    const railRight = container.querySelector(".rail--right") as HTMLElement;
    expect(railLeft).not.toBeNull();
    expect(railRight).not.toBeNull();
    expect(railLeft.style.display).toBe("none");
    expect(railRight.style.display).toBe("none");
    expect(railLeft.getAttribute("aria-hidden")).toBe("true");
    expect(railRight.getAttribute("aria-hidden")).toBe("true");
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

describe("rail keep-alive across view switches (260721 F1)", () => {
  it("keeps both rail asides mounted (hidden, never unmounted) on a full-bleed view", () => {
    seed("engine-fleet");
    const { container, getByRole } = render(<CockpitShell />);

    // Railed Operations view: the two aside nodes (and the river inside the right one) that we
    // watch across the switch.
    const railLeft = container.querySelector(".rail--left") as HTMLElement;
    const railRight = container.querySelector(".rail--right") as HTMLElement;
    const river = railRight.querySelector('[data-testid="event-river"]');
    expect(railLeft.style.display).toBe("flex");
    expect(railLeft.getAttribute("aria-hidden")).toBe("false");
    expect(river).not.toBeNull();

    // Switch to the full-bleed Engine Room: the asides are hidden, NOT unmounted (same nodes, so
    // rail scroll/collapsed state survives); the grid still drops to the single full-width column.
    fireEvent.click(getByRole("radio", { name: "Engine Room" }));
    expect(container.querySelector(".shell__body")?.getAttribute("data-fullbleed")).toBe("true");
    expect(container.querySelector(".rail--left")).toBe(railLeft);
    expect(container.querySelector(".rail--right")).toBe(railRight);
    expect(railLeft.style.display).toBe("none");
    expect(railRight.style.display).toBe("none");
    expect(railLeft.getAttribute("aria-hidden")).toBe("true");
    expect(railRight.getAttribute("aria-hidden")).toBe("true");
    expect(railRight.querySelector('[data-testid="event-river"]')).toBe(river);

    // Back to Operations: the exact same asides re-show — no remount, so no fresh
    // AttentionQueue/LifecycleList/EventRiver mount cost on entry.
    fireEvent.click(getByRole("radio", { name: "Operations" }));
    expect(container.querySelector(".shell__body")?.getAttribute("data-fullbleed")).toBe("false");
    expect(container.querySelector(".rail--left")).toBe(railLeft);
    expect(container.querySelector(".rail--right")).toBe(railRight);
    expect(railLeft.style.display).toBe("flex");
    expect(railLeft.getAttribute("aria-hidden")).toBe("false");
    expect(railRight.querySelector('[data-testid="event-river"]')).toBe(river);
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
    // The left rail stays mounted, hidden (keep-alive), while Chats goes full-bleed.
    expect((container.querySelector(".rail--left") as HTMLElement).style.display).toBe("none");
    expect(container.querySelector('[data-testid="sessions-view"]')).toBe(chats);
    expect(layer.style.display).toBe("flex");
    expect(layer.getAttribute("aria-hidden")).toBe("false");

    fireEvent.click(getByRole("radio", { name: "Operations" }));
    expect(container.querySelector('[data-testid="sessions-view"]')).toBe(chats);
    expect(layer.style.display).toBe("none");
    expect(layer.getAttribute("aria-hidden")).toBe("true");
  });
});

describe("Engine Room keep-alive cockpit layer (260721 C2)", () => {
  it("keeps the Engine Room mounted (hidden, never unmounted) across a tab switch", () => {
    seed("engine-fleet");
    const { container, getByRole } = render(<CockpitShell />);

    // Mounted at boot like the other persistent layers, but hidden while Operations is up.
    const room = container.querySelector('[data-testid="engine-room"]') as HTMLElement;
    expect(room).not.toBeNull();
    const layer = room.parentElement as HTMLElement;
    expect(layer.style.display).toBe("none");
    expect(layer.getAttribute("aria-hidden")).toBe("true");

    // Switch to the Engine Room: the same DOM node shows — no remount, exactly one room in the tree.
    fireEvent.click(getByRole("radio", { name: "Engine Room" }));
    expect(container.querySelector('[data-testid="engine-room"]')).toBe(room);
    expect(container.querySelectorAll('[data-testid="engine-room"]').length).toBe(1);
    expect(layer.style.display).toBe("flex");
    expect(layer.getAttribute("aria-hidden")).toBe("false");

    // Away to Chats and back: still the same node — the room's SVG + GSAP substrate was never rebuilt.
    fireEvent.click(getByRole("radio", { name: "Chats" }));
    expect(container.querySelector('[data-testid="engine-room"]')).toBe(room);
    expect(layer.style.display).toBe("none");
    fireEvent.click(getByRole("radio", { name: "Engine Room" }));
    expect(container.querySelector('[data-testid="engine-room"]')).toBe(room);
    expect(layer.style.display).toBe("flex");
  });
});

describe("persistent layers are exported memoized (260721 tab-switch CPU)", () => {
  // The memo gate is the fix's contract: a shell re-render (every setView) must not reconcile a
  // layer whose props are unchanged. Guard the export SHAPE here so a future refactor can't
  // silently drop the memo; the render-count behavior lives in Cockpit.memo.test.tsx.
  it("exports every persistent layer as a React.memo component", () => {
    const layers: Array<[string, unknown]> = [
      ["DetailPanel", DetailPanel],
      ["SessionsView", SessionsView],
      ["EngineRoom", EngineRoom],
      ["FileViewer", FileViewer],
      ["AttentionQueue", AttentionQueue],
      ["LifecycleList", LifecycleList],
      ["EventRiver", EventRiver],
      ["RailChat", RailChat],
      ["HighlightComposer", HighlightComposer],
      ["NotesReaderViewer", NotesReaderViewer],
    ];
    for (const [name, component] of layers) {
      expect((component as { $$typeof: symbol }).$$typeof, name).toBe(Symbol.for("react.memo"));
    }
  });
});

describe("the left rail shows lifecycle states and attention severities at the same time", () => {
  // Dot.tsx used to justify `warn` and `awaiting-developer` sharing amber on the grounds that
  // "AttentionQueue renders only severities and LifecycleList renders only states, so no list can
  // show both". True per LIST and false per VIEW: the two panels are siblings inside the same
  // always-visible left rail, so a developer reads both grammars in one glance — and they are
  // different facts about different objects (the reducer builds no attention row for an
  // `awaiting-developer` lifecycle, so an amber dot in one panel says nothing about the other).
  //
  // This renders the whole shell rather than the two panels, because the panels being siblings in
  // one view is exactly the claim under test.
  function railProjection(attentionQueue: Analytics["attentionQueue"]): WorkspaceProjection {
    const handoff: LifecycleProjection = {
      id: "LC-HANDOFF",
      state: "awaiting-developer",
      phase: "build",
      fleeting: false,
      repoId: "repo-a",
      tokens: 0,
      startedAt: "2026-06-20T09:00:00+00:00",
      lastEventTs: "2026-06-20T09:00:30+00:00",
      stateEnteredAt: "2026-06-20T09:00:00+00:00",
      inferred: false,
      actions: [],
      tokenSeries: [],
    };
    return {
      version: 2,
      generatedAt: "2026-06-20T09:01:00+00:00",
      lifecycles: [handoff],
      // The rail renders a leaf only while a worktree exists, so the row needs its enclosure.
      enclosures: [liveEnclosure("01", "LC-HANDOFF")],
      providers: [],
      activeWorktreeGroups: [],
      metrics: metricsFor([handoff]),
      analytics: {
        driftSnapshots: [],
        stalestSidecars: [],
        setupSummaries: [],
        setupProgress: [],
        routeCoverage: [],
        toolReports: [],
        ledgers: [],
        taskDocuments: [
          taskDoc({
            id: "01",
            kind: "subTask",
            lifecycleId: "LC-HANDOFF",
            title: "Handoff Leaf",
            docPath: "/tasks/repo-a/ops/01_handoff.json",
          }),
        ],
        series: [],
        attentionQueue,
        engineProcesses: [],
        agentPickups: [],
        expectationRows: [],
      },
    };
  }

  const WARN_ROW: Analytics["attentionQueue"] = [
    {
      id: "actionable-drift:repo-a",
      kind: "actionable-drift",
      severity: "warn",
      lane: "repo",
      title: "6 actionable drift",
      waitSeconds: 900,
      repoId: "repo-a",
    },
  ];

  it("keeps a handoff state and a queue warning apart in the one rail that shows both", () => {
    dashboardStore.getState().applySnapshot(railProjection(WARN_ROW));
    const { getByTestId } = render(<CockpitShell />);

    const stateDot = getByTestId("task-state").firstElementChild;
    const severityDot = getByTestId("attn-severity").firstElementChild;
    expect(stateDot, "no lifecycle state dot in the rail").toBeTruthy();
    expect(severityDot, "no attention severity dot in the rail").toBeTruthy();
    // Both are amber — the palette groups rather than identifies — so this is the glyph's job.
    expect(stateDot?.outerHTML).not.toBe(severityDot?.outerHTML);
  });

  it("speaks the severity of an attention row into the accessibility tree", () => {
    // The Dot is `aria-hidden`, so the severity lives entirely on its wrapper — and the wrapper
    // used to be a bare `<span aria-label>`. ARIA prohibits naming a `generic`, so that label was
    // in the DOM and in no accessibility tree: `getAttribute("aria-label")` passed while a screen
    // reader user got nothing. Queried BY ROLE AND NAME here, which is the computed tree, and
    // backed by axe, which fails the prohibited attribute outright.
    dashboardStore.getState().applySnapshot(railProjection(WARN_ROW));
    const { getByRole, container } = render(<CockpitShell />);
    expect(getByRole("img", { name: "Severity: warn" })).toBe(
      container.querySelector('[data-testid="attn-severity"]'),
    );
    // The state dot's label reaches the tree a different way: React Aria gives the row
    // `role="option"`, whose name comes from its content, so the span's label is absorbed into it.
    expect(
      getByRole("option", { name: /Task progress: awaiting-developer; phase: build/ }),
    ).toBeTruthy();
  });

  it("passes axe on the panel the severity label lives in", async () => {
    // The panel rather than the whole shell: axe walks every node it is given, and this is the
    // subtree the label is on. `aria-prohibited-attr` is `serious` and would fail here.
    dashboardStore.getState().applySnapshot(railProjection(WARN_ROW));
    const { container } = render(<AttentionQueue onSelect={vi.fn()} />);
    const results = await axe.run(container, {
      // jsdom has no layout engine, so skip the rules that need rendered geometry.
      rules: { "color-contrast": { enabled: false }, region: { enabled: false } },
    });
    expect(results.violations.map((violation) => violation.id)).toEqual([]);
  });
});

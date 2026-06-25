import { cleanup, fireEvent, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { dashboardStore } from "../data/store";
import { GALLERY } from "../dev/fixtures";
import type {
  EnclosureNode,
  LifecycleProjection,
  SeriesNode,
  TaskDocNode,
  WorkspaceProjection,
} from "../types/projection";
import { DetailPanel } from "./DetailPanel";

function seed(name: string) {
  const projection = GALLERY.find((entry) => entry.name === name)?.projection;
  if (!projection) throw new Error(`fixture not found: ${name}`);
  dashboardStore.getState().applySnapshot(projection);
}

function taskDoc(over: Partial<TaskDocNode> & Pick<TaskDocNode, "kind" | "docPath">): TaskDocNode {
  return {
    id: "1",
    lifecycleId: "LC-SER",
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
  };
}

function seriesNode(over: Partial<SeriesNode> & Pick<SeriesNode, "seriesId">): SeriesNode {
  return {
    repository: "repo-a",
    title: "series",
    status: "inProgress",
    objective: "",
    subTasks: [],
    doneCount: 0,
    totalCount: 0,
    sections: [],
    decisions: [],
    docPath: `/t/${over.seriesId}/task.json`,
    ...over,
  };
}

function enclosure(over: Partial<EnclosureNode> & Pick<EnclosureNode, "enclosure" | "lifecycleId">) {
  return {
    enclosureId: over.enclosure,
    leafId: over.enclosure,
    taskRoot: "/tasks/260610_browser-dashboard",
    taskId: "260610_BROWSER-DASHBOARD",
    taskName: "260610_browser-dashboard",
    repoName: "agents-remember",
    worktreeGroup: "/worktrees/260610-browser-dashboard-s16-ar",
    humanReviewStatus: "pending-review",
    closeoutStatus: "not-started",
    integrationStatus: "not-started",
    cleanup: "pending",
    actions: [],
    ...over,
  } satisfies EnclosureNode;
}

// A series projection: one lifecycle, a contract-paired master, and one authored slice doc.
function seedSeries(
  options: {
    lifecycleId?: string;
    enclosureTaskId?: string;
    enclosureTaskName?: string;
    sliceDoc?: Partial<TaskDocNode>;
  } = {},
) {
  const lc: LifecycleProjection = {
    id: options.lifecycleId ?? "LC-SER",
    state: "running",
    phase: "build",
    fleeting: false,
    tokens: 0,
    startedAt: "2026-06-20T09:00:00+00:00",
    lastEventTs: "2026-06-20T09:00:30+00:00",
    inferred: false,
    actions: [],
    tokenSeries: [],
  };
  const master = seriesNode({
    seriesId: "series",
    title: "My Series",
    objective: "Series objective text",
    docPath: "/t/series/task.json",
    subTasks: [
      {
        number: "1",
        name: "First slice",
        file: "01_first.md",
        status: "inProgress",
        scope: "",
        createdAt: "2026-06-20T09:00:00+00:00",
      },
      {
        number: "2",
        name: "Parallel series",
        file: "../other/task.md",
        status: "inProgress",
        scope: "",
        createdAt: "2026-06-21T09:00:00+00:00",
        linkedLifecycleId: "LC-OTHER",
      },
    ],
    sections: [
      {
        kind: "freeform",
        heading: "Current State",
        body: "Status is **strong**.\n\n| Slice | Status |\n| --- | --- |\n| 01 | done |",
      },
      { kind: "subTasks", heading: "Sub-tasks", body: "" },
    ],
  });
  const slice = taskDoc({
    lifecycleId: options.lifecycleId ?? "LC-SER",
    kind: "subTask",
    title: "First slice",
    objective: "Slice objective text",
    docPath: "/t/series/01_first.json",
    stepsTotal: 1,
    steps: [{ id: "S1", title: "do the thing", status: "pending", substeps: [] }],
    ...options.sliceDoc,
  });
  const projection: WorkspaceProjection = {
    version: 2,
    generatedAt: "2026-06-20T09:01:00+00:00",
    lifecycles: [lc],
    enclosures: options.enclosureTaskId
      ? [
          enclosure({
            enclosure: "/contracts/series",
            lifecycleId: options.lifecycleId ?? "LC-SER",
            leafId: options.enclosureTaskName ?? "series",
            taskId: options.enclosureTaskId,
            taskName: options.enclosureTaskName ?? "series",
          }),
        ]
      : [],
    providers: [],
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
      taskDocuments: [slice],
      series: [master],
      attentionQueue: [],
      engineProcesses: [],
    },
  };
  dashboardStore.getState().applySnapshot(projection);
}

function nestedProgressSteps(): TaskDocNode["steps"] {
  return Array.from({ length: 7 }, (_, stepIndex) => ({
    id: `S${stepIndex + 1}`,
    title: `Top level step ${stepIndex + 1}`,
    status: stepIndex < 6 ? "done" : "pending",
    substeps: Array.from({ length: 6 }, (_, substepIndex) => ({
      id: `S${stepIndex + 1}.${substepIndex + 1}`,
      title: `Nested step ${stepIndex + 1}.${substepIndex + 1}`,
      status: stepIndex < 6 || substepIndex < 4 ? "done" : "pending",
    })),
  }));
}

function seedSeriesOrdering() {
  const master = seriesNode({
    seriesId: "series",
    title: "Ordered Series",
    subTasks: [
      {
        number: "99",
        name: "Alpha later",
        file: "alpha_later.md",
        status: "inProgress",
        scope: "",
        createdAt: "2026-06-22T09:00:00+00:00",
      },
      {
        number: "01",
        name: "Zulu earlier",
        file: "zulu_earlier.md",
        status: "planning",
        scope: "",
        createdAt: "2026-06-20T09:00:00+00:00",
      },
    ],
  });
  const projection: WorkspaceProjection = {
    version: 2,
    generatedAt: "2026-06-20T09:01:00+00:00",
    lifecycles: [],
    enclosures: [],
    providers: [],
    metrics: {
      lifecycleCount: 0,
      runningCount: 0,
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
      taskDocuments: [],
      series: [master],
      attentionQueue: [],
      engineProcesses: [],
    },
  };
  dashboardStore.getState().applySnapshot(projection);
}

function seedTaskDocuments(docs: TaskDocNode[]) {
  seedProjection({
    analytics: {
      driftSnapshots: [],
      stalestSidecars: [],
      setupSummaries: [],
      setupProgress: [],
      routeCoverage: [],
      toolReports: [],
      ledgers: [],
      taskDocuments: docs,
      series: [],
      attentionQueue: [],
      engineProcesses: [],
    },
  });
}

function seedProjection(over: Partial<WorkspaceProjection>) {
  const lifecycles = over.lifecycles ?? [];
  const projection: WorkspaceProjection = {
    version: 2,
    generatedAt: "2026-06-20T09:01:00+00:00",
    lifecycles,
    enclosures: [],
    providers: [],
    metrics: {
      lifecycleCount: lifecycles.length,
      runningCount: lifecycles.filter((entry) => entry.state === "running").length,
      blockedCount: lifecycles.filter((entry) => entry.state === "blocked").length,
      pausedCount: lifecycles.filter((entry) => entry.state === "paused").length,
      totalTokens: lifecycles.reduce((sum, entry) => sum + entry.tokens, 0),
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
      taskDocuments: [],
      series: [],
      attentionQueue: [],
      engineProcesses: [],
    },
    ...over,
  };
  dashboardStore.getState().applySnapshot(projection);
}

function seedPromotedLeaf() {
  const lc: LifecycleProjection = {
    id: "01KVW2FE8MQK6QCQQP0J4SEK3C",
    state: "paused",
    phase: "build",
    fleeting: false,
    enclosure: "/contracts/16",
    repoId: "agents-remember",
    tokens: 0,
    startedAt: "2026-06-24T06:00:00+00:00",
    lastEventTs: "2026-06-24T06:00:30+00:00",
    inferred: false,
    actions: [],
    tokenSeries: [],
  };
  const doc = taskDoc({
    lifecycleId: "260610_BROWSER-DASHBOARD",
    kind: "subTask",
    title: "Lifecycle Finalize Task",
    docPath: "/tasks/260610_browser-dashboard/14_lifecycle-finalize-task.json",
    objective: "Close out the lifecycle finalizer.",
  });
  const master = taskDoc({
    lifecycleId: "260610_BROWSER-DASHBOARD",
    kind: "master",
    title: "Browser Dashboard Series",
    docPath: "/tasks/260610_browser-dashboard/task.json",
    objective: "Parent master content.",
  });
  const leaf = taskDoc({
    lifecycleId: "01KVW2FE8MQK6QCQQP0J4SEK3C",
    kind: "subTask",
    title: "Engine Room Stack Entry Height",
    status: "inProgress",
    docPath: "/tasks/260610_browser-dashboard/16_engine-room-stack-entry-height.json",
    objective: "Keep a single Engine Room enclosure entry visually bounded.",
    requirements: ["Render the selected leaf task document, not the parent task or enclosure contract."],
    stepsTotal: 1,
    steps: [{ id: "S1", title: "Fix the stack entry height", status: "inProgress", substeps: [] }],
    sections: [
      {
        kind: "freeform",
        heading: "Notes",
        body: "This is the authored leaf task document.",
      },
    ],
  });
  const projection: WorkspaceProjection = {
    version: 2,
    generatedAt: "2026-06-24T06:01:00+00:00",
    lifecycles: [lc],
    enclosures: [
      enclosure({
        enclosure: "/contracts/16",
        lifecycleId: "01KVW2FE8MQK6QCQQP0J4SEK3C",
        leafId: "16_engine-room-stack-entry-height",
      }),
    ],
    providers: [],
    metrics: {
      lifecycleCount: 1,
      runningCount: 0,
      blockedCount: 0,
      pausedCount: 1,
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
      taskDocuments: [master, doc, leaf],
      series: [
        seriesNode({
          seriesId: "260610_browser-dashboard",
          title: "Browser Dashboard Series",
          docPath: "/tasks/260610_browser-dashboard/task.json",
          subTasks: [
            {
              number: "16",
              name: "Engine Room Stack Entry Height",
              file: "16_engine-room-stack-entry-height.md",
              status: "inProgress",
              scope: "",
              createdAt: "2026-06-24T06:00:00+00:00",
            },
          ],
        }),
      ],
      attentionQueue: [],
      engineProcesses: [],
    },
  };
  dashboardStore.getState().applySnapshot(projection);
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  dashboardStore.getState().reset();
});

describe("DetailPanel gate respond (task 11)", () => {
  it("renders the gate respond drawer with the full request packet", () => {
    seed("gate-review");
    const { getByTestId, queryByTestId } = render(<DetailPanel selectedId="closeout-005" />);
    expect(getByTestId("gate-review").textContent).toContain("closeout-approval");
    expect(getByTestId("gate-respond-open")).toBeTruthy();
    expect(queryByTestId("gate-approve")).toBeNull();
    fireEvent.click(getByTestId("gate-respond-open"));
    expect(getByTestId("gate-request").textContent).toContain("Changed paths");
  });

  it("renders the proto-gate ask through the same respond surface", () => {
    seed("blocked");
    const { getByTestId, queryByTestId } = render(<DetailPanel selectedId="plan-002" />);
    expect(queryByTestId("gate-review")).toBeNull();
    expect(getByTestId("gate-banner").textContent).toContain("Approve the plan?");
    expect(getByTestId("gate-respond-open")).toBeTruthy();
  });
});

describe("DetailPanel master series navigation (6g)", () => {
  it("renders an unbound planning leaf task document by typed taskdoc selection", () => {
    const doc = taskDoc({
      lifecycleId: undefined,
      kind: "subTask",
      title: "Plan Before Worktree",
      docPath: "/tasks/repo-a/planning/01_plan.json",
      objective: "Plan the document before opening a worktree.",
    });
    seedTaskDocuments([doc]);

    const { getAllByText, getByText, queryByText } = render(
      <DetailPanel selectedId="taskdoc:/tasks/repo-a/planning/01_plan.json" />,
    );

    expect(getAllByText("Plan Before Worktree").length).toBeGreaterThan(0);
    expect(getByText("Plan the document before opening a worktree.")).toBeTruthy();
    expect(queryByText("No task document bound to this task.")).toBeNull();
  });

  it("renders an unbound master task document by kind", () => {
    const master = taskDoc({
      lifecycleId: undefined,
      kind: "master",
      title: "Planning Master",
      docPath: "/tasks/repo-a/planning/task.json",
      objective: "Master plan objective.",
      subTasks: [
        {
          number: "99",
          name: "Leaf created first",
          file: "01_leaf.md",
          status: "planning",
          scope: "",
          createdAt: "2026-06-20T09:00:00+00:00",
        },
      ],
    });
    const leaf = taskDoc({
      id: "99",
      lifecycleId: undefined,
      kind: "subTask",
      title: "Leaf created first",
      docPath: "/tasks/repo-a/planning/01_leaf.json",
      objective: "Leaf objective.",
    });
    seedTaskDocuments([master, leaf]);

    const { getByTestId, getByText, queryByText } = render(
      <DetailPanel selectedId="taskdoc:/tasks/repo-a/planning/task.json" />,
    );

    expect(getByText("Master plan objective.")).toBeTruthy();
    expect(getByTestId("subtask-open-1").textContent).toContain("99. Leaf created first");
    expect(queryByText("No task document bound to this task.")).toBeNull();
  });

  it("opens authored master leaves from the full projected pool when the master is lifecycle-bound", () => {
    const lc: LifecycleProjection = {
      id: "ROOT",
      state: "running",
      phase: "build",
      fleeting: false,
      tokens: 0,
      startedAt: "2026-06-20T09:00:00+00:00",
      lastEventTs: "2026-06-20T09:00:30+00:00",
      inferred: false,
      actions: [],
      tokenSeries: [],
    };
    const master = taskDoc({
      lifecycleId: "ROOT",
      kind: "master",
      title: "Lifecycle Master",
      docPath: "/tasks/repo-a/planning/task.json",
      objective: "Master plan objective.",
      subTasks: [
        {
          number: "1",
          name: "Leaf from projected pool",
          file: "01_leaf.md",
          status: "planning",
          scope: "",
          createdAt: "2026-06-20T09:00:00+00:00",
        },
      ],
    });
    const leaf = taskDoc({
      id: "1",
      lifecycleId: "ROOT",
      kind: "subTask",
      title: "Leaf from projected pool",
      docPath: "/tasks/repo-a/planning/01_leaf.json",
      objective: "Leaf objective from the projected pool.",
    });
    seedProjection({
      lifecycles: [lc],
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
    });

    const { getByTestId, getByText, queryByText } = render(
      <DetailPanel selectedId="taskdoc:/tasks/repo-a/planning/task.json" />,
    );

    expect(getByTestId("subtask-open-1").tagName.toLowerCase()).toBe("button");
    fireEvent.click(getByTestId("subtask-open-1"));
    expect(getByText("Leaf objective from the projected pool.")).toBeTruthy();
    expect(queryByText("No task document bound to this task.")).toBeNull();
  });

  it("keeps master rows static when the referenced leaf has no authored task document", () => {
    const master = taskDoc({
      lifecycleId: undefined,
      kind: "master",
      title: "Planning Master",
      docPath: "/tasks/repo-a/planning/task.json",
      objective: "Master plan objective.",
      subTasks: [
        {
          number: "1",
          name: "Missing leaf",
          file: "01_missing.md",
          status: "planning",
          scope: "",
          createdAt: "2026-06-20T09:00:00+00:00",
        },
      ],
    });
    seedTaskDocuments([master]);

    const { getByTestId } = render(
      <DetailPanel selectedId="taskdoc:/tasks/repo-a/planning/task.json" />,
    );

    expect(getByTestId("subtask-open-1").tagName.toLowerCase()).toBe("div");
    expect(getByTestId("subtask-open-1").textContent).toContain("1. Missing leaf");
  });

  it("renders structured ids with step and code example titles", () => {
    const doc = taskDoc({
      lifecycleId: undefined,
      kind: "subTask",
      title: "Id Display",
      docPath: "/tasks/repo-a/planning/ids.json",
      steps: [
        {
          id: "S11",
          title: "Make Operations disappearance archive/delete-based",
          status: "pending",
          substeps: [
            {
              id: "S11.1",
              title: "Exclude archived task documents",
              status: "pending",
            },
          ],
        },
      ],
      codeExamples: [
        {
          id: "E4",
          title: "Master leaf list uses task-specific numbers",
          distinctChange: "Series/master reader ordering and enumeration.",
          why: "The display number is structured presentation.",
          language: "tsx",
          snippet: "<LeafTaskRow />",
        },
      ],
    });
    seedTaskDocuments([doc]);

    const { getAllByText, getByText, queryByText } = render(
      <DetailPanel selectedId="taskdoc:/tasks/repo-a/planning/ids.json" />,
    );

    expect(getAllByText("S11 — Make Operations disappearance archive/delete-based")).toHaveLength(2);
    expect(getAllByText("S11.1 — Exclude archived task documents")).toHaveLength(2);
    expect(getByText("E4 — Master leaf list uses task-specific numbers")).toBeTruthy();
    expect(queryByText("Make Operations disappearance archive/delete-based")).toBeNull();
    expect(queryByText("Master leaf list uses task-specific numbers")).toBeNull();
  });

  it("pins the sub-task index above the description and keeps the in-section copy", () => {
    seedSeries();
    const { getAllByText, getByTestId, getByText } = render(
      <DetailPanel selectedId="series" />,
    );
    expect(getAllByText("My Series").length).toBeGreaterThan(0);
    const objective = getByText("Series objective text");
    const topIndex = getByTestId("subtask-open-1"); // pinned navigation copy
    expect(getByTestId("subtask-mid-1")).toBeTruthy(); // authored in-section copy stays
    // the pinned index precedes the description in the DOM (FOLLOWING set => objective is after it)
    expect(
      topIndex.compareDocumentPosition(objective) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
  });

  it("drills into a slice's reader and returns via the breadcrumb", () => {
    seedSeries();
    const { getAllByText, getByTestId, getByText, queryByText } = render(
      <DetailPanel selectedId="series" />,
    );
    // The master overview is shown, the slice body is not.
    expect(queryByText("Slice objective text")).toBeNull();

    fireEvent.click(getByTestId("subtask-open-1"));
    expect(getByText("Slice objective text")).toBeTruthy(); // the slice's full reader
    expect(getAllByText("S1 — do the thing")).toHaveLength(2); // top + implementation steps

    fireEvent.click(getByTestId("series-breadcrumb"));
    expect(getByTestId("subtask-open-1")).toBeTruthy(); // back to the master index
    expect(queryByText("Slice objective text")).toBeNull();
  });

  it("jumps lifecycles from a cross-master row", () => {
    seedSeries();
    const onOpenLifecycle = vi.fn();
    const { getByTestId } = render(
      <DetailPanel selectedId="series" onOpenLifecycle={onOpenLifecycle} />,
    );
    fireEvent.click(getByTestId("subtask-open-link-2")); // the "→" cross-series row
    expect(onOpenLifecycle).toHaveBeenCalledWith("LC-OTHER");
  });

  it("renders markdown in master sections (GFM table + bold), not raw", () => {
    seedSeries();
    const { container, queryByText } = render(<DetailPanel selectedId="series" />);
    const table = container.querySelector("table");
    expect(table).toBeTruthy(); // the GFM table is a real <table>, not raw pipes
    expect(container.querySelector("th")?.textContent).toBe("Slice");
    expect(container.querySelector("strong")?.textContent).toBe("strong");
    expect(queryByText(/\| Slice \| Status \|/)).toBeNull(); // raw markdown is gone
  });

  it("orders master leaves by creation time and displays task-specific numbers", () => {
    seedSeriesOrdering();
    const { getByTestId, queryByText } = render(<DetailPanel selectedId="series" />);

    expect(getByTestId("subtask-open-1").textContent).toContain("01. Zulu earlier");
    expect(getByTestId("subtask-open-2").textContent).toContain("99. Alpha later");
    expect(queryByText("1. Zulu earlier")).toBeNull();
    expect(queryByText("2. Alpha later")).toBeNull();
  });

  it("summarizes task progress with top-level implementation steps", () => {
    seedSeries({
      sliceDoc: {
        title: "Parallel Leaf Enclosure Workflow",
        stepsDone: 40,
        stepsTotal: 42,
        steps: nestedProgressSteps(),
      },
    });
    const { getByRole, getByTestId, queryByText } = render(<DetailPanel selectedId="series" />);

    const row = getByTestId("subtask-open-1");
    expect(row.textContent).toContain("6/7 · inProgress");
    expect(row.textContent).not.toContain("40/42");

    fireEvent.click(row);
    expect(getByRole("img", { name: "steps done" }).textContent).toBe("6/7");
    expect(queryByText("40/42")).toBeNull();
  });

  it("renders master content when a selected task-id lifecycle maps to the series task name", () => {
    seedSeries({
      lifecycleId: "SERIES_TASK",
      enclosureTaskId: "SERIES_TASK",
      enclosureTaskName: "series",
    });
    const { getByText, queryByText } = render(<DetailPanel selectedId="SERIES_TASK" />);

    expect(getByText("Series objective text")).toBeTruthy();
    expect(getByText("Current State")).toBeTruthy();
    expect(queryByText("series 1 task slices")).toBeNull();
    expect(queryByText("No task document bound to this task.")).toBeNull();
  });

  it("does not use parent taskName as content for a leaf lifecycle without a projected doc", () => {
    seedSeries({
      lifecycleId: "LEAF_TASK",
      enclosureTaskId: "SERIES_TASK",
      enclosureTaskName: "series",
      sliceDoc: { lifecycleId: "OTHER_TASK" },
    });
    const { getByText, queryByText } = render(<DetailPanel selectedId="LEAF_TASK" />);

    expect(getByText("No task document bound to this task.")).toBeTruthy();
    expect(queryByText("Series objective text")).toBeNull();
    expect(queryByText("Current State")).toBeNull();
  });

  it("keeps a direct leaf lifecycle document ahead of the parent series mapping", () => {
    seedSeries({
      lifecycleId: "LEAF_TASK",
      enclosureTaskId: "SERIES_TASK",
      enclosureTaskName: "series",
    });
    const { getAllByText, getByText, queryByText } = render(<DetailPanel selectedId="LEAF_TASK" />);

    expect(getByText("Slice objective text")).toBeTruthy();
    expect(getAllByText("S1 — do the thing")).toHaveLength(2);
    expect(queryByText("Series objective text")).toBeNull();
    expect(queryByText("Current State")).toBeNull();
  });
});

describe("DetailPanel promoted lifecycle identity", () => {
  it("renders the leaf task document without falling back to the master task documents", () => {
    seedPromotedLeaf();
    const { getAllByText, getByText, queryByText } = render(
      <DetailPanel selectedId="01KVW2FE8MQK6QCQQP0J4SEK3C" />,
    );

    expect(getAllByText("16_engine-room-stack-entry-height").length).toBeGreaterThan(0);
    expect(getByText("subTask")).toBeTruthy();
    expect(getByText("Engine Room Stack Entry Height")).toBeTruthy();
    expect(getByText("Keep a single Engine Room enclosure entry visually bounded.")).toBeTruthy();
    expect(getAllByText("S1 — Fix the stack entry height")).toHaveLength(2);
    expect(getByText("Notes")).toBeTruthy();
    expect(getByText("This is the authored leaf task document.")).toBeTruthy();
    expect(queryByText("Lifecycle Finalize Task")).toBeNull();
    expect(queryByText("Close out the lifecycle finalizer.")).toBeNull();
    expect(queryByText("Series Contract")).toBeNull();
    expect(queryByText("schema: ar-series-contract/v1")).toBeNull();
    expect(queryByText("01KVW2FE8MQK6QCQQP0J4SEK3C")).toBeNull();
    expect(queryByText("No task document bound to this task.")).toBeNull();
  });

  it("links an enclosure-opened leaf back to its parent task document", () => {
    seedPromotedLeaf();
    const onOpenLifecycle = vi.fn();
    const { getByTestId } = render(
      <DetailPanel selectedId="01KVW2FE8MQK6QCQQP0J4SEK3C" onOpenLifecycle={onOpenLifecycle} />,
    );

    expect(getByTestId("master-parent-link").textContent).toContain("Browser Dashboard Series");
    fireEvent.click(getByTestId("master-parent-link"));
    expect(onOpenLifecycle).toHaveBeenCalledWith(
      "taskdoc:/tasks/260610_browser-dashboard/task.json",
    );
  });
});

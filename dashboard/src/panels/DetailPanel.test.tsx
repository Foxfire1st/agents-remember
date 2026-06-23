import { cleanup, fireEvent, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { dashboardStore } from "../data/store";
import { GALLERY } from "../dev/fixtures";
import type {
  LifecycleProjection,
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
    lifecycleId: "LC-SER",
    repository: "repo-a",
    title: "doc",
    status: "inProgress",
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

// A series projection: one lifecycle, a contract-paired master, and one authored slice doc.
function seedSeries() {
  const lc: LifecycleProjection = {
    id: "LC-SER",
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
    kind: "master",
    title: "My Series",
    objective: "Series objective text",
    docPath: "/t/series/task.json",
    subTasks: [
      { number: "1", name: "First slice", file: "01_first.md", status: "inProgress", scope: "" },
      {
        number: "2",
        name: "Parallel series",
        file: "../other/task.md",
        status: "inProgress",
        scope: "",
        linkedLifecycleId: "LC-OTHER",
      },
    ],
    masterLifecycleId: "LC-PARENT",
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
    kind: "subTask",
    title: "First slice",
    objective: "Slice objective text",
    docPath: "/t/series/01_first.json",
    stepsTotal: 1,
    steps: [{ id: "S1", title: "do the thing", status: "pending", substeps: [] }],
  });
  const projection: WorkspaceProjection = {
    version: 2,
    generatedAt: "2026-06-20T09:01:00+00:00",
    lifecycles: [lc],
    enclosures: [],
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
      taskDocuments: [master, slice],
      attentionQueue: [],
      engineProcesses: [],
    },
  };
  dashboardStore.getState().applySnapshot(projection);
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("DetailPanel gate respond (task 11)", () => {
  it("renders the gate respond drawer with the full request packet", () => {
    seed("gate-review");
    const { getByTestId, queryByTestId } = render(<DetailPanel selectedId="closeout-005" />);
    expect(getByTestId("gate-review").textContent).toContain("closeout-approval");
    expect(getByTestId("gate-respond-open")).toBeTruthy();
    expect(queryByTestId("gate-approve")).toBeNull();
    fireEvent.click(getByTestId("gate-respond-open"));
    expect(getByTestId("gate-request").textContent).toContain("changedPaths");
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
  it("pins the sub-task index above the description and keeps the in-section copy", () => {
    seedSeries();
    const { getByTestId, getByText } = render(<DetailPanel selectedId="LC-SER" />);
    expect(getByText("My Series")).toBeTruthy();
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
    const { getByTestId, getByText, queryByText } = render(<DetailPanel selectedId="LC-SER" />);
    // The master overview is shown, the slice body is not.
    expect(queryByText("Slice objective text")).toBeNull();

    fireEvent.click(getByTestId("subtask-open-1"));
    expect(getByText("Slice objective text")).toBeTruthy(); // the slice's full reader
    expect(getByText("do the thing")).toBeTruthy(); // its steps render

    fireEvent.click(getByTestId("series-breadcrumb"));
    expect(getByTestId("subtask-open-1")).toBeTruthy(); // back to the master index
    expect(queryByText("Slice objective text")).toBeNull();
  });

  it("jumps lifecycles from a cross-master row and the parent breadcrumb", () => {
    seedSeries();
    const onOpenLifecycle = vi.fn();
    const { getByTestId } = render(
      <DetailPanel selectedId="LC-SER" onOpenLifecycle={onOpenLifecycle} />,
    );
    fireEvent.click(getByTestId("subtask-open-link-2")); // the "→" cross-series row
    expect(onOpenLifecycle).toHaveBeenCalledWith("LC-OTHER");
    fireEvent.click(getByTestId("master-parent-link")); // the "↑ parent series" breadcrumb
    expect(onOpenLifecycle).toHaveBeenCalledWith("LC-PARENT");
  });

  it("renders markdown in master sections (GFM table + bold), not raw", () => {
    seedSeries();
    const { container, queryByText } = render(<DetailPanel selectedId="LC-SER" />);
    const table = container.querySelector("table");
    expect(table).toBeTruthy(); // the GFM table is a real <table>, not raw pipes
    expect(container.querySelector("th")?.textContent).toBe("Slice");
    expect(container.querySelector("strong")?.textContent).toBe("strong");
    expect(queryByText(/\| Slice \| Status \|/)).toBeNull(); // raw markdown is gone
  });
});

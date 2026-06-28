import { cleanup, fireEvent, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { dashboardStore } from "../data/store";
import type {
  Analytics,
  EnclosureNode,
  LifecycleProjection,
  SeriesNode,
  TaskDocNode,
  WorkspaceProjection,
} from "../types/projection";
import { LifecycleList } from "./LifecycleList";

const EMPTY_ANALYTICS: Analytics = {
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
};

function lifecycle(over: Partial<LifecycleProjection> & Pick<LifecycleProjection, "id">) {
  return {
    state: "paused",
    phase: "build",
    fleeting: false,
    tokens: 0,
    startedAt: "2026-06-24T06:00:00+00:00",
    lastEventTs: "2026-06-24T06:00:30+00:00",
    inferred: false,
    actions: [],
    tokenSeries: [],
    ...over,
  } satisfies LifecycleProjection;
}

function enclosure(over: Partial<EnclosureNode> & Pick<EnclosureNode, "enclosure" | "lifecycleId">) {
  return {
    enclosureId: over.enclosure,
    leafId: over.enclosure,
    taskRoot: "/tasks/260610_browser-dashboard",
    taskId: "260610_BROWSER-DASHBOARD",
    taskName: "260610_browser-dashboard",
    repoName: "agents-remember",
    worktreeGroup: "/worktrees/260610-browser-dashboard",
    humanReviewStatus: "pending-review",
    closeoutStatus: "not-started",
    integrationStatus: "not-started",
    cleanup: "pending",
    actions: [],
    ...over,
  } satisfies EnclosureNode;
}

function taskDoc(over: Partial<TaskDocNode> & Pick<TaskDocNode, "title">) {
  return {
    id: "doc",
    repository: "agents-remember",
    status: "inProgress",
    kind: "subTask",
    stepsDone: 0,
    stepsTotal: 0,
    docPath: "/tasks/260610_browser-dashboard/01.json",
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
  } satisfies TaskDocNode;
}

function seriesNode(over: Partial<SeriesNode> & Pick<SeriesNode, "seriesId">) {
  return {
    repository: "agents-remember",
    title: "Browser Dashboard Series",
    status: "inProgress",
    objective: "",
    subTasks: [],
    doneCount: 0,
    totalCount: 0,
    seriesTokenTotal: 0,
    sections: [],
    decisions: [],
    docPath: "/tasks/260610_browser-dashboard/task.json",
    ...over,
  } satisfies SeriesNode;
}

function seed(projection: WorkspaceProjection) {
  dashboardStore.getState().applySnapshot(projection);
}

function projection(over: Partial<WorkspaceProjection>): WorkspaceProjection {
  const lifecycles = over.lifecycles ?? [];
  return {
    version: 2,
    generatedAt: "2026-06-24T06:01:00+00:00",
    lifecycles,
    enclosures: [],
    providers: [],
    activeWorktreeGroups: [],
    metrics: {
      lifecycleCount: lifecycles.length,
      runningCount: lifecycles.filter((entry) => entry.state === "running").length,
      blockedCount: lifecycles.filter((entry) => entry.state === "blocked").length,
      pausedCount: lifecycles.filter((entry) => entry.state === "paused").length,
      totalTokens: lifecycles.reduce((sum, entry) => sum + entry.tokens, 0),
      stalenessHistogram: {},
    },
    analytics: EMPTY_ANALYTICS,
    ...over,
  };
}

afterEach(() => {
  cleanup();
  dashboardStore.getState().reset();
});

describe("LifecycleList task labels", () => {
  it("limits sidebar rows to root docs, enclosure-matched leaves, and enclosure fallbacks", () => {
    const onSelect = vi.fn();
    seed(
      projection({
        lifecycles: [
          lifecycle({
            id: "260610_BROWSER-DASHBOARD",
            repoId: "agents-remember",
            phase: "close",
            enclosure: "/contracts/15",
          }),
          lifecycle({
            id: "LC-16",
            repoId: "agents-remember",
            enclosure: "/contracts/16",
          }),
          lifecycle({
            id: "LC-EMPTY",
            repoId: "agents-remember",
            enclosure: "/contracts/empty",
          }),
          lifecycle({
            id: "LC-ABANDONED",
            state: "abandoned",
            phase: "reframe-research",
            fleeting: true,
          }),
        ],
        enclosures: [
          enclosure({
            enclosure: "/contracts/15",
            lifecycleId: "260610_BROWSER-DASHBOARD",
            leafId: "15_parallel-leaf-enclosure-workflow",
          }),
          enclosure({
            enclosure: "/contracts/16",
            lifecycleId: "LC-16",
            leafId: "16_engine-room-stack-entry-height",
            cleanup: "completed",
          }),
          enclosure({
            enclosure: "/contracts/empty",
            lifecycleId: "LC-EMPTY",
            leafId: "empty-state-backdrop-zoom-stability",
          }),
        ],
        analytics: {
          ...EMPTY_ANALYTICS,
          taskDocuments: [
            taskDoc({
              kind: "master",
              lifecycleId: "260610_BROWSER-DASHBOARD",
              title: "Browser Dashboard Series",
              docPath: "/tasks/260610_browser-dashboard/task.json",
            }),
            taskDoc({
              id: "15",
              lifecycleId: "260610_BROWSER-DASHBOARD",
              title: "Parallel Leaf Enclosure Workflow",
              docPath: "/tasks/260610_browser-dashboard/15_parallel-leaf-enclosure-workflow.json",
            }),
            taskDoc({
              id: "16",
              lifecycleId: "LC-16",
              title: "Engine Room Stack Entry Height",
              docPath: "/tasks/260610_browser-dashboard/16_engine-room-stack-entry-height.json",
            }),
            taskDoc({
              lifecycleId: "260610_BROWSER-DASHBOARD",
              title: "Lifecycle + Event + Gate Design",
              docPath: "/tasks/260610_browser-dashboard/01_lifecycle-event-gate-design.json",
            }),
            taskDoc({
              lifecycleId: undefined,
              title: "Plan Before Worktree",
              docPath: "/tasks/260610_browser-dashboard/19_plan-before-worktree.json",
              status: "planning",
            }),
          ],
          series: [
            seriesNode({
              seriesId: "260610_browser-dashboard",
              subTasks: [
                {
                  number: "15",
                  name: "Parallel Leaf Enclosure Workflow",
                  file: "15_parallel-leaf-enclosure-workflow.md",
                  status: "inProgress",
                  scope: "",
                  createdAt: "2026-06-20T09:00:00+00:00",
                },
                {
                  number: "16",
                  name: "Engine Room Stack Entry Height",
                  file: "16_engine-room-stack-entry-height.md",
                  status: "inProgress",
                  scope: "",
                  createdAt: "2026-06-21T09:00:00+00:00",
                },
              ],
            }),
          ],
        },
      }),
    );

    const { getByText, queryByText } = render(<LifecycleList selectedId={null} onSelect={onSelect} />);

    expect(getByText("Tasks · 3")).toBeTruthy();
    expect(getByText("Browser Dashboard Series")).toBeTruthy();
    expect(getByText("15. Parallel Leaf Enclosure Workflow")).toBeTruthy();
    expect(queryByText("16. Engine Room Stack Entry Height")).toBeNull();
    expect(getByText("empty-state-backdrop-zoom-stability")).toBeTruthy();
    expect(queryByText("Lifecycle + Event + Gate Design")).toBeNull();
    expect(queryByText("Plan Before Worktree")).toBeNull();
    expect(queryByText("LC-ABANDONED")).toBeNull();
    expect(getByText("15. Parallel Leaf Enclosure Workflow").closest("[data-depth='1']")).toBeTruthy();
    expect(getByText("15. Parallel Leaf Enclosure Workflow").closest("[data-parent-key]")?.getAttribute("data-parent-key")).toBe(
      "taskdoc:/tasks/260610_browser-dashboard/task.json",
    );

    fireEvent.click(getByText("Browser Dashboard Series"));
    expect(onSelect).toHaveBeenCalledWith("taskdoc:/tasks/260610_browser-dashboard/task.json");
    fireEvent.click(getByText("15. Parallel Leaf Enclosure Workflow"));
    expect(onSelect).toHaveBeenCalledWith(
      "taskdoc:/tasks/260610_browser-dashboard/15_parallel-leaf-enclosure-workflow.json",
    );

    fireEvent.click(getByText("BY PHASE"));
    expect(getByText("15. Parallel Leaf Enclosure Workflow").closest("[data-depth='0']")).toBeTruthy();
  });

  it("nests numbered leaf docs whose enclosure leaf id is shorter than the task file stem", () => {
    const onSelect = vi.fn();
    seed(
      projection({
        lifecycles: [lifecycle({ id: "LC-31", repoId: "agents-remember" })],
        enclosures: [
          enclosure({
            enclosure: "/contracts/31",
            lifecycleId: "LC-31",
            leafId: "31",
          }),
        ],
        analytics: {
          ...EMPTY_ANALYTICS,
          taskDocuments: [
            taskDoc({
              kind: "master",
              title: "Browser Dashboard Series",
              docPath: "/tasks/260610_browser-dashboard/task.json",
            }),
            taskDoc({
              id: "31",
              lifecycleId: "LC-31",
              title: "Provider State Refresh and Engine Room Honesty",
              docPath: "/tasks/260610_browser-dashboard/31_provider-state-refresh-and-engine-room-honesty.json",
            }),
          ],
          series: [
            seriesNode({
              seriesId: "260610_browser-dashboard",
              subTasks: [
                {
                  number: "31",
                  name: "Provider State Refresh and Engine Room Honesty",
                  file: "31_provider-state-refresh-and-engine-room-honesty.md",
                  status: "inProgress",
                  scope: "",
                  createdAt: "2026-06-27T22:33:00+00:00",
                },
              ],
            }),
          ],
        },
      }),
    );

    const { getByText } = render(<LifecycleList selectedId={null} onSelect={onSelect} />);
    const row = getByText("31. Provider State Refresh and Engine Room Honesty");

    expect(getByText("Tasks · 2")).toBeTruthy();
    expect(row.closest("[data-depth='1']")).toBeTruthy();
    expect(row.closest("[data-parent-key]")?.getAttribute("data-parent-key")).toBe(
      "taskdoc:/tasks/260610_browser-dashboard/task.json",
    );
  });

  it("keeps standalone root task documents visible without listing loose leaf docs", () => {
    const onSelect = vi.fn();
    seed(
      projection({
        lifecycles: [],
        analytics: {
          ...EMPTY_ANALYTICS,
          taskDocuments: [
            taskDoc({
              kind: "light",
              title: "Standalone Planning Task",
              docPath: "/tasks/repo-a/plan/task.json",
              createdAt: "2026-06-20T09:00:00+00:00",
              status: "planning",
            }),
            taskDoc({
              title: "Loose Leaf Plan",
              docPath: "/tasks/repo-a/plan/01_plan.json",
              createdAt: "2026-06-21T09:00:00+00:00",
              status: "planning",
            }),
          ],
        },
      }),
    );

    const { getByText, queryByText } = render(<LifecycleList selectedId={null} onSelect={onSelect} />);

    expect(getByText("Standalone Planning Task")).toBeTruthy();
    expect(queryByText("Loose Leaf Plan")).toBeNull();
    fireEvent.click(getByText("Standalone Planning Task"));
    expect(onSelect).toHaveBeenCalledWith("taskdoc:/tasks/repo-a/plan/task.json");
  });

  it("exposes the full long task title and row context on title hover", () => {
    const longTitle =
      "Operations task reader row title that is intentionally long enough to require ellipsis in the left rail";
    seed(
      projection({
        lifecycles: [
          lifecycle({
            id: "01KVWK7Z8PQZ7BV9T6QPXFHM3B",
            state: "blocked",
            phase: "reframe-research",
            repoId: "agents-remember",
            gate: {
              id: "gate-1",
              kind: "plan-approval",
              state: "open",
              decisions: ["approve", "revise"],
              packet: {},
              ts: "2026-06-24T06:00:40+00:00",
            },
          }),
        ],
        enclosures: [
          enclosure({
            enclosure: "/contracts/long-title",
            lifecycleId: "01KVWK7Z8PQZ7BV9T6QPXFHM3B",
            leafId: "01",
          }),
        ],
        analytics: {
          ...EMPTY_ANALYTICS,
          taskDocuments: [
            taskDoc({
              lifecycleId: "01KVWK7Z8PQZ7BV9T6QPXFHM3B",
              title: longTitle,
              currentStep: "Constrain Tasks panel row title layout",
              stepsDone: 1,
              stepsTotal: 4,
            }),
          ],
        },
      }),
    );

    const { getByText } = render(<LifecycleList selectedId={null} onSelect={() => {}} />);

    const title = getByText(longTitle);
    const row = title.closest("[role='option']");
    expect(row?.className).toContain("min-w_0");
    expect(row?.className).toContain("max-w_100%");
    expect(row?.lastElementChild?.className).toContain("tov_ellipsis");
    expect(row?.lastElementChild?.className).not.toContain("ml_auto");
    expect(title.className).toContain("flex_1_1_0");
    expect(title.getAttribute("title")).toContain(`Title: ${longTitle}`);
    expect(title.getAttribute("title")).toContain("Lifecycle: 01KVWK7Z8PQZ7BV9T6QPXFHM3B");
    expect(title.getAttribute("title")).toContain("State: blocked");
    expect(title.getAttribute("title")).toContain("Phase: reframe-research");
    expect(title.getAttribute("title")).toContain("Repo: agents-remember");
    expect(title.getAttribute("title")).toContain("Gate: plan-approval");
    expect(title.getAttribute("title")).toContain(
      "Current step: Constrain Tasks panel row title layout",
    );
  });
});

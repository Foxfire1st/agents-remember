import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { dashboardStore } from "../data/store";
import type {
  Analytics,
  EnclosureNode,
  LifecycleProjection,
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

function taskDoc(over: Partial<TaskDocNode> & Pick<TaskDocNode, "lifecycleId" | "title">) {
  return {
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
  it("shows the leaf enclosure name for a promoted fleeting lifecycle", () => {
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
            id: "01KVW2FE8MQK6QCQQP0J4SEK3C",
            repoId: "agents-remember",
            enclosure: "/contracts/16",
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
            lifecycleId: "01KVW2FE8MQK6QCQQP0J4SEK3C",
            leafId: "16_engine-room-stack-entry-height",
          }),
        ],
        analytics: {
          ...EMPTY_ANALYTICS,
          taskDocuments: [
            taskDoc({
              lifecycleId: "260610_BROWSER-DASHBOARD",
              title: "Lifecycle + Event + Gate Design",
            }),
            taskDoc({
              lifecycleId: "260610_BROWSER-DASHBOARD",
              title: "Parallel Leaf Enclosure Workflow",
            }),
          ],
        },
      }),
    );

    const { getByText, queryByText } = render(<LifecycleList selectedId={null} onSelect={() => {}} />);

    expect(getByText("260610_browser-dashboard")).toBeTruthy();
    expect(getByText("16_engine-room-stack-entry-height")).toBeTruthy();
    expect(queryByText("01KVW2FE8MQK6QCQQP0J4SEK3C")).toBeNull();
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

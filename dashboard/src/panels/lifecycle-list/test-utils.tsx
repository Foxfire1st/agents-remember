import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

import { dashboardStore } from "../../data/store";
import { sessionStore } from "../../data/sessions";
import { metricsFor } from "../../types/projection";
import type {
  Analytics,
  EnclosureNode,
  LifecycleProjection,
  SeriesNode,
  TaskDocNode,
  WorkspaceProjection,
} from "../../types/projection";

export const EMPTY_ANALYTICS: Analytics = {
  driftSnapshots: [],
  stalestSidecars: [],
  setupSummaries: [],
  setupProgress: [],
  routeCoverage: [],
  toolReports: [],
  agentPickups: [],
  expectationRows: [],
  ledgers: [],
  taskDocuments: [],
  series: [],
  attentionQueue: [],
  engineProcesses: [],
};

export function lifecycle(over: Partial<LifecycleProjection> & Pick<LifecycleProjection, "id">) {
  return {
    state: "paused",
    phase: "build",
    fleeting: false,
    tokens: 0,
    startedAt: "2026-06-24T06:00:00+00:00",
    lastEventTs: "2026-06-24T06:00:30+00:00",
    stateEnteredAt: "2026-06-24T06:00:00+00:00",
    inferred: false,
    actions: [],
    tokenSeries: [],
    ...over,
  } satisfies LifecycleProjection;
}

export function enclosure(over: Partial<EnclosureNode> & Pick<EnclosureNode, "enclosure" | "lifecycleId">) {
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
    codeWorktreeExists: true,
    memoryWorktreeExists: true,
    actions: [],
    ...over,
  } satisfies EnclosureNode;
}

export function taskDoc(over: Partial<TaskDocNode> & Pick<TaskDocNode, "title">) {
  return {
    id: "doc",
    repository: "agents-remember",
    status: "inProgress",
    kind: "subTask",
    stepsDone: 0,
    stepsTotal: 0,
    docPath: "/tasks/260610_browser-dashboard/01.json",
    bodyRevision: "rev-fixture",
    createdAt: "2026-06-24T06:00:00+00:00",
    steps: [],
    objective: "",
    requirements: [],
    codeExamples: [],
    decisions: [],
    openQuestions: [],
    references: [],
    subTasks: [],
    sections: [],
    orchestrates: [],
    ...over,
  } satisfies TaskDocNode;
}

export function seriesNode(over: Partial<SeriesNode> & Pick<SeriesNode, "seriesId">) {
  return {
    repository: "agents-remember",
    title: "Browser Dashboard Series",
    status: "inProgress",
    createdAt: "2026-06-24T06:00:00+00:00",
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

export function seed(projection: WorkspaceProjection) {
  dashboardStore.getState().applySnapshot(projection);
}

export function projection(over: Partial<WorkspaceProjection>): WorkspaceProjection {
  const lifecycles = over.lifecycles ?? [];
  return {
    version: 2,
    generatedAt: "2026-06-24T06:01:00+00:00",
    lifecycles,
    enclosures: [],
    providers: [],
    activeWorktreeGroups: [],
    metrics: metricsFor(lifecycles),
    analytics: EMPTY_ANALYTICS,
    ...over,
  };
}

export function collapsibleHierarchyProjection(): WorkspaceProjection {
  return projection({
    enclosures: collapsibleEnclosures(),
    analytics: {
      ...EMPTY_ANALYTICS,
      taskDocuments: collapsibleTaskDocuments(),
      series: collapsibleSeries(),
    },
  });
}

function collapsibleEnclosures(): ReturnType<typeof enclosure>[] {
  return [
      enclosure({
        enclosure: "/contracts/a1",
        lifecycleId: "",
        leafId: "01_leaf-a1",
        taskRoot: "/tasks/master-a",
      }),
      enclosure({
        enclosure: "/contracts/b1",
        lifecycleId: "",
        leafId: "01_leaf-b1",
        taskRoot: "/tasks/master-b",
      }),
  ];
}

function collapsibleTaskDocuments(): ReturnType<typeof taskDoc>[] {
  return [
        taskDoc({
          id: "SPRINT-02",
          kind: "master",
          title: "Sprint 02",
          docPath: "/tasks/sprint-02/task.json",
          orchestrates: ["master-a", "master-b"],
          createdAt: "2026-06-19T09:00:00+00:00",
        }),
        taskDoc({
          kind: "master",
          title: "Master A",
          docPath: "/tasks/master-a/task.json",
          createdAt: "2026-06-20T08:00:00+00:00",
        }),
        taskDoc({
          kind: "master",
          title: "Master B",
          docPath: "/tasks/master-b/task.json",
          createdAt: "2026-06-20T09:00:00+00:00",
        }),
        taskDoc({
          kind: "master",
          title: "Empty Master",
          docPath: "/tasks/empty-master/task.json",
          createdAt: "2026-06-20T10:00:00+00:00",
        }),
        taskDoc({
          id: "01",
          title: "Leaf A1",
          docPath: "/tasks/master-a/01_leaf-a1.json",
          createdAt: "2026-06-21T08:00:00+00:00",
        }),
        taskDoc({
          id: "01",
          title: "Leaf B1",
          docPath: "/tasks/master-b/01_leaf-b1.json",
          createdAt: "2026-06-21T09:00:00+00:00",
        }),
  ];
}

function collapsibleSeries(): ReturnType<typeof seriesNode>[] {
  return [
        seriesNode({
          seriesId: "master-a",
          title: "Master A",
          docPath: "/tasks/master-a/task.json",
          subTasks: [
            {
              number: "01",
              name: "Leaf A1",
              file: "01_leaf-a1.md",
              status: "inProgress",
              scope: "",
              createdAt: "2026-06-21T08:00:00+00:00",
            },
          ],
        }),
        seriesNode({
          seriesId: "master-b",
          title: "Master B",
          docPath: "/tasks/master-b/task.json",
          subTasks: [
            {
              number: "01",
              name: "Leaf B1",
              file: "01_leaf-b1.md",
              status: "inProgress",
              scope: "",
              createdAt: "2026-06-21T09:00:00+00:00",
            },
          ],
        }),
  ];
}

export function installLifecycleListCleanup() {
  afterEach(() => {
    cleanup();
    dashboardStore.getState().reset();
    sessionStore.getState().hydrate([]);
    window.localStorage.clear();
  });
}

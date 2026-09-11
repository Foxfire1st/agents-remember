import { act, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { dashboardStore } from "../../data/store";
import type { CloseoutQueueNode, TaskExecutionGraphView } from "../../types/projection";
import { DetailPanel } from "../detail-panel/DetailPanel";
import { seedTaskDocuments, taskDoc } from "../detail-panel/test-utils";

const SPRINT_REF = { repository: "repo-a", path: "sprint/task.json" };

function sprintGraph(): TaskExecutionGraphView {
  return {
    nodes: [
      {
        nodeId: "repo-a/master-a/task.json",
        kind: "lump",
        masterRef: { repository: "repo-a", path: "master-a/task.json" },
        masterTitle: "Master A",
        leafIds: [],
        leafTitles: [],
        waveIndex: 1,
        frontierState: "ready",
        executionNature: "atomic",
        predecessors: [],
      },
    ],
  };
}

function queue(over: Partial<CloseoutQueueNode> = {}): CloseoutQueueNode {
  return {
    sprintRef: SPRINT_REF,
    revision: 3,
    serviceCondition: "valid-built",
    sourceClassification: "active",
    sourceFingerprint: "ab".repeat(32),
    sourceProblems: [],
    members: [
      {
        generationId: "cd".repeat(32),
        taskDocumentRef: { repository: "repo-a", path: "master-a/leaf-a.json" },
        owningMaster: { repository: "repo-a", path: "master-a/task.json" },
        classification: "ready",
        priority: "high",
        order: 0,
        reasons: [],
      },
    ],
    ...over,
  };
}

// Shell-level reachability (L12-R5): the sprint page must mount the graph view AND the
// CloseoutQueue panel, so a panel that is exported but unmounted fails this test instead of
// passing silently. The test renders the real DetailPanel surface (the sprint page in the
// Operations viewport), not the panel in isolation.
describe("sprint page shell (L12-R5)", () => {
  it("mounts the graph view and the closeout queue on the sprint page", () => {
    const sprint = taskDoc({
      id: "SPRINT",
      kind: "master",
      title: "Sprint",
      orchestrates: ["master-a"],
      docPath: "/tasks/repo-a/sprint/task.json",
      executionGraphView: sprintGraph(),
    });
    seedTaskDocuments([sprint]);
    dashboardStore.setState({ closeoutQueues: [queue()] });

    render(<DetailPanel selectedId="taskdoc:/tasks/repo-a/sprint/task.json" />);

    // the wave-grid graph is reachable from the sprint page
    expect(screen.getByTestId("sprint-graph")).toBeTruthy();
    expect(screen.getByText("Wave 1")).toBeTruthy();
    // the CloseoutQueue panel is mounted here, scoped to this sprint, with its revision meta
    expect(screen.getByTestId("closeout-queue")).toBeTruthy();
    expect(screen.getByText("rev 3 · valid-built · active")).toBeTruthy();
  });

  it("scopes the queue to the viewed sprint when another sprint has a queue", () => {
    const sprint = taskDoc({
      id: "SPRINT",
      kind: "master",
      title: "Sprint",
      orchestrates: ["master-a"],
      docPath: "/tasks/repo-a/sprint/task.json",
      executionGraphView: sprintGraph(),
    });
    seedTaskDocuments([sprint]);
    dashboardStore.setState({
      closeoutQueues: [
        queue(),
        queue({
          sprintRef: { repository: "repo-a", path: "other-sprint/task.json" },
          revision: 9,
        }),
      ],
    });

    render(<DetailPanel selectedId="taskdoc:/tasks/repo-a/sprint/task.json" />);

    expect(screen.getByTestId("closeout-queue")).toBeTruthy();
    expect(screen.getByText("rev 3 · valid-built · active")).toBeTruthy();
    expect(screen.queryByText("rev 9 · valid-built · active")).toBeNull();
  });

  it("keeps a graphless atomic-sequential sprint queue reachable", () => {
    const sprint = taskDoc({
      id: "SPRINT",
      kind: "master",
      title: "Graphless sprint",
      orchestrates: ["master-a"],
      docPath: "/tasks/repo-a/sprint/task.json",
      executionGraphView: undefined,
    });
    seedTaskDocuments([sprint]);
    dashboardStore.setState({ closeoutQueues: [queue()] });

    render(<DetailPanel selectedId="taskdoc:/tasks/repo-a/sprint/task.json" />);

    expect(screen.queryByTestId("sprint-graph")).toBeNull();
    expect(screen.getByTestId("closeout-queue")).toBeTruthy();
    expect(screen.getByText("rev 3 · valid-built · active")).toBeTruthy();
  });

  it("removes a seeded queue and its mounted sprint view through the canonical reset", () => {
    const staleCandidatePath = "scenario-a/stale-closeout-candidate.json";
    const staleQueueMetadata = "rev 731 · valid-built · scenario-a-stale-source";
    const sprint = taskDoc({
      id: "SPRINT",
      kind: "master",
      title: "Scenario reset sprint",
      orchestrates: ["master-a"],
      docPath: "/tasks/repo-a/sprint/task.json",
      executionGraphView: sprintGraph(),
    });
    seedTaskDocuments([sprint]);
    dashboardStore.setState({
      closeoutQueues: [
        queue({
          revision: 731,
          sourceClassification: "scenario-a-stale-source",
          members: [
            {
              generationId: "ef".repeat(32),
              taskDocumentRef: { repository: "repo-a", path: staleCandidatePath },
              owningMaster: { repository: "repo-a", path: "master-a/task.json" },
              classification: "ready",
              priority: "high",
              order: 0,
              reasons: [],
            },
          ],
        }),
      ],
    });

    render(<DetailPanel selectedId="taskdoc:/tasks/repo-a/sprint/task.json" />);

    expect(dashboardStore.getState().closeoutQueues).toHaveLength(1);
    expect(screen.getByTestId("closeout-queue")).toBeTruthy();
    expect(screen.getByText(staleCandidatePath)).toBeTruthy();
    expect(screen.getByText(staleQueueMetadata)).toBeTruthy();

    act(() => {
      dashboardStore.getState().reset();
    });

    expect(dashboardStore.getState().closeoutQueues).toEqual([]);
    expect(screen.queryByTestId("closeout-queue")).toBeNull();
    expect(screen.queryByText(staleCandidatePath)).toBeNull();
    expect(screen.queryByText(staleQueueMetadata)).toBeNull();
  });
});

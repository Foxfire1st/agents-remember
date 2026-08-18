import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { dashboardStore } from "../data/store";
import type { CloseoutQueueNode } from "../types/projection";
import { CloseoutQueue } from "./CloseoutQueue";

function queue(overrides: Partial<CloseoutQueueNode> = {}): CloseoutQueueNode {
  return {
    sprintRef: { repository: "repo-a", path: "sprint/task.json" },
    revision: 0,
    graphRevision: "0".repeat(64),
    candidates: [
      {
        taskDocumentRef: { repository: "repo-a", path: "master-a/leaf-a.json" },
        owningMaster: { repository: "repo-a", path: "master-a/task.json" },
        candidateState: "declared",
        gradePriority: "normal",
        reasons: [],
      },
      {
        taskDocumentRef: { repository: "repo-a", path: "master-b/leaf-b.json" },
        owningMaster: { repository: "repo-a", path: "master-b/task.json" },
        candidateState: "declared",
        gradePriority: undefined,
        reasons: ["explicit-grade-required"],
      },
    ],
    ...overrides,
  };
}

describe("CloseoutQueue", () => {
  beforeEach(() => {
    dashboardStore.setState({ closeoutQueues: [queue()] });
  });

  afterEach(() => {
    cleanup();
    dashboardStore.getState().reset();
  });

  it("renders candidates with state, grade, and reasons", () => {
    render(<CloseoutQueue />);
    expect(screen.getByText("master-a/leaf-a.json")).toBeTruthy();
    expect(screen.getByText("declared · normal")).toBeTruthy();
    expect(screen.getByText("master-b/leaf-b.json")).toBeTruthy();
    expect(screen.getByText("explicit-grade-required")).toBeTruthy();
  });

  it("renders the active atomic blocker", () => {
    dashboardStore.setState({
      closeoutQueues: [
        queue({
          activeBlocker: {
            master: { repository: "repo-a", path: "master-b/task.json" },
            rationale: "atomic unit integration",
            acquiredBy: "orchestrator",
          },
        }),
      ],
    });
    render(<CloseoutQueue />);
    expect(screen.getByText("blocker: master-b/task.json")).toBeTruthy();
    expect(screen.getByText("atomic unit integration")).toBeTruthy();
  });

  it("renders nothing when no queue is projected", () => {
    dashboardStore.setState({ closeoutQueues: [] });
    const { container } = render(<CloseoutQueue />);
    expect(container.firstChild).toBeNull();
  });
});

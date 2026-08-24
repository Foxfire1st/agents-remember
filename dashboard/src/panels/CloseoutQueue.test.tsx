import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { dashboardStore } from "../data/store";
import type { CloseoutQueueNode } from "../types/projection";
import { CloseoutQueue } from "./CloseoutQueue";

function queue(overrides: Partial<CloseoutQueueNode> = {}): CloseoutQueueNode {
  return {
    sprintRef: { repository: "repo-a", path: "sprint/task.json" },
    revision: 0,
    serviceCondition: "valid-built",
    sourceClassification: "active",
    sourceFingerprint: "0".repeat(64),
    sourceProblems: [],
    members: [
      {
        generationId: "1".repeat(64),
        taskDocumentRef: { repository: "repo-a", path: "master-a/leaf-a.json" },
        owningMaster: { repository: "repo-a", path: "master-a/task.json" },
        classification: "ready",
        priority: "normal",
        order: 0,
        reasons: [],
      },
      {
        generationId: "2".repeat(64),
        taskDocumentRef: { repository: "repo-a", path: "master-b/leaf-b.json" },
        owningMaster: { repository: "repo-a", path: "master-b/task.json" },
        classification: "blocked",
        priority: "low",
        order: 1,
        reasons: ["door-scheduling-provenance-stale"],
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
    expect(screen.getByText("ready · normal")).toBeTruthy();
    expect(screen.getByText("master-b/leaf-b.json")).toBeTruthy();
    expect(screen.getByText("door-scheduling-provenance-stale")).toBeTruthy();
  });

  it("renders typed non-admitting repair evidence", () => {
    dashboardStore.setState({
      closeoutQueues: [
        queue({
          serviceCondition: "invalid-empty",
          sourceClassification: undefined,
          sourceFingerprint: undefined,
          members: [],
          sourceProblems: [{
            kind: "projection",
            address: "/coord/tasks/repo-a/sprint/artifacts/closeout-candidates.json",
            state: "unreadable",
            errorType: "source-fingerprint-mismatch",
            repairAction: "closeout_queue(action='rebuild')",
          }],
        }),
      ],
    });
    render(<CloseoutQueue />);
    expect(screen.getByText("source-fingerprint-mismatch")).toBeTruthy();
    expect(screen.getByText("closeout_queue(action='rebuild')")).toBeTruthy();
  });

  it("renders nothing when no queue is projected", () => {
    dashboardStore.setState({ closeoutQueues: [] });
    const { container } = render(<CloseoutQueue />);
    expect(container.firstChild).toBeNull();
  });
});

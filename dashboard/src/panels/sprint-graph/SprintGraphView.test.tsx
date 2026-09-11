import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { TaskExecutionGraphView, TaskExecutionNodeView } from "../../types/projection";
import { SprintGraphView } from "./SprintGraphView";
import { leafLineStyles, waveGridStyles } from "./styles";

const MASTER_A = { repository: "repo-a", path: "master-a/task.json" };
const MASTER_B = { repository: "repo-a", path: "master-b/task.json" };
const ATOMIC_F = { repository: "repo-a", path: "atomic-f/task.json" };

function node(over: Partial<TaskExecutionNodeView>): TaskExecutionNodeView {
  return {
    nodeId: "node",
    kind: "lump",
    masterRef: MASTER_A,
    masterTitle: "Master",
    leafIds: [],
    leafTitles: [],
    waveIndex: 1,
    frontierState: "ready",
    predecessors: [],
    ...over,
  };
}

// A zero-edge sprint: every master independent, all in one derived wave (L12-R7 scenario).
const zeroEdgeGraph: TaskExecutionGraphView = {
  nodes: [
    node({
      nodeId: "repo-a/master-a/task.json",
      kind: "segment",
      masterRef: MASTER_A,
      masterTitle: "Master A",
      leafIds: ["A-L1", "A-L2"],
      leafTitles: ["Leaf one", "Leaf two"],
      waveIndex: 1,
      frontierState: "in-flight",
    }),
    node({
      nodeId: "repo-a/master-b/task.json",
      kind: "lump",
      masterRef: MASTER_B,
      masterTitle: "Master B (atomic)",
      waveIndex: 1,
      frontierState: "ready",
      executionNature: "atomic",
    }),
  ],
};

// A segmented-master scenario: OM1's early segment in wave 1 gates the atomic block in wave 2,
// which gates OM1's late segment in wave 3 (L12-R7 scenario).
const segmentedGraph: TaskExecutionGraphView = {
  nodes: [
    node({
      nodeId: "repo-a/master-a/task.json#seg1",
      kind: "segment",
      masterRef: MASTER_A,
      masterTitle: "Master One",
      leafIds: ["OM1-L1", "OM1-L2"],
      leafTitles: ["Shared framework", "Control bridge"],
      waveIndex: 1,
      frontierState: "in-flight",
    }),
    node({
      nodeId: "repo-a/atomic-f/task.json",
      kind: "lump",
      masterRef: ATOMIC_F,
      masterTitle: "Atomic F",
      waveIndex: 2,
      frontierState: "waiting",
      executionNature: "atomic",
      predecessors: [
        {
          predecessorRef: MASTER_A,
          predecessorTitle: "Master One",
          reason: "OM1's early segment lands before the atomic block",
        },
      ],
    }),
    node({
      nodeId: "repo-a/master-a/task.json#seg2",
      kind: "segment",
      masterRef: MASTER_A,
      masterTitle: "Master One",
      leafIds: ["OM1-L3"],
      leafTitles: ["Late leaf"],
      waveIndex: 3,
      frontierState: "ready",
      predecessors: [
        {
          predecessorRef: ATOMIC_F,
          predecessorTitle: "Atomic F",
          reason: "the atomic block gates OM1's L3 segment",
        },
      ],
    }),
  ],
};

describe("SprintGraphView (L12-R2/R6/R7)", () => {
  it("renders a zero-edge graph as one wave row of independent boxes (no predecessors)", () => {
    render(<SprintGraphView graphView={zeroEdgeGraph} />);
    expect(screen.getByTestId("sprint-graph")).toBeTruthy();
    expect(screen.getByTestId("graph-wave-1")).toBeTruthy();
    expect(screen.queryByTestId("graph-wave-2")).toBeNull();
    expect(screen.getAllByTestId("graph-box")).toHaveLength(2);
    expect(screen.getByText("Master A")).toBeTruthy();
    // one ellipsized leaf line per leaf with id + title
    expect(screen.getByText("A-L1 — Leaf one")).toBeTruthy();
    expect(screen.getByText("A-L2 — Leaf two")).toBeTruthy();
    // an atomic master renders as a lump box with no leaf list
    const lump = screen.getByTestId("graph-lump");
    expect(lump.textContent).toBe("atomic unit");
    expect(screen.queryByTestId("graph-predecessor")).toBeNull();
  });

  it("renders a segmented master across waves with labeled edge reasons", () => {
    render(<SprintGraphView graphView={segmentedGraph} />);
    expect(screen.getByTestId("graph-wave-1")).toBeTruthy();
    expect(screen.getByTestId("graph-wave-2")).toBeTruthy();
    expect(screen.getByTestId("graph-wave-3")).toBeTruthy();
    // one box per node: two Master One segments + one atomic lump
    expect(screen.getAllByTestId("graph-box")).toHaveLength(3);
    expect(screen.getAllByText("Master One")).toHaveLength(2);
    expect(screen.getAllByText("OM1-L3 — Late leaf")).toHaveLength(1);
    // edges render with their reasons as textual dependency labels
    const reasons = screen
      .getAllByTestId("graph-predecessor")
      .map((item) => item.textContent);
    expect(reasons).toContain("← Master One — OM1's early segment lands before the atomic block");
    expect(reasons).toContain("← Atomic F — the atomic block gates OM1's L3 segment");
  });

  it("exposes frontier state per node for styling", () => {
    render(<SprintGraphView graphView={segmentedGraph} />);
    const boxes = screen.getAllByTestId("graph-box");
    expect(boxes.map((box) => box.dataset.frontier)).toEqual([
      "in-flight",
      "waiting",
      "ready",
    ]);
  });

  it("pins the at-most-3-per-row grid and the narrow single-column fallback (L12-R6)", () => {
    expect(waveGridStyles.gridTemplateColumns).toBe("repeat(3, minmax(0, 1fr))");
    const narrow = waveGridStyles["@media (max-width: 720px)"];
    expect(narrow.gridTemplateColumns).toBe("1fr");
    // the narrow layout still renders the same wave-ordered boxes with predecessor info
    render(<SprintGraphView graphView={segmentedGraph} />);
    expect(screen.getAllByTestId("graph-box")).toHaveLength(3);
    expect(screen.getAllByTestId("graph-predecessor")).toHaveLength(2);
  });

  it("pins the ellipsized leaf line with viewport-growing character ranges", () => {
    expect(leafLineStyles.whiteSpace).toBe("nowrap");
    expect(leafLineStyles.overflow).toBe("hidden");
    expect(leafLineStyles.textOverflow).toBe("ellipsis");
    expect(leafLineStyles.maxWidth).toBe("min(100%, 30ch)");
    // the visible character range grows with the viewport
    expect(leafLineStyles.lg?.maxWidth).toBe("min(100%, 72ch)");
  });
});
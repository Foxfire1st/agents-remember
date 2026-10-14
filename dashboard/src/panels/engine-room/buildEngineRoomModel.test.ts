import { describe, expect, it } from "vitest";

import type {
  EngineProcessNode,
  LifecycleProjection,
} from "../../types/projection";
import { buildEngineRoomModel } from "./buildEngineRoomModel";

function node(over: Partial<EngineProcessNode> = {}): EngineProcessNode {
  return {
    id: "/c.md",
    enclosure: "/c.md",
    worktreeGroup: "/w/r/grp",
    taskId: "T",
    leafId: "demo-leaf",
    taskName: "demo",
    repoName: "r",
    phase: "worktree-started",
    health: "nominal",
    codeSource: { factState: "derived" },
    codeWorktree: { factState: "observed", exists: true },
    memoryMode: "external",
    humanReviewStatus: "pending-review",
    closeoutStatus: "not-started",
    integrationStatus: "not-started",
    cleanup: "pending",
    completedPhases: [],
    failedPhases: [],
    seedFallback: false,
    providers: [],
    edges: [],
    landing: [],
    ledgerRows: [],
    ledgerRowCount: 0,
    actions: [],
    summary: "",
    missingFacts: [],
    sourceFiles: [],
    ...over,
  };
}

const lifecycle = (
  over: Partial<LifecycleProjection> & Pick<LifecycleProjection, "id">,
): LifecycleProjection => ({
  state: "running",
  phase: "build",
  fleeting: false,
  startedAt: "",
  lastEventTs: "",
  inferred: false,
  ...over,
});

describe("buildEngineRoomModel", () => {
  it("attaches each process to its lifecycle by lifecycleId", () => {
    const model = buildEngineRoomModel([node({ lifecycleId: "L1" })], [lifecycle({ id: "L1" })]);
    expect(model.processes).toHaveLength(1);
    expect(model.processes[0]?.lifecycle?.id).toBe("L1");
  });

  it("builds no provider fallback from an empty process surface (MIK-R112)", () => {
    const model = buildEngineRoomModel([], []);
    expect(model.processes).toHaveLength(0);
  });

  it("exposes worktreeGroup as the enclosure key, stable across a fleeting→real id swap", () => {
    const fleeting = buildEngineRoomModel([node({ id: "start:demo" })], []);
    const real = buildEngineRoomModel([node({ id: "/series-contract.md" })], []);
    expect(fleeting.processes[0]?.enclosureKey).toBe("/w/r/grp");
    expect(real.processes[0]?.enclosureKey).toBe("/w/r/grp");
    expect(fleeting.processes[0]?.enclosureKey).toBe(real.processes[0]?.enclosureKey);
  });

  it("orders active leaf enclosures before cleanup-pending siblings for the same parent task", () => {
    const model = buildEngineRoomModel(
      [
        node({
          worktreeGroup: "/w/r/old",
          leafId: "15_parallel-leaf-enclosure-workflow",
          phase: "cleanup-pending",
          closeoutStatus: "completed",
          integrationStatus: "completed",
        }),
        node({
          worktreeGroup: "/w/r/current",
          leafId: "16_engine-room-stack-entry-height",
          phase: "worktree-started",
        }),
      ],
      [],
    );

    expect(model.processes.map((view) => view.node.leafId)).toEqual([
      "16_engine-room-stack-entry-height",
      "15_parallel-leaf-enclosure-workflow",
    ]);
  });
});

import { describe, expect, it } from "vitest";

import type {
  EngineProcessNode,
  LifecycleProjection,
  ProviderNode,
} from "../../types/projection";
import { buildEngineRoomModel } from "./buildEngineRoomModel";

function node(over: Partial<EngineProcessNode> = {}): EngineProcessNode {
  return {
    id: "/c.md",
    enclosure: "/c.md",
    worktreeGroup: "/w/r/grp",
    taskId: "T",
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
  tokens: 0,
  startedAt: "",
  lastEventTs: "",
  inferred: false,
  actions: [],
  tokenSeries: [],
  ...over,
});

const worktreeEngine = (id: string, group: string): ProviderNode => ({
  id,
  state: "configured",
  ok: true,
  watcherUp: false,
  indexingState: "unknown",
  scope: "worktree",
  role: id.includes("memory") || id.includes("grepai") ? "memory" : "code",
  worktreeGroup: group,
});

describe("buildEngineRoomModel", () => {
  it("attaches each process to its lifecycle by lifecycleId", () => {
    const model = buildEngineRoomModel([node({ lifecycleId: "L1" })], [], [lifecycle({ id: "L1" })]);
    expect(model.processes).toHaveLength(1);
    expect(model.processes[0]?.lifecycle?.id).toBe("L1");
    expect(model.usesFallback).toBe(false);
  });

  it("exposes the lifecycle gate on the process view", () => {
    const model = buildEngineRoomModel(
      [node({ lifecycleId: "L1" })],
      [],
      [
        lifecycle({
          id: "L1",
          gate: {
            id: "G1",
            kind: "closeout-approval",
            state: "open",
            decisions: [],
            packet: { question: "land?" },
            ts: "",
          },
        }),
      ],
    );
    expect(model.processes[0]?.gate?.kind).toBe("closeout-approval");
  });

  it("lifts workspace-scoped providers into workspaceEngines", () => {
    const ws: ProviderNode = {
      id: "codegraphcontext-code",
      state: "ready",
      ok: true,
      watcherUp: true,
      indexingState: "indexed",
      scope: "workspace",
      role: "code",
    };
    const model = buildEngineRoomModel([node()], [ws], []);
    expect(model.workspaceEngines.map((provider) => provider.id)).toEqual(["codegraphcontext-code"]);
  });

  it("falls back to groupEngines when worktree providers exist but no engineProcesses", () => {
    const model = buildEngineRoomModel([], [worktreeEngine("cgc@grp", "grp")], []);
    expect(model.usesFallback).toBe(true);
    expect(model.fallbackStacks).toHaveLength(1);
    expect(model.processes).toHaveLength(0);
  });

  it("does not fall back once engineProcesses are present", () => {
    const model = buildEngineRoomModel([node()], [worktreeEngine("cgc@grp", "grp")], []);
    expect(model.usesFallback).toBe(false);
    expect(model.fallbackStacks).toHaveLength(0);
    expect(model.processes).toHaveLength(1);
  });

  it("exposes worktreeGroup as the enclosure key, stable across a fleeting→real id swap", () => {
    const fleeting = buildEngineRoomModel([node({ id: "start:demo" })], [], []);
    const real = buildEngineRoomModel([node({ id: "/series-contract.md" })], [], []);
    expect(fleeting.processes[0]?.enclosureKey).toBe("/w/r/grp");
    expect(real.processes[0]?.enclosureKey).toBe("/w/r/grp");
    expect(fleeting.processes[0]?.enclosureKey).toBe(real.processes[0]?.enclosureKey);
  });
});

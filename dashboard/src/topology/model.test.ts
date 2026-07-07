import { describe, expect, it } from "vitest";

import type { EnclosureNode, LifecycleProjection, ProviderNode } from "../types/projection";
import { activeTopologyInputs, buildTopology } from "./model";

const enclosure = (overrides: Partial<EnclosureNode> = {}): EnclosureNode => ({
  enclosure: "/tasks/demo/enclosures/demo/series-contract.md",
  enclosureId: "/tasks/demo/enclosures/demo/series-contract.md",
  leafId: "demo",
  taskRoot: "/tasks/demo",
  taskId: "DEMO",
  taskName: "demo",
  repoName: "agents-remember",
  lifecycleId: "LC1",
  worktreeGroup: "/worktrees/agents-remember/demo-ar",
  humanReviewStatus: "pending-review",
  closeoutStatus: "not-started",
  integrationStatus: "not-started",
  cleanup: "pending",
  codeWorktreeExists: true,
  memoryWorktreeExists: true,
  actions: [],
  ...overrides,
});

const provider = (overrides: Partial<ProviderNode> = {}): ProviderNode => ({
  id: "codegraphcontext-code@demo",
  state: "configured",
  ok: true,
  watcherUp: true,
  indexingState: "indexed",
  scope: "worktree",
  role: "code",
  ...overrides,
});

const lifecycle = (overrides: Partial<LifecycleProjection> = {}): LifecycleProjection => ({
  id: "LC1",
  state: "running",
  phase: "build",
  fleeting: false,
  tokens: 0,
  startedAt: "2026-06-28T00:00",
  lastEventTs: "2026-06-28T00:00",
  inferred: false,
  actions: [],
  tokenSeries: [],
  ...overrides,
});

describe("activeTopologyInputs", () => {
  it("keeps only active-group enclosures and drops orphan/terminal lifecycles", () => {
    const live = enclosure({
      enclosure: "/tasks/demo/enclosures/live/series-contract.md",
      worktreeGroup: "/worktrees/agents-remember/live-ar",
    });
    const dead = enclosure({
      enclosure: "/tasks/demo/enclosures/dead/series-contract.md",
      worktreeGroup: "/worktrees/agents-remember/dead-ar",
    });
    const liveLc = lifecycle({ id: "LIVE", enclosure: live.enclosure });
    const deadLc = lifecycle({ id: "DEAD", enclosure: dead.enclosure });
    const orphanLc = lifecycle({ id: "ORPHAN", enclosure: undefined });

    // activeWorktreeGroups is a basename set; EnclosureNode.worktreeGroup is a full path.
    const out = activeTopologyInputs([liveLc, deadLc, orphanLc], [live, dead], ["live-ar"]);

    expect(out.enclosures).toEqual([live]);
    expect(out.lifecycles).toEqual([liveLc]);
  });
});

describe("buildTopology", () => {
  it("folds the bound lifecycle into the enclosure node and emits no task ring", () => {
    const owner = enclosure();
    const lc = lifecycle({ id: "LCX", enclosure: owner.enclosure, phase: "build", state: "blocked" });
    const nodes = buildTopology([lc], [owner], []);

    const wt = nodes.find((node) => node.kind === "wt" && node.label === owner.taskName);
    expect(wt).toBeDefined();
    expect(wt?.id).toBe("LCX"); // click-through now lives on the enclosure node
    expect(wt?.status).toBe("crit"); // lifecycleStatus(blocked)
    expect(wt?.sub).toBe("build · blocked");
    expect(nodes.some((node) => (node.kind as string) === "task")).toBe(false);
  });

  it("joins a worktree provider to its enclosure when worktreeGroup formats differ (path vs basename)", () => {
    const owner = enclosure({ worktreeGroup: "/worktrees/agents-remember/demo-ar" });
    // Real served data: the worktree ProviderNode.worktreeGroup is a basename, the enclosure's a full path.
    const nodes = buildTopology([], [owner], [provider({ worktreeGroup: "demo-ar" })]);

    const worktreeIndex = nodes.findIndex((node) => node.kind === "wt" && node.label === owner.taskName);
    const providerNode = nodes.find((node) => node.kind === "prov");

    expect(worktreeIndex).toBeGreaterThan(-1);
    expect(providerNode?.parent).toBe(worktreeIndex);
  });

  it("parents worktree-scoped providers to their owning worktree node", () => {
    const owner = enclosure();
    const nodes = buildTopology([], [owner], [provider({ worktreeGroup: owner.worktreeGroup })]);

    const worktreeIndex = nodes.findIndex((node) => node.kind === "wt" && node.label === owner.taskName);
    const providerNode = nodes.find((node) => node.kind === "prov" && node.label === "codegraphcontext-code@demo");

    expect(worktreeIndex).toBeGreaterThan(-1);
    expect(providerNode?.parent).toBe(worktreeIndex);
  });

  it("falls back to the workspace core when a worktree provider has no matching group", () => {
    const nodes = buildTopology([], [enclosure()], [provider({ worktreeGroup: "/missing/group" })]);
    const workspaceIndex = nodes.findIndex((node) => node.kind === "ws");
    const providerNode = nodes.find((node) => node.kind === "prov");

    expect(providerNode?.parent).toBe(workspaceIndex);
  });

  it("keeps workspace-scoped providers parented to the workspace core", () => {
    const nodes = buildTopology(
      [],
      [enclosure()],
      [provider({ id: "grepai-memory", scope: "workspace", worktreeGroup: undefined })],
    );
    const workspaceIndex = nodes.findIndex((node) => node.kind === "ws");
    const providerNode = nodes.find((node) => node.kind === "prov");

    expect(providerNode?.parent).toBe(workspaceIndex);
  });

  it("parents repo-scoped workspace providers to their covered repo node", () => {
    const nodes = buildTopology(
      [],
      [],
      [
        provider({
          id: "codegraphcontext-code:repo-b",
          scope: "workspace",
          repoId: "repo-b",
          worktreeGroup: undefined,
        }),
      ],
    );
    const repoIndex = nodes.findIndex((node) => node.kind === "repo" && node.label === "repo-b");
    const providerNode = nodes.find((node) => node.kind === "prov");

    expect(repoIndex).toBeGreaterThan(-1);
    expect(providerNode?.parent).toBe(repoIndex);
  });

  it("keeps worktreeGroup precedence over repoId for provider parenting", () => {
    const owner = enclosure();
    const nodes = buildTopology(
      [],
      [owner],
      [
        provider({
          worktreeGroup: owner.worktreeGroup,
          repoId: "repo-b",
        }),
      ],
    );
    const worktreeIndex = nodes.findIndex((node) => node.kind === "wt" && node.label === owner.taskName);
    const providerNode = nodes.find((node) => node.kind === "prov");

    expect(providerNode?.parent).toBe(worktreeIndex);
  });
});

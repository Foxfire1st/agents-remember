import { describe, expect, it } from "vitest";

import type { EnclosureNode, ProviderNode } from "../types/projection";
import { buildTopology } from "./model";

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

describe("buildTopology", () => {
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

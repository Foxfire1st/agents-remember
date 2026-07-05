import { describe, expect, it } from "vitest";

import type { DashboardState } from "./store";
import type { AttentionItem, LifecycleProjection, Phase } from "../types/projection";
import { buildTree, fmtWait, hasLiveWorktree, selectQueue } from "./selectors";

const lifecycle = (id: string, repoId?: string, phase: Phase = "build"): LifecycleProjection => {
  const base: LifecycleProjection = {
    id,
    state: "running",
    phase,
    fleeting: false,
    tokens: 0,
    startedAt: "t",
    lastEventTs: "t",
    inferred: false,
    actions: [],
    tokenSeries: [],
  };
  return repoId === undefined ? base : { ...base, repoId };
};

describe("buildTree", () => {
  it("BY PHASE groups by l-01 phase in pipeline order, members id-sorted", () => {
    const tree = buildTree(
      [
        lifecycle("LC2", undefined, "build"),
        lifecycle("LC1", undefined, "build"),
        lifecycle("LC3", undefined, "request"),
      ],
      "phase",
    );
    expect(tree.map((g) => g.key)).toEqual(["request", "build"]); // pipeline order, not alpha
    expect(tree.find((g) => g.key === "build")?.lifecycles.map((l) => l.id)).toEqual(["LC1", "LC2"]);
  });

  it("BY REPO groups by repoId, sorted, with members id-sorted", () => {
    const tree = buildTree(
      [lifecycle("LC3", "repo-b"), lifecycle("LC2", "repo-a"), lifecycle("LC1", "repo-a")],
      "repo",
    );
    expect(tree.map((g) => g.key)).toEqual(["repo-a", "repo-b"]);
    expect(tree[0].lifecycles.map((l) => l.id)).toEqual(["LC1", "LC2"]);
  });

  it("BY REPO buckets a lifecycle with no repoId under (unassigned)", () => {
    const tree = buildTree([lifecycle("LC1")], "repo");
    expect(tree[0].key).toBe("(unassigned)");
  });
});

describe("hasLiveWorktree", () => {
  it("is the stat'ed existence truth (L11): either physical worktree admits, neither hides", () => {
    // Never a cleanup-state proxy — the flags alone decide tasks-surface visibility.
    expect(hasLiveWorktree({ codeWorktreeExists: true, memoryWorktreeExists: true })).toBe(true);
    expect(hasLiveWorktree({ codeWorktreeExists: true, memoryWorktreeExists: false })).toBe(true);
    expect(hasLiveWorktree({ codeWorktreeExists: false, memoryWorktreeExists: true })).toBe(true);
    expect(hasLiveWorktree({ codeWorktreeExists: false, memoryWorktreeExists: false })).toBe(false);
  });
});

describe("fmtWait", () => {
  it("scales seconds → s/m/h/d and renders unknown as a dash", () => {
    expect(fmtWait(45)).toBe("45s");
    expect(fmtWait(120)).toBe("2m");
    expect(fmtWait(7200)).toBe("2h");
    expect(fmtWait(172800)).toBe("2d");
    expect(fmtWait(undefined)).toBe("—");
  });
});

describe("selectQueue", () => {
  it("reads the server-computed queue, empty when analytics is absent", () => {
    const item: AttentionItem = {
      id: "blocked-gate:LC1",
      kind: "blocked-gate",
      severity: "warn",
      lane: "lifecycle",
      title: "Gate — input needed",
    };
    expect(
      selectQueue({
        analytics: { attentionQueue: [item] },
        suppressedAttentionIds: {},
      } as DashboardState),
    ).toEqual([item]);
    expect(selectQueue({ analytics: null, suppressedAttentionIds: {} } as DashboardState)).toEqual([]);
  });

  it("hides optimistically suppressed attention ids", () => {
    const item: AttentionItem = {
      id: "actionable-drift:repo:main",
      kind: "actionable-drift",
      severity: "warn",
      lane: "repo",
      title: "1 actionable drift in repo",
    };
    expect(
      selectQueue({
        analytics: { attentionQueue: [item] },
        suppressedAttentionIds: { [item.id]: true },
      } as DashboardState),
    ).toEqual([]);
  });
});

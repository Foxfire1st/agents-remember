import { describe, expect, it } from "vitest";

import type { DashboardState } from "./store";
import type { AttentionItem, LifecycleProjection } from "../types/projection";
import { buildTree, fmtWait, selectQueue } from "./selectors";

const lifecycle = (id: string, repoId?: string): LifecycleProjection => {
  const base: LifecycleProjection = {
    id,
    state: "running",
    phase: "build",
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
  it("BY LIFECYCLE yields one id-sorted group per lifecycle", () => {
    const tree = buildTree([lifecycle("LC2"), lifecycle("LC1")], "lifecycle");
    expect(tree.map((g) => g.key)).toEqual(["LC1", "LC2"]);
    expect(tree.every((g) => g.lifecycles.length === 1)).toBe(true);
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
    expect(selectQueue({ analytics: { attentionQueue: [item] } } as DashboardState)).toEqual([item]);
    expect(selectQueue({ analytics: null } as DashboardState)).toEqual([]);
  });
});

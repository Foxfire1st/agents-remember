// The coordination-topology model (mc2 harvest #4): a deterministic radial tree derived from
// the projection — workspace core → repos (inner ring) → worktrees (middle ring) → lifecycles
// (rim), with provider satellites orbiting their scoped parent. Pure + deterministic
// (id-sorted) so the render is stable across snapshots and unit-testable without a canvas.

import { engineState } from "../data/selectors";
import type { EnclosureNode, LifecycleProjection, ProviderNode } from "../types/projection";

export type ConstelStatus = "core" | "ok" | "warn" | "crit" | "idle";

export interface ConstelNode {
  kind: "ws" | "repo" | "wt" | "task" | "prov";
  parent: number; // index into the node list, -1 for the workspace core
  rf: number; // ring fraction from the centre (0 for ws/prov)
  ang: number; // angle in radians (0 for prov — they orbit by poff)
  poff: number; // provider orbit offset
  base: number; // node radius
  status: ConstelStatus;
  label: string;
  sub: string;
  id: string | null; // lifecycle id → click-through to Operations (null when not selectable)
  px: number; // screen position, filled by the renderer's layout()
  py: number;
}

export const RF = { repo: 0.26, wt: 0.52, task: 0.76 };

const TAU = Math.PI * 2;
const RANK: Record<ConstelStatus, number> = { crit: 3, warn: 2, ok: 1, idle: 0, core: 0 };

function lifecycleStatus(lifecycle: LifecycleProjection): ConstelStatus {
  if (lifecycle.state === "blocked") return "crit";
  if (lifecycle.state === "abandoned") return "idle";
  if (lifecycle.state === "paused" || lifecycle.inferred) return "warn";
  return "ok"; // running / completed
}

export function buildTopology(
  lifecycles: LifecycleProjection[],
  enclosures: EnclosureNode[],
  providers: ProviderNode[],
): ConstelNode[] {
  const nodes: ConstelNode[] = [];
  const add = (node: Omit<ConstelNode, "px" | "py">): number => {
    nodes.push({ ...node, px: 0, py: 0 });
    return nodes.length - 1;
  };

  const repoKeys = [
    ...new Set([
      ...enclosures.map((e) => e.repoName),
      ...lifecycles.map((l) => l.repoId).filter((r): r is string => Boolean(r)),
    ]),
  ].sort();

  const ws = add({
    kind: "ws",
    parent: -1,
    rf: 0,
    ang: 0,
    poff: 0,
    base: 7,
    status: "core",
    label: "WORKSPACE",
    sub: `${repoKeys.length} repos · ${enclosures.length} worktrees · ${lifecycles.length} lifecycles`,
    id: null,
  });

  const repoIdx = new Map<string, number>();
  const nRepos = Math.max(repoKeys.length, 1);
  repoKeys.forEach((repo, ri) => {
    const ang = (ri / nRepos) * TAU;
    repoIdx.set(repo, add({ kind: "repo", parent: ws, rf: RF.repo, ang, poff: 0, base: 4.6, status: "ok", label: repo, sub: "repo", id: null }));
  });

  // Worktrees (enclosures) spread within their repo's angular span.
  const wtIdxByEnclosure = new Map<string, number>();
  const wtIdxByGroup = new Map<string, number>();
  const span = (TAU / nRepos) * 0.74;
  const enclByRepo = new Map<string, EnclosureNode[]>();
  for (const enclosure of [...enclosures].sort((a, b) => a.enclosure.localeCompare(b.enclosure))) {
    const list = enclByRepo.get(enclosure.repoName) ?? [];
    list.push(enclosure);
    enclByRepo.set(enclosure.repoName, list);
  }
  for (const [repo, encls] of enclByRepo) {
    const parent = repoIdx.get(repo);
    if (parent == null) continue;
    const repoAng = nodes[parent].ang;
    encls.forEach((enclosure, wi) => {
      const a = repoAng + (encls.length > 1 ? (wi / (encls.length - 1) - 0.5) * span : 0);
      const status: ConstelStatus = enclosure.cleanup === "pending" ? "warn" : "ok";
      const wtIdx = add({ kind: "wt", parent, rf: RF.wt, ang: a, poff: 0, base: 3.1, status, label: enclosure.taskName || enclosure.enclosure, sub: `worktree · ${enclosure.cleanup} cleanup`, id: null });
      wtIdxByEnclosure.set(enclosure.enclosure, wtIdx);
      wtIdxByGroup.set(enclosure.worktreeGroup, wtIdx);
    });
  }

  // Lifecycles (tasks) at the rim: under their enclosure if known, else their repo, else the core.
  for (const lifecycle of [...lifecycles].sort((a, b) => a.id.localeCompare(b.id))) {
    let parent = ws;
    if (lifecycle.enclosure && wtIdxByEnclosure.has(lifecycle.enclosure)) {
      parent = wtIdxByEnclosure.get(lifecycle.enclosure) ?? ws;
    } else if (lifecycle.repoId && repoIdx.has(lifecycle.repoId)) {
      parent = repoIdx.get(lifecycle.repoId) ?? ws;
    }
    const status = lifecycleStatus(lifecycle);
    add({ kind: "task", parent, rf: RF.task, ang: nodes[parent].ang, poff: 0, base: 2.7, status, label: lifecycle.id, sub: `${lifecycle.phase} · ${lifecycle.state}`, id: lifecycle.id });
    if (nodes[parent].kind === "wt" && RANK[status] > RANK[nodes[parent].status]) {
      nodes[parent].status = status;
    }
  }

  // Provider satellites orbit their scoped parent: worktree providers attach to their enclosure;
  // workspace providers stay on the core until the backend emits per-repo coverage.
  providers.forEach((provider, pi) => {
    const engine = engineState(provider);
    const status: ConstelStatus = engine === "down" ? "crit" : engine === "indexing" ? "idle" : "ok";
    const parent = provider.worktreeGroup ? (wtIdxByGroup.get(provider.worktreeGroup) ?? ws) : ws;
    add({ kind: "prov", parent, rf: 0, ang: 0, poff: (pi / Math.max(providers.length, 1)) * TAU, base: 1.5, status, label: provider.id, sub: `provider · ${provider.state}`, id: null });
  });

  return nodes;
}

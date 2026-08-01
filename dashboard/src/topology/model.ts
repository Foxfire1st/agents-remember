// The coordination-topology model (mc2 harvest #4): a deterministic radial tree derived from
// the projection — workspace core → source checkouts (inner ring) → active worktree enclosures
// (outer ring), with provider satellites orbiting their scoped parent. The view is bounded to
// ACTIVE work: the caller passes only enclosures whose worktree group is live (see
// `activeTopologyInputs`), and each enclosure node folds in its 1:1 lifecycle (id/status/phase)
// — there is no separate lifecycle/task rim. Pure + deterministic (id-sorted) so the render is
// stable across snapshots and unit-testable without a canvas.

import { engineState } from "../data/selectors";
import type { EnclosureNode, LifecycleProjection, ProviderNode, State } from "../types/projection";

// The constellation's own colour-bearing vocabulary, declared as a tuple with the type derived —
// so every table keyed by it (`constel.ts`'s palette) and every test that iterates it reads ONE
// list. A hand-written union here is a list that only the type system can see, and a table that
// only the type system can see is a table no runtime assertion can prove total.
export const CONSTEL_STATUSES = ["core", "ok", "warn", "crit", "idle"] as const;

export type ConstelStatus = (typeof CONSTEL_STATUSES)[number];

export interface ConstelNode {
  kind: "ws" | "repo" | "wt" | "prov";
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

export const RF = { repo: 0.3, wt: 0.62 };

const TAU = Math.PI * 2;

// The state → constellation-status grammar, declared ONCE and totally. `Record<State, …>` is
// the load-bearing part: a seventh state in `LIFECYCLE_STATES` makes this object literal stop
// compiling until someone decides what it looks like. What it replaces was an if-chain ending
// in `return "ok"`, which covered five of the six states and answered "healthy" for the sixth —
// and a default that says "healthy" is not a default, it is a claim. That is how a lifecycle
// which had handed the turn back drew as a green node on the one surface a developer scans for
// work waiting on them, and why `model.test.ts` passed a seventh-state mutation that
// `Dot.test.tsx` caught: `Dot` derives its known-set from the cva recipe (one declaration) and
// its test iterates the vocabulary. This is the same move in this module's vocabulary.
export const CONSTEL_STATUS_BY_STATE: Record<State, ConstelStatus> = {
  blocked: "crit", // a fault — the lifecycle cannot proceed without intervention
  // The turn is back with the developer (NOTIFY-AND-CONTINUE turn end): actionable but NOT a
  // fault, so the same `warn` treatment as paused — never `crit` (nothing is broken), never
  // `ok` (something is waiting on you). The colour grammar rules the same way (`Dot.tsx` gives
  // it amber, the palette's one "wants you, is not broken" hue).
  "awaiting-developer": "warn",
  paused: "warn", // parked, and the reducer INFERS this from a stale heartbeat
  abandoned: "idle", // over, and over without finishing
  running: "ok",
  completed: "ok",
};

// A state this build has never heard of — a newer server, or a persisted projection from one.
// It must not be `ok`: `ok` is the exact wrong answer, the one that made the handed-back
// lifecycle read as healthy, and an unrecognised state is the case where this build knows
// LEAST. `warn` is the honest register in the vocabulary this model has ("look at this"), and
// it is `Dot.tsx`'s ruling for the same situation — an unrecognised variant renders the
// `muted` "we could not classify this" tone rather than borrowing a live state's colour.
// `crit` is not it either: that would claim a fault the server never reported.
export const UNCLASSIFIED_STATUS: ConstelStatus = "warn";

// The table READ through a view that admits what the wire actually allows. `lifecycle.state` is
// typed `State`, but that type is a CLAIM about the server, not a guarantee — the value arrives as
// JSON from a process this build does not control. Indexing `Record<State, …>` types the miss
// away: TypeScript hands back `ConstelStatus`, never `undefined`, so `?? UNCLASSIFIED_STATUS`
// below reads as dead code that anyone may delete for zero new `tsc` errors — and the `undefined`
// it was catching flows on to `constel.ts`, whose palette used to answer `?? COLORS.ok` and paint
// the cyan healthy fill. That is the original defect, restorable end-to-end with both gates green.
// This alias is what makes the fallback load-bearing to the compiler: the miss is typed, so
// removing the `??` fails `tsc -b` here rather than failing a developer at a glance.
//
// `noUncheckedIndexedAccess` would say the same thing for every index in the project. It is not on
// (measured: 601 errors across 81 files, 33 of them non-test), so this says it at the one seam
// that matters — where the index key is wire data rather than a value this module produced.
const STATUS_BY_DECLARED_STATE: Partial<Record<string, ConstelStatus>> = CONSTEL_STATUS_BY_STATE;

export function lifecycleStatus(
  lifecycle: Pick<LifecycleProjection, "state" | "inferred">,
): ConstelStatus {
  const declared = STATUS_BY_DECLARED_STATE[lifecycle.state] ?? UNCLASSIFIED_STATUS;
  // `inferred` degrades a HEALTHY reading only: the reducer derived this state (stale heartbeat
  // → paused, dormant fleeting → abandoned) instead of reading a written transition, so it may
  // not be drawn with the confidence of an observed one. It never upgrades warn/crit/idle.
  return declared === "ok" && lifecycle.inferred ? "warn" : declared;
}

// Worktree groups join across the projection by BASENAME: the worktree-scoped
// `ProviderNode.worktreeGroup` and `activeWorktreeGroups` are basenames, while
// `EnclosureNode.worktreeGroup` is a full path. Normalising both sides here keeps the
// enclosure↔provider and enclosure↔active-set joins correct on real served data.
const groupKey = (group: string): string => group.split("/").pop() ?? group;

// Bound the topology inputs to active work: keep only enclosures whose worktree group is in
// `activeWorktreeGroups` (the server's active-enclosure admission), then keep only the
// lifecycles bound to one of those surviving enclosures. The shared store collections retain
// all-time history for other views; this is the pure seam that scopes the constellation.
export function activeTopologyInputs(
  lifecycles: LifecycleProjection[],
  enclosures: EnclosureNode[],
  activeWorktreeGroups: string[],
): { lifecycles: LifecycleProjection[]; enclosures: EnclosureNode[] } {
  const active = new Set(activeWorktreeGroups.map(groupKey));
  const liveEnclosures = enclosures.filter((e) => active.has(groupKey(e.worktreeGroup)));
  const liveKeys = new Set(liveEnclosures.map((e) => e.enclosure));
  const liveLifecycles = lifecycles.filter((l) => l.enclosure != null && liveKeys.has(l.enclosure));
  return { lifecycles: liveLifecycles, enclosures: liveEnclosures };
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

  // Source checkouts (repo ring): every repo with an active enclosure, plus repos a
  // non-worktree provider covers (the official line still shows even with no active worktree).
  // Lifecycles no longer contribute repo keys of their own — they live on their enclosure.
  const repoKeys = [
    ...new Set([
      ...enclosures.map((e) => e.repoName),
      ...providers
        .filter((provider) => provider.scope !== "worktree")
        .map((provider) => provider.repoId)
        .filter((repo): repo is string => Boolean(repo)),
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
    sub: `${repoKeys.length} checkouts · ${enclosures.length} active worktrees`,
    id: null,
  });

  const repoIdx = new Map<string, number>();
  const nRepos = Math.max(repoKeys.length, 1);
  repoKeys.forEach((repo, ri) => {
    const ang = (ri / nRepos) * TAU;
    repoIdx.set(repo, add({ kind: "repo", parent: ws, rf: RF.repo, ang, poff: 0, base: 4.6, status: "ok", label: repo, sub: "checkout", id: null }));
  });

  // Each active enclosure binds 1:1 to its lifecycle (EnclosureNode.lifecycleId ↔
  // LifecycleProjection.enclosure), so the enclosure node IS the selectable leaf — it folds in
  // the lifecycle's id (click-through), status, and phase·state. No separate task rim.
  const lcByEnclosure = new Map<string, LifecycleProjection>();
  for (const lifecycle of lifecycles) {
    if (lifecycle.enclosure) lcByEnclosure.set(lifecycle.enclosure, lifecycle);
  }

  // Active worktree enclosures spread within their checkout's angular span.
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
      const lifecycle = lcByEnclosure.get(enclosure.enclosure);
      const status: ConstelStatus = lifecycle
        ? lifecycleStatus(lifecycle)
        : enclosure.cleanup === "pending"
          ? "warn"
          : "ok";
      const wtIdx = add({
        kind: "wt",
        parent,
        rf: RF.wt,
        ang: a,
        poff: 0,
        base: 3.1,
        status,
        label: enclosure.taskName || enclosure.enclosure,
        sub: lifecycle ? `${lifecycle.phase} · ${lifecycle.state}` : `worktree · ${enclosure.cleanup} cleanup`,
        id: lifecycle?.id ?? null,
      });
      wtIdxByGroup.set(groupKey(enclosure.worktreeGroup), wtIdx);
    });
  }

  // Provider satellites orbit their scoped parent: worktree providers attach to their enclosure
  // (joined by worktree-group basename); repo-covered workspace providers attach to their source
  // checkout; aggregate workspace providers (no repoId) stay on the core.
  providers.forEach((provider, pi) => {
    const engine = engineState(provider);
    const status: ConstelStatus = engine === "down" ? "crit" : engine === "indexing" ? "idle" : "ok";
    const parent = provider.worktreeGroup
      ? (wtIdxByGroup.get(groupKey(provider.worktreeGroup)) ?? ws)
      : provider.repoId
        ? (repoIdx.get(provider.repoId) ?? ws)
        : ws;
    add({ kind: "prov", parent, rf: 0, ang: 0, poff: (pi / Math.max(providers.length, 1)) * TAU, base: 1.5, status, label: provider.id, sub: `provider · ${provider.state}`, id: null });
  });

  return nodes;
}

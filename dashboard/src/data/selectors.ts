// Pure derivations over the store — unit-testable without React or a live stream.
// The attention queue is computed server-side (reducer); the client only reads it. The
// operation tree is a client-side pivot over the flat lifecycle collection (the projection's
// "linked flat collections" design). Wait-time is server-anchored everywhere — these helpers
// only format it, never compute it from the clock.

import type {
  AttentionItem,
  DriftSnapshotNode,
  LifecycleProjection,
  Phase,
  ProviderNode,
} from "../types/projection";
import type { DashboardState } from "./store";

// Stable empty reference: a fresh `[]` each call would make useSyncExternalStore (Zustand's
// useStore) see a new snapshot every render and loop ("getSnapshot should be cached").
const EMPTY_QUEUE: readonly AttentionItem[] = [];

export const selectQueue = (state: DashboardState): readonly AttentionItem[] =>
  state.analytics?.attentionQueue ?? EMPTY_QUEUE;

export type Pivot = "repo" | "phase";

export interface TreeGroup {
  key: string;
  label: string;
  lifecycles: LifecycleProjection[];
}

// The l-01 phase pipeline order — phase groups render in this order (not alphabetical) so the
// tree reads top→bottom as the lifecycle pipeline.
const PHASE_ORDER: Phase[] = [
  "request",
  "trust-checkpoint",
  "reframe-research",
  "decide",
  "build",
  "close",
];

/**
 * The two-axis operation tree (note 06): group the flat lifecycle collection BY REPO or BY PHASE.
 * BY PHASE buckets lifecycles under their l-01 phase (mc2's "lifecycle" axis) — meaningful because
 * many lifecycles share a phase, unlike grouping by id (one item per group). Deterministic order
 * (phases in pipeline order, repos sorted; members id-sorted) so the tree is stable across snapshots.
 */
export function buildTree(lifecycles: LifecycleProjection[], pivot: Pivot): TreeGroup[] {
  if (pivot === "phase") {
    const byPhase = new Map<string, LifecycleProjection[]>();
    for (const lifecycle of lifecycles) {
      const group = byPhase.get(lifecycle.phase);
      if (group) group.push(lifecycle);
      else byPhase.set(lifecycle.phase, [lifecycle]);
    }
    const known: string[] = [...PHASE_ORDER];
    const extras = [...byPhase.keys()].filter((phase) => !known.includes(phase)).sort();
    return [...PHASE_ORDER, ...extras]
      .filter((phase) => byPhase.has(phase))
      .map((phase) => ({
        key: phase,
        label: phase,
        lifecycles: (byPhase.get(phase) ?? []).sort((a, b) => a.id.localeCompare(b.id)),
      }));
  }
  const byRepo = new Map<string, LifecycleProjection[]>();
  for (const lifecycle of lifecycles) {
    const key = lifecycle.repoId ?? "(unassigned)";
    const group = byRepo.get(key);
    if (group) group.push(lifecycle);
    else byRepo.set(key, [lifecycle]);
  }
  return [...byRepo.entries()]
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([key, members]) => ({
      key,
      label: key,
      lifecycles: members.sort((a, b) => a.id.localeCompare(b.id)),
    }));
}

/** Format a server-computed age. Input is `waitSeconds` / `staleSeconds`, never `Date.now()`. */
export function fmtWait(seconds: number | undefined | null): string {
  if (seconds == null) return "—";
  if (seconds < 60) return `${Math.round(seconds)}s`;
  if (seconds < 3600) return `${Math.round(seconds / 60)}m`;
  if (seconds < 86400) return `${Math.round(seconds / 3600)}h`;
  return `${Math.round(seconds / 86400)}d`;
}

// ── Engine room (note 08: two engines per pod = CGC + GrepAI) ──────────────────
// State carried by colour + silhouette: nominal (amber) / indexing (cyan fill) / down (red).
export type EngineState = "nominal" | "indexing" | "down";

const DOWN_STATES = new Set(["stopped", "failed", "error", "down", "unreachable"]);
const INDEXING_STATES = new Set(["indexing", "indexing-pending", "reindexing", "seeding"]);

export function engineState(provider: ProviderNode): EngineState {
  if (provider.ok === false || DOWN_STATES.has(provider.state)) return "down";
  if (INDEXING_STATES.has(provider.indexingState)) return "indexing";
  return "nominal";
}

// Group providers into stacks: the workspace stack + one per worktree (its isolated CGC+GrepAI),
// so the engine room shows that each worktree spawns its own engines bound to its repos (note 08's
// twin-engine pod), not just main's. Code engine (CGC) before memory engine (GrepAI) within a stack.
export interface EngineStack {
  key: string;
  scope: "workspace" | "worktree";
  repoId: string | null;
  engines: ProviderNode[];
}

const ROLE_ORDER: Record<string, number> = { code: 0, memory: 1 };

function sortEngines(list: ProviderNode[]): ProviderNode[] {
  return [...list].sort(
    (a, b) => (ROLE_ORDER[a.role ?? ""] ?? 9) - (ROLE_ORDER[b.role ?? ""] ?? 9) || a.id.localeCompare(b.id),
  );
}

export function groupEngines(providers: ProviderNode[]): EngineStack[] {
  const workspace = providers.filter((p) => p.scope !== "worktree");
  const byGroup = new Map<string, ProviderNode[]>();
  for (const provider of providers) {
    if (provider.scope !== "worktree") continue;
    const key = provider.worktreeGroup ?? "(worktree)";
    const list = byGroup.get(key);
    if (list) list.push(provider);
    else byGroup.set(key, [provider]);
  }
  const stacks: EngineStack[] = [];
  if (workspace.length > 0) {
    stacks.push({ key: "workspace", scope: "workspace", repoId: null, engines: sortEngines(workspace) });
  }
  for (const [key, engines] of [...byGroup.entries()].sort(([a], [b]) => a.localeCompare(b))) {
    stacks.push({ key, scope: "worktree", repoId: engines[0]?.repoId ?? null, engines: sortEngines(engines) });
  }
  return stacks;
}

// ── Memory mirror (the MEM segmented bar, mc2 harvest #2; maps onto drift_check) ──
export interface DriftSegment {
  cls: string;
  count: number;
  pct: number;
}

// Healthy classifications first so the bar reads left→right from good to actionable; any
// classification the snapshot reports but we didn't anticipate is appended (forward-compatible).
const DRIFT_ORDER = ["current", "drifted", "missingVerification", "missing", "orphaned", "unsupported"];

export function driftSegments(snapshot: DriftSnapshotNode | undefined | null): DriftSegment[] {
  const counts = snapshot?.counts ?? {};
  const total = Object.values(counts).reduce((sum, n) => sum + n, 0);
  if (total === 0) return [];
  const ordered = [...new Set([...DRIFT_ORDER, ...Object.keys(counts)])];
  return ordered
    .filter((cls) => (counts[cls] ?? 0) > 0)
    .map((cls) => ({ cls, count: counts[cls], pct: Math.round((counts[cls] / total) * 100) }));
}

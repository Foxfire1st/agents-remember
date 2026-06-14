// Pure derivations over the store — unit-testable without React or a live stream.
// The attention queue is computed server-side (reducer); the client only reads it. The
// operation tree is a client-side pivot over the flat lifecycle collection (the projection's
// "linked flat collections" design). Wait-time is server-anchored everywhere — these helpers
// only format it, never compute it from the clock.

import type { AttentionItem, LifecycleProjection } from "../types/projection";
import type { DashboardState } from "./store";

// Stable empty reference: a fresh `[]` each call would make useSyncExternalStore (Zustand's
// useStore) see a new snapshot every render and loop ("getSnapshot should be cached").
const EMPTY_QUEUE: readonly AttentionItem[] = [];

export const selectQueue = (state: DashboardState): readonly AttentionItem[] =>
  state.analytics?.attentionQueue ?? EMPTY_QUEUE;

export type Pivot = "repo" | "lifecycle";

export interface TreeGroup {
  key: string;
  label: string;
  lifecycles: LifecycleProjection[];
}

/**
 * The two-axis operation tree (note 06): group the flat lifecycle collection BY REPO or
 * BY LIFECYCLE. Deterministic order (groups + members id-sorted) so the rendered tree is
 * stable across snapshots.
 */
export function buildTree(lifecycles: LifecycleProjection[], pivot: Pivot): TreeGroup[] {
  if (pivot === "lifecycle") {
    return [...lifecycles]
      .sort((a, b) => a.id.localeCompare(b.id))
      .map((lifecycle) => ({ key: lifecycle.id, label: lifecycle.id, lifecycles: [lifecycle] }));
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

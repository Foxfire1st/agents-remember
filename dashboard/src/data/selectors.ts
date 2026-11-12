// Pure derivations over the store — unit-testable without React or a live stream.
// The attention queue is computed server-side (reducer); the client only reads it. The
// task tree and counts arrive in the served entries. Wait-time is server-anchored — these helpers
// only format it, never compute it from the clock.

import type {
  AttentionItem,
  DriftSnapshotNode,
  EnclosureNode,
  TaskEntry,
} from '../types/projection';
import type { DashboardState } from './store';

// Physical worktree visibility selects the worktree surfaces; it never admits or hides a task.
// The server observes both paths. Task entries remain reachable from their authored membership
// whether a worktree exists, has been cleaned up, or has yet to start.
export function hasLiveWorktree(
  enclosure: Pick<EnclosureNode, 'codeWorktreeExists' | 'memoryWorktreeExists'>,
): boolean {
  return enclosure.codeWorktreeExists || enclosure.memoryWorktreeExists;
}

// Stable empty reference: a fresh `[]` each call would make useSyncExternalStore (Zustand's
// useStore) see a new snapshot every render and loop ("getSnapshot should be cached").
const EMPTY_QUEUE: readonly AttentionItem[] = [];
let cachedQueueSource: readonly AttentionItem[] | null = null;
let cachedSuppressedSource: Record<string, true> | null = null;
let cachedVisibleQueue: readonly AttentionItem[] = EMPTY_QUEUE;

// The two attention kinds the server retired with the search providers: nothing publishes them,
// so nothing can dismiss them. A payload from a server older than the removal can still carry
// them, and its provider-down entry would otherwise reach the bell as an alert nobody can act on
// — the queue is the seam that keeps them off the surface.
const RETIRED_ATTENTION_KINDS: ReadonlySet<string> = new Set(['provider-down', 'failed-setup']);

export const selectQueue = (state: DashboardState): readonly AttentionItem[] => {
  const queue = state.analytics?.attentionQueue ?? EMPTY_QUEUE;
  const suppressed = state.suppressedAttentionIds;
  const suppressedAny = Object.keys(suppressed).length > 0;
  const retiredAny = queue.some((item) => RETIRED_ATTENTION_KINDS.has(item.kind));
  if (!suppressedAny && !retiredAny) return queue;
  if (queue === cachedQueueSource && suppressed === cachedSuppressedSource)
    return cachedVisibleQueue;
  cachedQueueSource = queue;
  cachedSuppressedSource = suppressed;
  // Suppression is the view's own record that the human acknowledged an occurrence. Every entry
  // is one acknowledgement per occurrence (MIK-R200 rule 7), so a suppressed id hides its row
  // whatever the entry's kind: nothing else may keep a read entry on the queue. A payload from a
  // server older than the provider retirement can still carry a retired kind; nothing publishes
  // it, so nothing can dismiss it, and this filter is the seam that keeps it off the surface.
  cachedVisibleQueue = queue.filter(
    (item) => !RETIRED_ATTENTION_KINDS.has(item.kind) && !suppressed[item.id],
  );
  return cachedVisibleQueue;
};

// The server marks a started task's activity unknown only when the whole host observation is
// unknown, so one published count answers for every row: while it is above zero, no row shows a
// works or paused figure and the header alone says that host activity is unavailable.
export const selectHostActivityUnknown = (state: DashboardState): boolean =>
  state.taskCounts.unknown > 0;

/** Format a server-computed age. Input is `waitSeconds` / `staleSeconds`, never `Date.now()`. */
export function fmtWait(seconds: number | undefined | null): string {
  if (seconds == null) return '—';
  if (seconds < 60) return `${Math.round(seconds)}s`;
  if (seconds < 3600) return `${Math.round(seconds / 60)}m`;
  if (seconds < 86400) return `${Math.round(seconds / 3600)}h`;
  return `${Math.round(seconds / 86400)}d`;
}

// ── Memory mirror (the MEM segmented bar, mc2 harvest #2; maps onto drift reporting) ──
export interface DriftSegment {
  cls: string;
  count: number;
  pct: number;
}

// Healthy classifications first so the bar reads left→right from good to actionable; any
// classification the snapshot reports but we didn't anticipate is appended (forward-compatible).
// A converted repository reports `current`, `stale` and `violation` (MIK-R44): those three
// names are ordered here so the converted bar reads the same way.
const DRIFT_ORDER = [
  'current',
  'drifted',
  'missingVerification',
  'missing',
  'orphaned',
  'unsupported',
  'stale',
  'violation',
];

export function driftSegments(snapshot: DriftSnapshotNode | undefined | null): DriftSegment[] {
  const counts = snapshot?.counts ?? {};
  const total = Object.values(counts).reduce((sum, n) => sum + n, 0);
  if (total === 0) return [];
  const ordered = [...new Set([...DRIFT_ORDER, ...Object.keys(counts)])];
  return ordered
    .filter((cls) => (counts[cls] ?? 0) > 0)
    .map((cls) => ({ cls, count: counts[cls], pct: Math.round((counts[cls] / total) * 100) }));
}

/** Flatten the already-bound worktree facts once for operational surfaces.
 * This keeps each entry attached; no consumer matches ids or document paths. */
export interface TaskWorktree {
  enclosure: EnclosureNode;
  entry?: TaskEntry;
}
let worktreeTasks: DashboardState['tasks'] | undefined;
let worktreeUnbound: DashboardState['enclosures'] | undefined;
let worktreeRows: TaskWorktree[] = [];
export function selectWorktrees(state: DashboardState): TaskWorktree[] {
  if (worktreeTasks === state.tasks && worktreeUnbound === state.enclosures) return worktreeRows;
  worktreeTasks = state.tasks;
  worktreeUnbound = state.enclosures;
  worktreeRows = Object.values(state.tasks).flatMap((entry) =>
    entry.worktrees.map((enclosure) => ({ entry, enclosure })),
  );
  worktreeRows.push(...Object.values(state.enclosures).map((enclosure) => ({ enclosure })));
  return worktreeRows;
}

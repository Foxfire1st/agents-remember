// Pure derivations over the store — unit-testable without React or a live stream.
// The attention queue is computed server-side (reducer); the client only reads it. The
// task tree and counts arrive in the served entries. Wait-time is server-anchored — these helpers
// only format it, never compute it from the clock.

import type {
  AttentionItem,
  DriftSnapshotNode,
  EnclosureNode,
  ProviderNode,
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

export const selectQueue = (state: DashboardState): readonly AttentionItem[] => {
  const queue = state.analytics?.attentionQueue ?? EMPTY_QUEUE;
  const suppressed = state.suppressedAttentionIds;
  if (Object.keys(suppressed).length === 0) return queue;
  if (queue === cachedQueueSource && suppressed === cachedSuppressedSource)
    return cachedVisibleQueue;
  cachedQueueSource = queue;
  cachedSuppressedSource = suppressed;
  cachedVisibleQueue = queue.filter((item) => !suppressed[item.id]);
  return cachedVisibleQueue;
};

/** Format a server-computed age. Input is `waitSeconds` / `staleSeconds`, never `Date.now()`. */
export function fmtWait(seconds: number | undefined | null): string {
  if (seconds == null) return '—';
  if (seconds < 60) return `${Math.round(seconds)}s`;
  if (seconds < 3600) return `${Math.round(seconds / 60)}m`;
  if (seconds < 86400) return `${Math.round(seconds / 3600)}h`;
  return `${Math.round(seconds / 86400)}d`;
}

// ── Engine room (note 08: two engines per pod = CGC + GrepAI) ──────────────────
// State carried by colour + silhouette: nominal (amber) / indexing (cyan fill) / down (red).
export type EngineState = 'nominal' | 'indexing' | 'down';

const DOWN_STATES = new Set(['stopped', 'failed', 'error', 'down', 'unreachable']);
const INDEXING_STATES = new Set(['indexing', 'indexing-pending', 'reindexing', 'seeding']);

export function engineState(provider: ProviderNode): EngineState {
  if (provider.ok === false || DOWN_STATES.has(provider.state)) return 'down';
  if (INDEXING_STATES.has(provider.indexingState)) return 'indexing';
  return 'nominal';
}

// Group providers into stacks: the workspace stack + one per worktree (its isolated CGC+GrepAI),
// so the engine room shows that each worktree spawns its own engines bound to its repos (note 08's
// twin-engine pod), not just main's. Code engine (CGC) before memory engine (GrepAI) within a stack.
export interface EngineStack {
  key: string;
  scope: 'workspace' | 'worktree';
  repoId: string | null;
  engines: ProviderNode[];
}

const ROLE_ORDER: Record<string, number> = { code: 0, memory: 1 };

function sortEngines(list: ProviderNode[]): ProviderNode[] {
  return [...list].sort(
    (a, b) =>
      (ROLE_ORDER[a.role ?? ''] ?? 9) - (ROLE_ORDER[b.role ?? ''] ?? 9) || a.id.localeCompare(b.id),
  );
}

export function groupEngines(providers: ProviderNode[]): EngineStack[] {
  const workspace = providers.filter((p) => p.scope !== 'worktree');
  const byGroup = new Map<string, ProviderNode[]>();
  for (const provider of providers) {
    if (provider.scope !== 'worktree') continue;
    const key = provider.worktreeGroup ?? '(worktree)';
    const list = byGroup.get(key);
    if (list) list.push(provider);
    else byGroup.set(key, [provider]);
  }
  const stacks: EngineStack[] = [];
  if (workspace.length > 0) {
    stacks.push({
      key: 'workspace',
      scope: 'workspace',
      repoId: null,
      engines: sortEngines(workspace),
    });
  }
  for (const [key, engines] of [...byGroup.entries()].sort(([a], [b]) => a.localeCompare(b))) {
    stacks.push({
      key,
      scope: 'worktree',
      repoId: engines[0]?.repoId ?? null,
      engines: sortEngines(engines),
    });
  }
  return stacks;
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

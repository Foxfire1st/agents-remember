import { taskEntrySelectionKey } from '../data/taskReference';
import {
  limitLines,
  limitUntil,
  statusTitle,
  statusTone,
  taskStatusKey,
} from '../panels/detail-panel/pageParts';
// The coordination-topology model (mc2 harvest #4): a deterministic radial tree derived from
// the projection — workspace core → source checkouts (inner ring) → active worktree enclosures
// (outer ring), with provider satellites orbiting their scoped parent. The view is bounded to
// ACTIVE work: the caller passes only enclosures whose worktree group is live (see
// `activeTopologyInputs`), and each enclosure reads its task entry
// for its status and task selection. Pure + deterministic (id-sorted) so the render is
// stable across snapshots and unit-testable without a canvas.

import { engineState,type TaskWorktree } from '../data/selectors';
import type { ProviderNode, TaskEntry } from '../types/projection';

// The constellation's own colour-bearing vocabulary, declared as a tuple with the type derived —
// so every table keyed by it (`constel.ts`'s palette) and every test that iterates it reads ONE
// list. A hand-written union here is a list that only the type system can see, and a table that
// only the type system can see is a table no runtime assertion can prove total.
export const CONSTEL_STATUSES = ['core', 'ok', 'warn', 'crit', 'idle'] as const;

export type ConstelStatus = (typeof CONSTEL_STATUSES)[number];

export interface ConstelNode {
  kind: 'ws' | 'repo' | 'wt' | 'prov';
  parent: number; // index into the node list, -1 for the workspace core
  rf: number; // ring fraction from the centre (0 for ws/prov)
  ang: number; // angle in radians (0 for prov — they orbit by poff)
  poff: number; // provider orbit offset
  base: number; // node radius
  status: ConstelStatus;
  label: string;
  sub: string;
  /** The whole served detail, one line per limited seat where there is one: the hover's body. */
  detail?: string;
  id: string | null; // task selection key → Operations (null when not selectable)
  px: number; // screen position, filled by the renderer's layout()
  py: number;
}

export const RF = { repo: 0.3, wt: 0.62 };

const TAU = Math.PI * 2;

// Worktree groups join across the projection by BASENAME: the worktree-scoped
// `ProviderNode.worktreeGroup` and `activeWorktreeGroups` are basenames, while
// `EnclosureNode.worktreeGroup` is a full path. Normalising both sides here keeps the
// enclosure↔provider and enclosure↔active-set joins correct on real served data.
const groupKey = (group: string): string => group.split('/').pop() ?? group;

// Bound the topology inputs to active work: keep only enclosures whose worktree group is in
// `activeWorktreeGroups` (the server's active-enclosure admission). The shared store retains
// all-time history for other views; this is the pure seam that scopes the constellation.
export function activeTopologyInputs(
  worktrees: TaskWorktree[],
  activeWorktreeGroups: string[],
): { worktrees: TaskWorktree[] } {
  const active = new Set(activeWorktreeGroups.map(groupKey));
  return {
    worktrees: worktrees.filter(({ enclosure }) => active.has(groupKey(enclosure.worktreeGroup))),
  };
}

function collectRepoKeys(worktrees: TaskWorktree[], providers: ProviderNode[]): string[] {
  // Source checkouts (repo ring): every repo with an active enclosure, plus repos a
  // non-worktree provider covers (the official line still shows even with no active worktree).
  return [
    ...new Set([
      ...worktrees.map(({ enclosure }) => enclosure.repoName),
      ...providers
        .filter((provider) => provider.scope !== 'worktree')
        .map((provider) => provider.repoId)
        .filter((repo): repo is string => Boolean(repo)),
    ]),
  ].sort();
}

function groupWorktreesByRepo(worktrees: TaskWorktree[]): Map<string, TaskWorktree[]> {
  const byRepo = new Map<string, TaskWorktree[]>();
  for (const row of [...worktrees].sort((a, b) =>
    a.enclosure.enclosure.localeCompare(b.enclosure.enclosure),
  )) {
    const list = byRepo.get(row.enclosure.repoName);
    if (list) list.push(row);
    else byRepo.set(row.enclosure.repoName, [row]);
  }
  return byRepo;
}

function worktreeStatus(row: TaskWorktree): ConstelStatus {
  if (!row.entry) return 'idle';
  const tone = statusTone(taskStatusKey(row.entry));
  return tone === 'wait' ? 'warn' : tone === 'gone' ? 'idle' : 'ok';
}

function providerParent(
  provider: ProviderNode,
  wtIdxByGroup: Map<string, number>,
  repoIdx: Map<string, number>,
  ws: number,
): number {
  return provider.worktreeGroup
    ? (wtIdxByGroup.get(groupKey(provider.worktreeGroup)) ?? ws)
    : provider.repoId
      ? (repoIdx.get(provider.repoId) ?? ws)
      : ws;
}

/** What one worktree node says: its label, its word, and the whole served detail of a limit.
 *
 * No clock on a canvas: the words, the exact end and each limited seat's provider, window and
 * reset go into the node's hover tip and its accessible text (MIK-R190 rule 13).
 */
function worktreeFields(
  entry: TaskEntry | undefined,
  fallbackLabel: string,
  cleanup: string,
): { label: string; sub: string; detail?: string; id: string | null } {
  if (!entry) {
    return { label: fallbackLabel, sub: `worktree · ${cleanup} cleanup`, id: null };
  }
  const sub = `worktree · ${statusTitle(taskStatusKey(entry), limitUntil(entry))}`;
  const limit = taskStatusKey(entry) === 'limited' ? entry.providerLimit : undefined;
  return {
    label: entry.title,
    sub,
    ...(limit ? { detail: [sub, ...limitLines(limit)].join('\n') } : {}),
    id: taskEntrySelectionKey(entry.key),
  };
}

export function buildTopology(worktrees: TaskWorktree[], providers: ProviderNode[]): ConstelNode[] {
  const nodes: ConstelNode[] = [];
  const add = (node: Omit<ConstelNode, 'px' | 'py'>): number => {
    nodes.push({ ...node, px: 0, py: 0 });
    return nodes.length - 1;
  };

  const repoKeys = collectRepoKeys(worktrees, providers);
  const ws = add({
    kind: 'ws',
    parent: -1,
    rf: 0,
    ang: 0,
    poff: 0,
    base: 7,
    status: 'core',
    label: 'WORKSPACE',
    sub: `${repoKeys.length} checkouts · ${worktrees.length} active worktrees`,
    id: null,
  });

  const repoIdx = addRepositories(repoKeys, ws, add);
  const nRepos = Math.max(repoKeys.length, 1);

  const wtIdxByGroup = new Map<string, number>();
  const span = (TAU / nRepos) * 0.74;
  const enclByRepo = groupWorktreesByRepo(worktrees);
  for (const [repo, encls] of enclByRepo) {
    const parent = repoIdx.get(repo);
    if (parent == null) continue;
    const repoAng = nodes[parent].ang;
    encls.forEach((row, wi) => {
      const { enclosure, entry } = row;
      const a = repoAng + (encls.length > 1 ? (wi / (encls.length - 1) - 0.5) * span : 0);
      const status = worktreeStatus(row);
      const wtIdx = add({
        kind: 'wt',
        parent,
        rf: RF.wt,
        ang: a,
        poff: 0,
        base: 3.1,
        status,
        ...worktreeFields(
          entry,
          enclosure.taskName || enclosure.enclosure,
          enclosure.cleanup,
        ),
      });
      wtIdxByGroup.set(groupKey(enclosure.worktreeGroup), wtIdx);
    });
  }

  // Provider satellites orbit their scoped parent: worktree providers attach to their enclosure
  // (joined by worktree-group basename); repo-covered workspace providers attach to their source
  // checkout; aggregate workspace providers (no repoId) stay on the core.
  providers.forEach((provider, pi) => {
    const engine = engineState(provider);
    const status: ConstelStatus =
      engine === 'down' ? 'crit' : engine === 'indexing' ? 'idle' : 'ok';
    const parent = providerParent(provider, wtIdxByGroup, repoIdx, ws);
    add({
      kind: 'prov',
      parent,
      rf: 0,
      ang: 0,
      poff: (pi / Math.max(providers.length, 1)) * TAU,
      base: 1.5,
      status,
      label: provider.id,
      sub: `provider · ${provider.state}`,
      id: null,
    });
  });

  return nodes;
}

function addRepositories(
  repoKeys: string[],
  ws: number,
  add: (node: Omit<ConstelNode, 'px' | 'py'>) => number,
): Map<string, number> {
  const repoIdx = new Map<string, number>();
  const nRepos = Math.max(repoKeys.length, 1);
  repoKeys.forEach((repo, ri) => {
    const ang = (ri / nRepos) * TAU;
    repoIdx.set(
      repo,
      add({
        kind: 'repo',
        parent: ws,
        rf: RF.repo,
        ang,
        poff: 0,
        base: 4.6,
        status: 'ok',
        label: repo,
        sub: 'checkout',
        id: null,
      }),
    );
  });
  return repoIdx;
}

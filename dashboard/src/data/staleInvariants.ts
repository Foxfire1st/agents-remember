// Which invariants a leaf's move changed, and their stale marks on the review page (D576).
//
// On a move from the comparison the page shows to a newer one, the page asks
// `/api/review/trees/changes` once. The server names the invariants that differ, by invariant key,
// or answers `unavailable`; unavailable never means "nothing changed", and the page never computes
// "differs" itself. The named invariants are marked stale until their detail at the newer
// comparison has loaded: the tree rows, the open card and any review-side count read this one set,
// by invariant key, never by row position. The Explanation's stale parts (`/stale`) are another
// thing and are not counted here.
import { useSyncExternalStore } from 'react';

import { qs } from './files';
import { getReviewJson } from './reviewTransport';

export type ReviewInvariantChanges =
  | { state: 'named'; from_comparison: number; to_comparison: number; invariants: string[] }
  | { state: 'unavailable'; reason: string };

interface Marks {
  // The comparison whose detail clears a mark.
  to: number;
  keys: ReadonlySet<string>;
}

interface Move {
  answer: Promise<ReviewInvariantChanges>;
  settled: boolean;
}

const NONE: ReadonlySet<string> = new Set();
const marks = new Map<string, Marks>();
const moves = new Map<string, Move>();
const listeners = new Set<() => void>();

export const invariantScope = (repo: string, master: string, leaf: string) => `${repo}/${master}/${leaf}`;

const notify = () => listeners.forEach((listener) => listener());

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function publish(scope: string, next: Marks | undefined): void {
  if (next && next.keys.size) marks.set(scope, next);
  else marks.delete(scope);
  notify();
}

// A move to `to` changed `keys`: they join the marks still waiting for their detail.
export function markStale(scope: string, to: number, keys: readonly string[]): void {
  if (!keys.length) return;
  publish(scope, { to, keys: new Set([...(marks.get(scope)?.keys ?? []), ...keys]) });
}

// A detail read at `number` loaded these invariants: their marks clear.
export function loadedStale(scope: string, number: number, ids: readonly string[]): void {
  const current = marks.get(scope);
  if (!current || current.to !== number || !ids.some((id) => current.keys.has(id))) return;
  publish(scope, { to: number, keys: new Set([...current.keys].filter((key) => !ids.includes(key))) });
}

// A full renewal re-reads every detail: no mark is left waiting for one.
export function clearStale(scope: string): void {
  if (marks.has(scope)) publish(scope, undefined);
}

export function useStaleInvariants(repo: string, master: string, leaf: string): ReadonlySet<string> {
  const scope = invariantScope(repo, master, leaf);
  return useSyncExternalStore(subscribe, () => marks.get(scope)?.keys ?? NONE);
}

async function changesBetween(repo: string, master: string, leaf: string, from: number, to: number): Promise<ReviewInvariantChanges> {
  const address = `/api/review/trees/changes?${qs({ repo, master, leaf, from: String(from), to: String(to) })}`;
  try {
    const answer = await getReviewJson<ReviewInvariantChanges>(address);
    if (answer.state === 'named' && Array.isArray(answer.invariants)) return answer;
    return { state: 'unavailable', reason: answer.state === 'unavailable' ? answer.reason : `answered ${String(answer.state)}` };
  } catch (cause) {
    return { state: 'unavailable', reason: cause instanceof Error ? cause.message : String(cause) };
  }
}

const moveKey = (scope: string, from: number, to: number) => `${scope}#${from}>${to}`;

// The one question for a move: asked once, whoever asks first; `apply` runs on that first answer.
export function askInvariantChanges(
  repo: string, master: string, leaf: string, from: number, to: number,
  apply: (answer: ReviewInvariantChanges) => void,
): Promise<ReviewInvariantChanges> {
  const key = moveKey(invariantScope(repo, master, leaf), from, to);
  const known = moves.get(key);
  if (known) return known.answer;
  const move: Move = { settled: false, answer: changesBetween(repo, master, leaf, from, to) };
  move.answer = move.answer.then((answer) => {
    move.settled = true;
    apply(answer);
    notify();
    return answer;
  });
  moves.set(key, move);
  return move.answer;
}

// Whether the move's answer is in; a move nobody asked about is not settled.
export function moveSettled(scope: string, from: number, to: number): boolean {
  return moves.get(moveKey(scope, from, to))?.settled ?? false;
}

// Whether the move from `from` to `to` has its answer (no move named: nothing to wait for), re-read as moves settle.
export function useMoveSettled(scope: string, from: number | undefined, to: number | undefined): boolean {
  return useSyncExternalStore(subscribe, () => from === undefined || to === undefined || moveSettled(scope, from, to));
}

export function resetStaleInvariantsForTests(): void {
  marks.clear();
  moves.clear();
  notify();
}

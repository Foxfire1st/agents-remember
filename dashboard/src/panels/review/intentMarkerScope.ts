// The review workspace's scope for per-hunk intent markers (MIK-R34): which comparison they describe,
// each changed file's classification (read once per surface), and the one followed marker the reader
// can return to.
//
// Only a tree comparison provides a scope. A dataset review provides none, and every diff renders
// exactly as before; so does any diff rendered outside a review workspace.
import { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react';

import type { ReviewSourceInventory } from '../../data/review';
import {
  type LaneRead,
  type ReviewFileClassification,
  readFileClassification,
} from '../../data/reviewLane';
import type { MarkTarget } from './hunkMarkers';

// Where a followed marker sat: the file, the pane that drew it and the owner hunk it marks.
export interface MarkerAt {
  path: string;
  pane: string;
  hunk: string;
}

// How the workspace moves for a marker: `capture` records the reading position as it is now and
// returns what puts it back; `open` selects a marker's target in the tree.
export interface MarkerMoves {
  capture: () => () => void;
  open: (target: MarkTarget) => void;
}

// A classification as a pane reads it: `none` for a path with no hunks to mark (unchanged, or not a
// tree comparison), never a zero; `unlisted` for a changed file a partial change inventory does not
// list, whose classification is therefore not read (review R1 F5: said, never silent).
export type MarkRead =
  LaneRead<ReviewFileClassification> | { phase: 'none' } | { phase: 'unlisted' };

export interface IntentMarkerScopeValue {
  comparison: number;
  // Whether the change inventory lists a path, and whether it was measured only in part.
  listed: (path: string) => boolean;
  partial: boolean;
  classify: (path: string) => Promise<LaneRead<ReviewFileClassification>> | null;
  peek: (path: string) => LaneRead<ReviewFileClassification> | undefined;
  // The marker last followed, while the reader has not returned to it, and the target it opened.
  origin: MarkerAt | null;
  target: MarkTarget | null;
  // The marker being returned to, until its pane has drawn and focused it.
  returning: MarkerAt | null;
  follow: (at: MarkerAt, target: MarkTarget) => void;
  back: () => void;
  settle: () => void;
}

export const IntentMarkerScope = createContext<IntentMarkerScopeValue | null>(null);

// A return whose marker is never drawn (its file could not be read again) stops waiting after this.
export const RETURN_WINDOW_MS = 15_000;

interface Followed {
  scope: string;
  at: MarkerAt;
  target: MarkTarget;
  restore: () => void;
}

// What the scope reads of the review's change inventory: the paths it lists, and whether it was
// measured only in part -- `partial`, or not `measured` at all -- so an unlisted changed file may exist.
export function markerInventory(inventory: ReviewSourceInventory): {
  changed: string[];
  partial: boolean;
} {
  return {
    changed: inventory.entries.map((entry) => entry.path),
    partial: inventory.partial || inventory.state !== 'measured',
  };
}

export interface MarkerScopeInput {
  repo: string;
  master: string;
  leaf: string;
  // The tree comparison the review on screen names; `undefined` for a dataset review.
  comparison: number | undefined;
  // The comparison's changed paths: only these have hunks, so only these are classified.
  changed: string[];
  // The change inventory was measured only in part (or not at all): a changed file may be unlisted.
  partial: boolean;
  moves: MarkerMoves;
}

export function useIntentMarkerScope(input: MarkerScopeInput): IntentMarkerScopeValue | null {
  const { repo, master, leaf, comparison, changed, partial, moves } = input;
  const scopeKey = `${repo}/${master}/${leaf}/${comparison ?? ''}`;
  const changedKey = changed.join('\n');
  const listed = useCallback((path: string) => changedKey.split('\n').includes(path), [changedKey]);
  const reads = useClassificationCache(scopeKey, changedKey, (path) =>
    comparison === undefined ? null : readFileClassification(repo, master, leaf, comparison, path),
  );
  const [followed, setFollowed] = useState<Followed | null>(null);
  const [returning, setReturning] = useState<{ scope: string; at: MarkerAt } | null>(null);
  useEffect(() => {
    if (!returning) return undefined;
    const timer = setTimeout(() => setReturning(null), RETURN_WINDOW_MS);
    return () => clearTimeout(timer);
  }, [returning]);
  if (comparison === undefined) return null;
  return {
    comparison,
    listed,
    partial,
    ...reads,
    // A marker of another comparison (the reader refreshed onto a new one) is not offered.
    origin: followed?.scope === scopeKey ? followed.at : null,
    target: followed?.scope === scopeKey ? followed.target : null,
    returning: returning?.scope === scopeKey ? returning.at : null,
    follow: (at, target) => {
      setFollowed({ scope: scopeKey, at, target, restore: moves.capture() });
      setReturning(null);
      moves.open(target);
    },
    back: () => {
      if (followed?.scope !== scopeKey) return;
      setFollowed(null);
      setReturning({ scope: scopeKey, at: followed.at });
      followed.restore();
    },
    settle: () => setReturning(null),
  };
}

// Each changed path's classification, asked once per scope: the answer is shared by every pane of the
// file and kept while the scope lasts. A failed read is forgotten, so reopening the file asks again.
function useClassificationCache(
  scopeKey: string,
  changedKey: string,
  read: (path: string) => Promise<LaneRead<ReviewFileClassification>> | null,
): Pick<IntentMarkerScopeValue, 'classify' | 'peek'> {
  const cache = useRef<{
    scope: string;
    pending: Map<string, Promise<LaneRead<ReviewFileClassification>>>;
    settled: Map<string, LaneRead<ReviewFileClassification>>;
  }>({ scope: '', pending: new Map(), settled: new Map() });
  const latestRead = useRef(read);
  useEffect(() => {
    latestRead.current = read;
  });
  const current = useCallback(() => {
    if (cache.current.scope !== scopeKey)
      cache.current = { scope: scopeKey, pending: new Map(), settled: new Map() };
    return cache.current;
  }, [scopeKey]);
  const classify = useCallback(
    (path: string) => {
      if (!changedKey.split('\n').includes(path)) return null;
      const held = current();
      const known = held.pending.get(path);
      if (known) return known;
      const asked = latestRead.current(path);
      if (!asked) return null;
      const kept = asked.then((answer) => {
        if (answer.phase === 'ready') held.settled.set(path, answer);
        else held.pending.delete(path);
        return answer;
      });
      held.pending.set(path, kept);
      return kept;
    },
    [changedKey, current],
  );
  const peek = useCallback((path: string) => current().settled.get(path), [current]);
  return { classify, peek };
}

// One path's classification for the pane that draws it: `given` when the caller already holds it
// (the lane's own read), else the scope's shared read -- only for a changed path of a tree comparison.
export function useMarkClassification(
  path: string,
  active: boolean,
  given?: ReviewFileClassification,
): MarkRead {
  const scope = useContext(IntentMarkerScope);
  const classify = scope?.classify;
  const asking = active && given === undefined && scope !== null && scope.listed(path);
  // An answer is kept with the read that gave it, so another path's or comparison's never draws.
  const [answer, setAnswer] = useState<{
    classify: IntentMarkerScopeValue['classify'];
    path: string;
    read: MarkRead;
  } | null>(null);
  useEffect(() => {
    if (!asking || !classify) return undefined;
    const pending = classify(path);
    if (!pending) {
      setAnswer({ classify, path, read: { phase: 'none' } });
      return undefined;
    }
    let live = true;
    void pending.then((read) => {
      if (live) setAnswer({ classify, path, read });
    });
    return () => {
      live = false;
    };
  }, [asking, classify, path]);
  if (!scope || !active) return { phase: 'none' };
  if (given !== undefined) return { phase: 'ready', value: given };
  // A complete inventory that does not list the path: the file is unchanged, and there is nothing to
  // mark. A partial one: the file may have changed, and its marks are not read -- which is said.
  if (!scope.listed(path)) return scope.partial ? { phase: 'unlisted' } : { phase: 'none' };
  return shownRead(scope, path, answer);
}

// The answer this read gave for `path`, else what the scope already holds for it, else loading.
function shownRead(
  scope: IntentMarkerScopeValue,
  path: string,
  answer: { classify: IntentMarkerScopeValue['classify']; path: string; read: MarkRead } | null,
): MarkRead {
  if (answer && answer.path === path && answer.classify === scope.classify) return answer.read;
  return scope.peek(path) ?? { phase: 'loading' };
}

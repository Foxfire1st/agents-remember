// One open-review scope on the existing state stream, never a second connection or poller.
// Registrations belong to mounted surfaces; the last disposer reclaims the baseline and callbacks.
import { useEffect, useRef } from 'react';
import { detachInFlightReads } from './abortableRead';
import { useDashboard } from './store';
import { useRevalidateIntentEntry } from './intentEntryRevalidation';

export interface ReviewMemoryScope { repo: string; master: string; leaf: string }
export interface ReviewExplainerMovedEvent extends ReviewMemoryScope {
  kind?: 'comparison-moved' | 'master-moved' | 'moved-no-explainer' | 'cleared';
  from?: number | null;
  to?: number | null;
  blocks?: number;
  blocksChanged?: number;
  updateOffered?: boolean;
  commitFrom?: string | null;
  commitTo?: string | null;
  text?: string;
  cleared?: boolean;
  moved?: null;
}
// `openedAt` is when the registering surface began asking for this scope (before any of its reads),
// on the same monotonic clock as `seenAt`.
interface Registration { scope: ReviewMemoryScope; openedAt: number; changed: () => void; failed: (detail: string) => void }
type MovedListener = (event: ReviewExplainerMovedEvent) => void;
const registrations = new Set<Registration>();
const publicationListeners = new Set<{ scope: ReviewMemoryScope; changed: () => void; failed: (detail: string) => void }>();
const scopeListeners = new Set<() => void>();
const movedListeners = new Set<{ scope: ReviewMemoryScope; onMoved: MovedListener }>();
let revision: string | undefined;
// When the last baseline or notification of this scope was received: a reconnect is only blind
// between then and its own arming.
let seenAt: number | undefined;
const monotonic = (): number => performance.now();
// File stamps may be coarser than the clock (2 s on some filesystems) and round down: an edit is
// taken as possibly unseen when its stamp is this close to the reference.
// Fallback only: the baseline carries no memory identity (`revision` is one connection's counter).
export const MEMORY_STAMP_SLACK_MS = 2_000;
const key = (scope: ReviewMemoryScope | undefined) => scope && JSON.stringify([scope.repo, scope.master, scope.leaf]);
export const reviewMemoryScope = (): ReviewMemoryScope | undefined => registrations.values().next().value?.scope;
export const reviewPublicationScope = (): ReviewMemoryScope | undefined => publicationListeners.values().next().value?.scope;
export function subscribeReviewPublication(
  scope: ReviewMemoryScope, changed: () => void, failed: (detail: string) => void,
): () => void {
  const before = key(reviewPublicationScope());
  const entry = { scope, changed, failed };
  publicationListeners.add(entry);
  if (before !== key(reviewPublicationScope())) scopeListeners.forEach((listener) => listener());
  return () => {
    const previous = key(reviewPublicationScope());
    publicationListeners.delete(entry);
    if (previous !== key(reviewPublicationScope())) scopeListeners.forEach((listener) => listener());
  };
}
// The publication state each scope last saw (page and environment stamps): a reconnect's baseline
// that names it again changed nothing, so it renews nothing. A server that sends no environment
// stamp renews, as before.
const publicationStates = new Map<string, string>();
type PublicationStamps = { kind?: string; pageStamp?: unknown; environmentStamp?: unknown };
// Remembers the state an event names and answers whether it is a baseline repeating it.
function repeatsSeenPublication(scope: ReviewMemoryScope, event: PublicationStamps): boolean {
  if (typeof event.environmentStamp !== 'string') return false;
  const scopeKey = key(scope) ?? '';
  const state = JSON.stringify([event.pageStamp ?? null, event.environmentStamp]);
  const repeated = event.kind === 'baseline' && publicationStates.get(scopeKey) === state;
  publicationStates.set(scopeKey, state);
  return repeated;
}
export function publishReviewPublication(data: unknown, failed = false): void {
  if (typeof data !== 'object' || data === null) return;
  const event = data as Partial<ReviewMemoryScope> & PublicationStamps & { detail?: string };
  if (typeof event.repo !== 'string' || typeof event.master !== 'string' || typeof event.leaf !== 'string') return;
  const scope = { repo: event.repo, master: event.master, leaf: event.leaf };
  if (!failed && repeatsSeenPublication(scope, event)) return;
  // A publication renews explainer answers: none may join a request asked before it.
  if (!failed) detachInFlightReads('explainer:');
  publicationListeners.forEach((entry) => {
    if (key(entry.scope) !== key(scope)) return;
    if (failed) entry.failed(event.detail ?? 'Explanation changes could not be watched. Reopen the review to reconnect.');
    else entry.changed();
  });
}
export function subscribeReviewMemoryScope(callback: () => void): () => void {
  scopeListeners.add(callback);
  return () => { scopeListeners.delete(callback); };
}
export function subscribeReviewExplainerMoved(
  scope: ReviewMemoryScope,
  onMoved: MovedListener,
): () => void {
  const entry = { scope, onMoved };
  movedListeners.add(entry);
  return () => {
    movedListeners.delete(entry);
  };
}
export function publishReviewExplainerMoved(data: unknown): void {
  if (typeof data !== 'object' || data === null) return;
  const payload = data as Partial<ReviewExplainerMovedEvent> & {
    repository?: string;
    task?: string;
    leaf_id?: string;
  };
  const repo = payload.repo ?? payload.repository;
  const master = payload.master ?? payload.task;
  const leaf = payload.leaf ?? payload.leaf_id;
  if (typeof repo !== 'string' || typeof master !== 'string' || typeof leaf !== 'string') return;
  const event: ReviewExplainerMovedEvent = { ...payload, repo, master, leaf };
  const targetKey = key({ repo, master, leaf });
  const listening = [...movedListeners].filter((entry) => key(entry.scope) === targetKey);
  // A moved comparison renews review and explainer answers alike: none may join an older request.
  if (listening.length > 0) detachInFlightReads();
  listening.forEach((entry) => entry.onMoved(event));
}
function register(registration: Registration): () => void {
  const before = key(reviewMemoryScope());
  registrations.add(registration);
  if (before !== key(reviewMemoryScope())) {
    revision = undefined;
    seenAt = undefined;
    scopeListeners.forEach((listener) => listener());
  }
  return () => {
    const previous = key(reviewMemoryScope());
    registrations.delete(registration);
    if (previous !== key(reviewMemoryScope())) {
      revision = undefined;
      seenAt = undefined;
      scopeListeners.forEach((listener) => listener());
    }
  };
}
// Whether a connection's baseline must renew one registration's answers. A baseline is readiness,
// not proof that the independently read HTTP answers are current: an edit before arming produced no
// native notification. The server names how long ago memory last changed (`changedAgoMs`, read after
// arming), so the answers are renewed exactly when that change may postdate them: after the surface
// opened (the first baseline) or after the last event the previous connection delivered (a
// reconnect). A server that does not say renews, as before.
function baselineRenews(entry: Registration, arming: boolean, changedAgoMs: unknown, now: number): boolean {
  if (typeof changedAgoMs !== 'number' || !Number.isFinite(changedAgoMs)) return true;
  const since = arming ? entry.openedAt : seenAt;
  if (since === undefined) return true;
  // Transit makes `now` late, so the change is placed late, never early: the error only renews.
  return now - changedAgoMs >= since - MEMORY_STAMP_SLACK_MS;
}

export function publishReviewMemory(
  data: ReviewMemoryScope & { revision: string; changedAgoMs?: number },
  initial = false,
): void {
  if (key(data) !== key(reviewMemoryScope()) || typeof data.revision !== 'string') return;
  const now = monotonic();
  const arming = revision === undefined;
  const baseline = arming || initial;
  const notified = !baseline && revision !== data.revision;
  revision = data.revision;
  const renewed = [...registrations].filter((entry) =>
    key(entry.scope) === key(data) &&
    (notified || (baseline && baselineRenews(entry, arming, data.changedAgoMs, now))));
  seenAt = now;
  // The renewal asks again: it must not join a request asked before the change.
  if (renewed.length > 0) detachInFlightReads('review:');
  renewed.forEach((entry) => entry.changed());
}
export function publishReviewMemoryError(data: ReviewMemoryScope & { detail?: string }): void {
  if (key(data) !== key(reviewMemoryScope())) return;
  registrations.forEach((entry) => {
    if (key(entry.scope) === key(data)) entry.failed(data.detail ?? 'Memory watch failed. Retry the review or reopen it to reconnect.');
  });
}
export function useReviewMemoryWatch(
  scope: ReviewMemoryScope & { history?: string },
  cycle: { refresh: () => void; memoryChanged: () => void; memoryWatchFailed: (detail: string) => void },
  retainSelection?: () => void,
): void {
  const { repo, master, leaf, history } = scope;
  const live = useDashboard((state) => Object.values(state.enclosures).some((node) =>
    node.repoName === repo && node.taskName === master && node.leafId === leaf &&
    state.activeWorktreeGroups.includes(node.worktreeGroup.split('/').filter(Boolean).pop() ?? ''),
  ));
  const revalidate = useRevalidateIntentEntry();
  const latest = useRef({ cycle, revalidate, retainSelection });
  latest.current = { cycle, revalidate, retainSelection };
  // When this surface began asking for this scope: stamped in the render that names the scope, so it
  // precedes every read the surface's effects start for it.
  const scopeKey = key({ repo, master, leaf });
  const opened = useRef<{ key: string | undefined; at: number }>({ key: undefined, at: 0 });
  if (opened.current.key !== scopeKey) opened.current = { key: scopeKey, at: monotonic() };
  const openedAt = opened.current.at;
  useEffect(() => {
    if (!live || history === 'recorded') return;
    return register({ scope: { repo, master, leaf }, openedAt, changed: () => {
      // The renewed catalogue must not undo the implicit subject already on screen.
      latest.current.retainSelection?.();
      latest.current.cycle.memoryChanged();
      latest.current.revalidate();
    }, failed: (detail) => latest.current.cycle.memoryWatchFailed(detail) });
  }, [repo, master, leaf, history, live, openedAt]);
}

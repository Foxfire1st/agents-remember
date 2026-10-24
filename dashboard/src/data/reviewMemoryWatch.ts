// One open-review scope on the existing state stream, never a second connection or poller.
// Registrations belong to mounted surfaces; the last disposer reclaims the baseline and callbacks.
import { useEffect, useRef } from 'react';
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
interface Registration { scope: ReviewMemoryScope; changed: () => void; failed: (detail: string) => void }
type MovedListener = (event: ReviewExplainerMovedEvent) => void;
const registrations = new Set<Registration>();
const scopeListeners = new Set<() => void>();
const movedListeners = new Set<{ scope: ReviewMemoryScope; onMoved: MovedListener }>();
let revision: string | undefined;
const key = (scope: ReviewMemoryScope | undefined) => scope && JSON.stringify([scope.repo, scope.master, scope.leaf]);
export const reviewMemoryScope = (): ReviewMemoryScope | undefined => registrations.values().next().value?.scope;
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
  movedListeners.forEach((entry) => {
    if (key(entry.scope) === targetKey) entry.onMoved(event);
  });
}
function register(registration: Registration): () => void {
  const before = key(reviewMemoryScope());
  registrations.add(registration);
  if (before !== key(reviewMemoryScope())) {
    revision = undefined;
    scopeListeners.forEach((listener) => listener());
  }
  return () => {
    const previous = key(reviewMemoryScope());
    registrations.delete(registration);
    if (previous !== key(reviewMemoryScope())) {
      revision = undefined;
      scopeListeners.forEach((listener) => listener());
    }
  };
}
export function publishReviewMemory(data: ReviewMemoryScope & { revision: string }, initial = false): void {
  if (key(data) !== key(reviewMemoryScope()) || typeof data.revision !== 'string') return;
  // The first native baseline is readiness, not proof that the independently read HTTP
  // snapshot is current: an edit before admission/arming produced no native notification.
  // Reconcile once after arming, then stay quiet until an edit or reconnect baseline.
  const changed = revision === undefined || initial || revision !== data.revision;
  revision = data.revision;
  if (changed) registrations.forEach((entry) => { if (key(entry.scope) === key(data)) entry.changed(); });
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
  useEffect(() => {
    if (!live || history === 'recorded') return;
    return register({ scope: { repo, master, leaf }, changed: () => {
      // The renewed catalogue must not undo the implicit subject already on screen.
      latest.current.retainSelection?.();
      latest.current.cycle.memoryChanged();
      latest.current.revalidate();
    }, failed: (detail) => latest.current.cycle.memoryWatchFailed(detail) });
  }, [repo, master, leaf, history, live]);
}

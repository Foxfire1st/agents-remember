import { useCallback, useEffect, useRef, useState } from 'react';

import { parseReaderHash } from '../data/knowledgeReader';

import type { ChangeSetTarget } from '../panels/changeset/ChangeSetViewer';

export function parseReviewHash(hash: string): ChangeSetTarget | null {
  if (!hash.startsWith('#review?')) return null;
  const params = new URLSearchParams(hash.slice('#review?'.length));
  const repo = params.get('repo');
  const master = params.get('master');
  const leaf = params.get('leaf');
  if (!repo || !master || !leaf) return null;
  return {
    repo, master, leaf,
    review: params.get('history') === 'recorded' ? { historical: true } : {},
  };
}

export function reviewHash(target: ChangeSetTarget): string {
  const params = new URLSearchParams({ repo: target.repo, master: target.master ?? '', leaf: target.leaf ?? '' });
  if (target.review?.historical) params.set('history', 'recorded');
  return `#review?${params}`;
}

// Selection and comparison are history entries within the mounted review target. Restoring an
// entry may normalize its state with replace; a reader's new choice uses push, without hashchange.
// The target's own keys (repo, master, leaf, history) are never touched here.
export interface ReviewHashState {
  sel: string | null;
  comparison: number | null;
}

export function readReviewHashState(hash: string = window.location.hash): ReviewHashState {
  if (!hash.startsWith('#review?')) return { sel: null, comparison: null };
  const params = new URLSearchParams(hash.slice('#review?'.length));
  const raw = params.get('comparison');
  const number = raw !== null && /^\d+$/.test(raw) ? Number(raw) : null;
  return { sel: params.get('sel') || null, comparison: number };
}

export function writeReviewHashState(state: ReviewHashState, mode: 'push' | 'replace' = 'push'): void {
  const hash = window.location.hash;
  if (!hash.startsWith('#review?')) return;
  const params = new URLSearchParams(hash.slice('#review?'.length));
  params.delete('sel');
  params.delete('comparison');
  if (state.comparison !== null) params.set('comparison', String(state.comparison));
  if (state.sel) params.set('sel', state.sel);
  const next = `#review?${params}`;
  if (next === hash) return;
  window.history[mode === 'push' ? 'pushState' : 'replaceState'](window.history.state, '', next);
}

export function useReviewHash(
  setView: (view: 'operations' | 'knowledge') => void,
  setNotesOpen: (open: boolean) => void,
) {
  const [target, setTarget] = useState<ChangeSetTarget | null>(() => parseReviewHash(window.location.hash));
  useEffect(() => {
    const navigate = () => {
      const next = parseReviewHash(window.location.hash);
      setTarget((current) => {
        // Same-target history is restored by the mounted review page.
        if (next) return current?.review && reviewHash(current) === reviewHash(next) ? current : next;
        return current?.review ? null : current;
      });
      if (next || parseReaderHash(window.location.hash)) {
        setView(next ? 'operations' : 'knowledge');
        setNotesOpen(false);
      }
    };
    window.addEventListener('hashchange', navigate);
    window.addEventListener('popstate', navigate);
    return () => {
      window.removeEventListener('hashchange', navigate);
      window.removeEventListener('popstate', navigate);
    };
  }, [setView, setNotesOpen]);
  const setChangeSet = useCallback((next: ChangeSetTarget | null) => {
    parked.current = null;
    if (next?.review) {
      window.history.pushState(null, '', reviewHash(next));
    } else if (window.location.hash.startsWith('#review?')) {
      window.history.pushState(null, '', window.location.pathname + window.location.search);
    }
    setTarget(next);
  }, []);
  // A bottom tab left with the review open keeps the review mounted (D575): only its address leaves
  // the URL, and returning puts that exact address back, selection and comparison included. The URL
  // wins: an address the URL names by then is kept, and the parked one is dropped.
  const parked = useRef<string | null>(null);
  const park = useCallback(() => {
    if (!window.location.hash.startsWith('#review?')) return;
    parked.current = window.location.hash;
    window.history.pushState(null, '', window.location.pathname + window.location.search);
  }, []);
  const unpark = useCallback(() => {
    const hash = parked.current;
    parked.current = null;
    if (hash && !window.location.hash) window.history.pushState(null, '', hash);
  }, []);
  return [target, setChangeSet, { park, unpark }] as const;
}

import { useCallback, useEffect, useState } from 'react';

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

// The review page's own part of its hash (MIK-R89 v2 rules 9 and 10): the selection, as rule 13's
// identity (`sel=`, panels/review/blockIdentity.ts `formatSelection`), and the comparison on screen
// when it is not the latest (`comparison=<n>`). The page writes it with `history.replaceState`: a
// selection adds no history entry and fires no `hashchange`, so the reason of the comment in
// `useReviewHash` below holds, and a reload restores what was selected. The target's own keys
// (repo, master, leaf, history) are never touched here.
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

export function writeReviewHashState(state: ReviewHashState): void {
  const hash = window.location.hash;
  if (!hash.startsWith('#review?')) return;
  const params = new URLSearchParams(hash.slice('#review?'.length));
  params.delete('sel');
  params.delete('comparison');
  if (state.comparison !== null) params.set('comparison', String(state.comparison));
  if (state.sel) params.set('sel', state.sel);
  const next = `#review?${params}`;
  if (next === hash) return;
  window.history.replaceState(window.history.state, '', next);
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
        // Subject selection belongs to the mounted reviewer, never to browser history.
        if (next) return current?.review && reviewHash(current) === reviewHash(next) ? current : next;
        return current?.review ? null : current;
      });
      if (next || parseReaderHash(window.location.hash)) {
        setView(next ? 'operations' : 'knowledge');
        setNotesOpen(false);
      }
    };
    window.addEventListener('hashchange', navigate);
    return () => window.removeEventListener('hashchange', navigate);
  }, [setView, setNotesOpen]);
  const setChangeSet = useCallback((next: ChangeSetTarget | null) => {
    if (next?.review) {
      window.history.pushState(null, '', reviewHash(next));
    } else if (window.location.hash.startsWith('#review?')) {
      window.history.pushState(null, '', window.location.pathname + window.location.search);
    }
    setTarget(next);
  }, []);
  return [target, setChangeSet] as const;
}

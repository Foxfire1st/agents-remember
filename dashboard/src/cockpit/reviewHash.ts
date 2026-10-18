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

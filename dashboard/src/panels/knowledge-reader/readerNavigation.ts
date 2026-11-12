// The browser's own history entry holds the place left by a navigation. Only the
// current visible position is kept locally; there is no finite path cache to evict Back visits.
import { useCallback, useEffect, useLayoutEffect, useRef, useState, type RefObject } from 'react';
import { parseReaderHash, readerHash, type ReaderAddress } from '../../data/knowledgeReader';

export function useReaderNavigation(pane: RefObject<HTMLDivElement | null>) {
  const [address, setAddress] = useState(() => parseReaderHash(window.location.hash));
  const restore = useRef(0);
  const visiblePosition = useRef(0);
  const hash = useRef(window.location.hash);
  useEffect(() => {
    const follow = () => {
      if (hash.current === window.location.hash) return;
      const next = parseReaderHash(window.location.hash);
      if (!next) return;
      hash.current = window.location.hash;
      restore.current = window.history.state?.knowledgeScroll ?? 0;
      visiblePosition.current = restore.current;
      setAddress(next);
    };
    window.addEventListener('popstate', follow);
    window.addEventListener('hashchange', follow);
    return () => {
      window.removeEventListener('popstate', follow);
      window.removeEventListener('hashchange', follow);
    };
  }, []);
  const remember = useCallback(() => {
    if (!hiddenDocument(pane.current)) visiblePosition.current = pane.current?.scrollTop ?? 0;
  }, [pane]);
  const save = useCallback(() => {
    remember();
    window.history.replaceState(
      { ...window.history.state, knowledgeScroll: visiblePosition.current },
      '',
    );
  }, [remember]);
  const go = useCallback(
    (next: ReaderAddress) => {
      const nextHash = readerHash(next);
      if (nextHash === hash.current) {
        if (pane.current) pane.current.scrollTop = 0;
        return;
      }
      save();
      restore.current = 0;
      visiblePosition.current = 0;
      window.history.pushState({ ...window.history.state, knowledgeScroll: 0 }, '', nextHash);
      hash.current = nextHash;
      setAddress(next);
    },
    [save, pane],
  );
  const restoreCurrent = useCallback(() => {
    if (pane.current) pane.current.scrollTop = visiblePosition.current;
  }, [pane]);
  return { address, go, save, remember, restore, restoreCurrent };
}

export function useRestoreReaderScroll(
  pane: RefObject<HTMLDivElement | null>,
  ready: string,
  restore: RefObject<number>,
) {
  useLayoutEffect(() => {
    if (ready && pane.current) pane.current.scrollTop = restore.current;
  }, [pane, ready, restore]);
}

// Chromium reads zero from a CSS-hidden panel. That zero is not the reader's place.
function hiddenDocument(pane: HTMLDivElement | null): boolean {
  if (!pane || pane.closest('[aria-hidden=true]')) return true;
  return ['[data-reader-document]', '[data-reader-content]'].some((selector) => {
    const panel = pane.closest(selector);
    return panel !== null && window.getComputedStyle(panel).display === 'none';
  });
}

// The reads a card or a lane file makes only once it is in view (MIK-R89 v2 rule 2, W5): a card's
// intent-marker classification (`/api/review/trees?file=`), and a lane file's classification and
// text (`/api/changeset/file-diff?comparison=`). They wait until their block scrolls near the reading
// column's viewport, and at most READ_BATCH files read at a time: a file holds one place in the gate
// for all of its reads (a lane file's two), and the next waiting file starts when a place is free.
// The opening's own six reads never pass through here, and none of these starts before them: a lane
// file in view is read once the opening's six reads are done (`open`), with no gesture, and the
// others as they scroll into view (amendment f43ae286); a card's marks wait for the reader's first
// scroll, wheel, key move or selection on the page (`engage`). So the opening makes exactly rule 2's
// six reads, and lane file reads follow them.
import { createContext, useContext, useEffect, useState, useSyncExternalStore } from 'react';

import { columnScrolls } from './blockDom';
import { useReviewShown } from '../reviewShown';

export const READ_BATCH = 8;

export class ReadGate {
  private active = 0;
  private readonly waiting: (() => void)[] = [];

  constructor(private readonly limit: number = READ_BATCH) {}

  // Runs `task` once a place is free; the place is held until the task settles.
  run<T>(task: () => Promise<T>): Promise<T> {
    return new Promise<T>((resolve, reject) => {
      const start = () => {
        this.active += 1;
        let settled: Promise<T>;
        try {
          settled = task();
        } catch (cause) {
          settled = Promise.reject(cause);
        }
        settled.then(resolve, reject).finally(() => {
          this.active -= 1;
          this.waiting.shift()?.();
        });
      };
      if (this.active < this.limit) start();
      else this.waiting.push(start);
    });
  }

  get running(): number {
    return this.active;
  }

  private engagedNow = false;
  private openedNow = false;
  private readonly listeners = new Set<() => void>();

  // The reader's first gesture on the page; in-view reads may start from now on.
  engage(): void {
    if (this.engagedNow) return;
    this.engagedNow = true;
    for (const listener of this.listeners) listener();
  }

  get engaged(): boolean {
    return this.engagedNow;
  }

  // The opening's six reads are done; in-view lane file reads may start from now on.
  open(): void {
    if (this.openedNow) return;
    this.openedNow = true;
    for (const listener of this.listeners) listener();
  }

  get opened(): boolean {
    return this.openedNow;
  }

  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };
}

// The reading column's gate; absent outside the review page, where reads start as before.
export const ReadGateContext = createContext<ReadGate | null>(null);

export const useReadGate = (): ReadGate | null => useContext(ReadGateContext);

const always = () => () => undefined;

// Whether in-view reads may start: once the page's reader has engaged, or for a lane file once the
// opening's reads are done; outside the review page (no gate) reads start as before.
function useStarted(gate: ReadGate | null, afterOpening: boolean): boolean {
  return useSyncExternalStore(gate ? gate.subscribe : always, () => (gate ? gate.engaged || (afterOpening && gate.opened) : true));
}

// What counts as the reader's first move on the page: a scroll or wheel on the tree or the column, a key
// that moves (never typing in a field), or a selection (ReadingColumn engages on it).
const MOVE_KEYS = new Set(['j', 'k', 'ArrowDown', 'ArrowUp', 'PageDown', 'PageUp', 'Home', 'End', ' ']);
const MOVES = ['scroll', 'wheel', 'touchmove'] as const;

function typing(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  return target.isContentEditable || ['INPUT', 'TEXTAREA', 'SELECT'].includes(target.tagName);
}

export function useEngageOnGesture(gate: ReadGate, page: React.RefObject<HTMLElement | null>): void {
  // A hidden review (another bottom tab on screen, D575) is engaged by nothing done in that tab.
  const shown = useReviewShown();
  useEffect(() => {
    if (gate.engaged || !shown) return undefined;
    // The page under the reader: the tree and the column (not the change page drawn over them), or a
    // cold page's body.
    const inPage = (target: EventTarget | null) =>
      target === document ||
      target === document.body ||
      (target instanceof Node && Boolean(page.current?.closest('[data-review-body]')?.contains(target)));
    const onMove = (event: Event) => {
      if (inPage(event.target)) gate.engage();
    };
    const onKey = (event: KeyboardEvent) => {
      if (MOVE_KEYS.has(event.key) && !typing(event.target) && inPage(event.target)) gate.engage();
    };
    const options = { capture: true, passive: true } as const;
    for (const name of MOVES) window.addEventListener(name, onMove, options);
    window.addEventListener('keydown', onKey, true);
    return () => {
      for (const name of MOVES) window.removeEventListener(name, onMove, options);
      window.removeEventListener('keydown', onKey, true);
    };
  }, [gate, page, shown]);
}

// How far beyond the viewport a block counts as in view, so its reads have answered by the time the
// reader reaches it.
const NEAR = '600px 0px';

// The element the block scrolls in: the reading column while it is the page scroller, else the
// viewport (the stacked narrow layout, where the column does not scroll on its own).
function scrollRoot(node: Element): Element | null {
  const scroller = node.closest('[data-review-scroller]');
  return scroller && columnScrolls() ? scroller : null;
}

// Whether the element is in (or near) view, once the reader has engaged with the page, or with
// `afterOpening` (a lane file) once the opening's reads are done. Once true it stays true: a block
// that was read is not read again when it scrolls out. Without IntersectionObserver (jsdom, old
// engines) everything counts as in view, which is the behaviour before this gate existed.
export function useInView(ref: React.RefObject<Element | null>, afterOpening = false): boolean {
  const started = useStarted(useReadGate(), afterOpening);
  const [seen, setSeen] = useState(() => typeof IntersectionObserver === 'undefined');
  useEffect(() => {
    const node = ref.current;
    if (seen || !node || typeof IntersectionObserver === 'undefined') return undefined;
    const observer = new IntersectionObserver(
      (records) => {
        if (records.some((record) => record.isIntersecting)) setSeen(true);
      },
      { root: scrollRoot(node), rootMargin: NEAR },
    );
    observer.observe(node);
    return () => observer.disconnect();
  }, [ref, seen]);
  return seen && started;
}

// "After a selection's read, the reading column scrolls once to the subject's heading, unless the
// reader has scrolled since the click" (MIK-R89 v2 rule 9; evidence 7). A selection arms one target;
// any scrolling gesture of the reader on the column (wheel, touch, a scrolling key, its scrollbar)
// disarms it; when the selection's answer is on screen the column is scrolled once, by its own
// `scrollTop`, and the target is spent. The tree is never scrolled from here. Otherwise the column
// keeps the reader's line in place while content above it changes height (readerAnchor.ts).
import { useCallback, useEffect, useRef, useState } from 'react';

import { blockElement, columnScrolls, groupDomId, laneFileDomId, scrollColumnTo, subjectDomId } from './blockDom';
import { isScrollKey, useReaderAnchor } from './readerAnchor';

export type ScrollTarget =
  | { kind: 'subject'; key: string }
  | { kind: 'block'; key: string }
  | { kind: 'group'; subjectKey: string; group: 'unchanged' | 'not-comparable' }
  | { kind: 'lane'; path?: string };

function targetElement(scroller: HTMLElement, target: ScrollTarget): HTMLElement | null {
  const byId = (id: string) => scroller.ownerDocument.getElementById(id);
  if (target.kind === 'subject') return byId(subjectDomId(target.key));
  if (target.kind === 'block') return blockElement(scroller, target.key);
  if (target.kind === 'group') return byId(groupDomId(target.subjectKey, target.group));
  return target.path ? byId(laneFileDomId(target.path)) : byId('review-lane');
}

// The column scrolls itself while it is the page scroller; in the stacked narrow layout the page
// around it scrolls, and the target is brought into view there.
function bring(scroller: HTMLElement, element: HTMLElement | null): void {
  if (!element) return;
  if (columnScrolls()) scrollColumnTo(scroller, element);
  else element.scrollIntoView?.({ block: 'start' });
}

export function useScrollOnce(scroller: React.RefObject<HTMLElement | null>) {
  const armed = useRef<{ target: ScrollTarget; armed: boolean } | null>(null);
  // While a landing is due and the reader has not scrolled, the landing alone moves the column.
  const holding = useCallback(() => armed.current?.armed === true, []);
  useReaderAnchor(scroller, holding);
  useEffect(() => {
    const node = scroller.current;
    if (!node) return undefined;
    const disarm = () => {
      if (armed.current) armed.current.armed = false;
    };
    const onKey = (event: KeyboardEvent) => {
      if (isScrollKey(event)) disarm();
    };
    // A pointer pressed on the column itself, not on its content, is its scrollbar.
    const onPointer = (event: PointerEvent) => {
      if (event.target === node) disarm();
    };
    node.addEventListener('wheel', disarm, { passive: true });
    node.addEventListener('touchmove', disarm, { passive: true });
    node.addEventListener('keydown', onKey);
    node.addEventListener('pointerdown', onPointer);
    return () => {
      node.removeEventListener('wheel', disarm);
      node.removeEventListener('touchmove', disarm);
      node.removeEventListener('keydown', onKey);
      node.removeEventListener('pointerdown', onPointer);
    };
  }, [scroller]);
  // Arming renders once more, so a target whose place is already drawn (a group already open) lands.
  const [, rearm] = useState(0);
  const arm = useCallback((target: ScrollTarget | null) => {
    armed.current = target ? { target, armed: true } : null;
    if (target) rearm((count) => count + 1);
  }, []);
  // Spends the armed target: scrolls once to it, unless the reader scrolled since it was armed.
  const land = useCallback(() => {
    const at = armed.current;
    const node = scroller.current;
    if (!at || !node) return;
    armed.current = null;
    if (at.armed) bring(node, targetElement(node, at.target));
  }, [scroller]);
  const waiting = useCallback(() => armed.current !== null, []);
  return { arm, land, waiting };
}

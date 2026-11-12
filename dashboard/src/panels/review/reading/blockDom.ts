// Where the reading column's parts are in the DOM: a block by its identity key, a subject's heading,
// a group and a lane file, and whether the column is the page scroller. A block key (blockIdentity.ts `blockKey`) joins its parts with NUL, which an attribute value
// does not carry well; its DOM form joins them with `|` instead.
import { BLOCK_STOP } from './readingStyles';

const NUL = String.fromCharCode(0);
export const blockDomKey = (key: string): string => key.split(NUL).join('|');

export function blockElement(scroller: HTMLElement | null, key: string): HTMLElement | null {
  if (!scroller) return null;
  const wanted = blockDomKey(key);
  for (const element of scroller.querySelectorAll<HTMLElement>(`[${BLOCK_STOP}]`))
    if (element.getAttribute(BLOCK_STOP) === wanted) return element;
  return null;
}

export const subjectDomId = (key: string): string => `review-subject-${encodeURIComponent(key)}`;
export const groupDomId = (subjectKey: string, kind: 'unchanged' | 'not-comparable'): string =>
  `review-group-${encodeURIComponent(subjectKey)}-${kind}`;
export const laneFileDomId = (path: string): string => `review-lane-${encodeURIComponent(path)}`;

// The reading column is the page scroller except in the stacked narrow layout (40rem and narrower),
// where the page around it scrolls (ReviewLayout.tsx). The same media query decides both.
const STACKED_QUERY = '(max-width: 40rem)';
export function columnScrolls(): boolean {
  return typeof window.matchMedia !== 'function' || !window.matchMedia(STACKED_QUERY).matches;
}

// The sticky head inside the scroller covers the top of the column: a scrolled-to part lands below it.
function stickyOffset(scroller: HTMLElement): number {
  const head = scroller.querySelector<HTMLElement>('[data-reading-head]');
  return head ? head.getBoundingClientRect().height : 0;
}

// The event a product scroll of the column announces once it has scrolled: the reader's line is taken
// again at the new place (readerAnchor.ts).
export const PRODUCT_SCROLL = 'review-product-scroll';

// Scrolls the page scroller so that `target` starts just below the sticky head. Only the scroller's
// own `scrollTop` changes: no ancestor and no tree is scrolled (rule 9).
export function scrollColumnTo(scroller: HTMLElement | null, target: HTMLElement | null): boolean {
  if (!scroller || !target) return false;
  const top = target.getBoundingClientRect().top - scroller.getBoundingClientRect().top;
  scroller.scrollTop = scroller.scrollTop + top - stickyOffset(scroller) - 8;
  scroller.dispatchEvent(new Event(PRODUCT_SCROLL));
  return true;
}

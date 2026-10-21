// Reveal a tree row inside the tree's one scroller, by its `scrollTop` only (MIK-R89 v2 rule 9). The
// scroller's height comes from CSS alone: nothing here writes a style, so a pending read that shrinks
// the page can never leave the tree at 0 px (UX F1). The rows pinned to the scroller's bottom (the
// Worklist and "Code without invariant") cover the rows above them, so a row above the pinned block is
// revealed above it.
export const TREE_SCROLL_SELECTOR = '[data-review-tree-panel]';
export const TREE_PINNED_SELECTOR = '[data-review-tree-pinned]';

export function revealTreeRow(row: HTMLElement | null): void {
  const scroller = row?.closest<HTMLElement>(TREE_SCROLL_SELECTOR);
  if (!row || !scroller) return;
  const bounds = scroller.getBoundingClientRect();
  const pinned = scroller.querySelector<HTMLElement>(TREE_PINNED_SELECTOR);
  const coveredBy =
    pinned && !pinned.contains(row) && pinned.compareDocumentPosition(row) & Node.DOCUMENT_POSITION_PRECEDING
      ? pinned.getBoundingClientRect().top
      : bounds.bottom;
  const bottom = Math.min(bounds.bottom, coveredBy);
  const selected = row.getBoundingClientRect();
  if (selected.top < bounds.top) scroller.scrollTop += selected.top - bounds.top;
  else if (selected.bottom > bottom) scroller.scrollTop += selected.bottom - bottom;
}

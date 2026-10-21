// Arrow up and down move focus between the tree's shown rows, pinned rows included; Enter and Space
// select, as buttons do (MIK-R86 v1 rule 8, kept by v2). Focus never scrolls the page: the row is
// revealed in the tree's own scroller (treeScroll.ts).
import type { KeyboardEvent } from 'react';

import { revealTreeRow } from '../treeScroll';

export const TREE_ROOT_SELECTOR = '[data-testid="review-tree"]';

export function treeArrow(event: KeyboardEvent<HTMLButtonElement>): void {
  const delta = event.key === 'ArrowDown' ? 1 : event.key === 'ArrowUp' ? -1 : 0;
  if (delta === 0) return;
  const nodes = Array.from(
    event.currentTarget.closest(TREE_ROOT_SELECTOR)?.querySelectorAll<HTMLButtonElement>('[data-tree-node]') ?? [],
  );
  const index = nodes.indexOf(event.currentTarget);
  if (index === -1) return;
  event.preventDefault();
  const next = nodes[(index + delta + nodes.length) % nodes.length];
  next?.focus({ preventScroll: true });
  revealTreeRow(next ?? null);
}

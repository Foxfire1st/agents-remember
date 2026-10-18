// Reveal a selected row without scrolling the containing page.
export function revealTreeRow(row: HTMLElement | null): void {
  const panel = row?.closest<HTMLElement>('[data-review-tree-panel]');
  if (!row || !panel) return;
  const bounds = visiblePanelBounds(panel);
  const selected = row.getBoundingClientRect();
  if (selected.top < bounds.top) panel.scrollTop += selected.top - bounds.top;
  else if (selected.bottom > bounds.bottom) panel.scrollTop += selected.bottom - bounds.bottom;
}

function visiblePanelBounds(panel: HTMLElement): Pick<DOMRect, 'top' | 'bottom'> {
  // Restore the responsive CSS limit before measuring a changed viewport/header. A fixed header
  // allowance can otherwise leave the panel itself below the review's clipping edge, where even
  // its maximum scrollTop cannot reveal the last row. Constrain the scrollport, not the page.
  panel.style.removeProperty('max-height');
  let bounds = panel.getBoundingClientRect();
  const surface = panel.closest<HTMLElement>('[data-kbzone="review"]');
  const surfaceBounds = surface?.getBoundingClientRect();
  const bottom = Math.min(window.innerHeight, surfaceBounds?.bottom ?? Infinity);
  if (bounds.bottom > bottom) {
    panel.style.maxHeight = `${Math.max(0, bottom - bounds.top)}px`;
    bounds = panel.getBoundingClientRect();
  }
  const top = Math.max(0, bounds.top, surfaceBounds?.top ?? -Infinity);
  const visibleBottom = Math.min(bottom, bounds.bottom);
  return { top: belowControls(panel, top, visibleBottom), bottom: visibleBottom };
}

function belowControls(panel: HTMLElement, top: number, bottom: number): number {
  const controls = panel.querySelector<HTMLElement>('[data-testid="review-triage-bar"]')?.getBoundingClientRect();
  return controls && controls.top < bottom && controls.bottom > top
    ? Math.max(top, controls.bottom)
    : top;
}

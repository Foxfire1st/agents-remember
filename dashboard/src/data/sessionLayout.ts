// Narrow-width + 80-col-floor rules for the sessions view (260715-FEUI-L1 S2, R3) — pure
// decisions so vitest covers the thresholds without a layout engine. The React side feeds
// measured widths in; this module answers "collapse / expand / show the floor chip".

/** The rail auto-collapses when the view is narrower than this (reopenable — R3). */
export const RAIL_AUTO_COLLAPSE_PX = 900;

/** The inspector auto-collapses when the view is narrower than this (reopenable — R3). */
export const INSPECTOR_AUTO_COLLAPSE_PX = 1100;

/** The PTY width the stage defends before panels yield (design §2). */
export const PTY_MIN_COLS = 80;

/**
 * Approximate mono cell advance at the terminal's 13px font (≈7.8px) plus a per-cell share of
 * the pane chrome. An APPROXIMATION by design — the chip is a hint, not a measurement; the real
 * cols arrive with the xterm stage (L6).
 */
export const APPROX_CELL_PX = 8;

export function ptyFloorPx(cols: number = PTY_MIN_COLS): number {
  return cols * APPROX_CELL_PX;
}

/** The design's rail width target (~280px) — review round 2 finding 4. */
export const RAIL_TARGET_PX = 280;

/** The fallback rail percentage: ≈280px at the 1280px design reference width. */
export const RAIL_FALLBACK_PERCENT = 22;

/**
 * react-resizable-panels v3 is percentage-only, so the ~280px rail target is converted from the
 * measured view width on first real layout (clamped to the panel's min/max). Unmeasured (0)
 * widths keep the 1280px-reference fallback.
 */
export function railDefaultPercent(
  rootWidthPx: number,
  minPercent = 12,
  maxPercent = 40,
): number {
  if (rootWidthPx <= 0) return RAIL_FALLBACK_PERCENT;
  return Math.min(maxPercent, Math.max(minPercent, (RAIL_TARGET_PX / rootWidthPx) * 100));
}

/**
 * True when react-resizable-panels has a persisted layout under this autoSaveId — calibration
 * must never override a user's saved layout. Storage is injectable for tests; the key format
 * (`react-resizable-panels:${autoSaveId}`) is the library's own (getPanelGroupKey).
 */
export function hasPersistedPanelLayout(
  autoSaveId: string,
  storage?: Pick<Storage, "getItem">,
): boolean {
  const store = storage ?? (typeof window === "undefined" ? undefined : window.localStorage);
  if (!store) return false;
  try {
    return store.getItem(`react-resizable-panels:${autoSaveId}`) !== null;
  } catch {
    return false;
  }
}

/** True when a measured stage width can no longer fit ~80 columns — the hint chip shows. */
export function stageBelowPtyFloor(stageWidthPx: number): boolean {
  return stageWidthPx > 0 && stageWidthPx < ptyFloorPx();
}

/**
 * Auto-collapse fires only on a DOWNWARD threshold crossing and auto-expand only on an UPWARD
 * one — so a user who reopens a panel below the threshold is respected (no fight: staying below
 * produces no new crossing), and a user who closed it above the threshold stays closed.
 * `previousWidth: null` (first measure) treats the threshold as the starting side.
 */
export function autoCollapseTransition(
  previousWidth: number | null,
  width: number,
  threshold: number,
): "collapse" | "expand" | null {
  const wasBelow = previousWidth !== null && previousWidth < threshold;
  const isBelow = width < threshold;
  if (previousWidth === null) return isBelow ? "collapse" : null;
  if (!wasBelow && isBelow) return "collapse";
  if (wasBelow && !isBelow) return "expand";
  return null;
}

// The cockpit focus model (design §5.3) — pure region logic. Regions carry a
// `data-region` marker; each region exposes ONE primary focus target (`data-focus-target`):
// rail = the selected row (roving tabindex), stage = the composer, inspector = the active
// tab. F6 cycles forward, Shift+F6 backward; collapsed panels drop out of the cycle. Esc from
// the composer lands on the stage HEADER (not the cycle target), and the explicit
// Focus-terminal command routes INTO the PTY host. (The former `statusline` region left the
// cycle when the StatusLine bar was removed to declutter.)

export const FOCUS_REGIONS = ["rail", "stage", "inspector"] as const;

export type FocusRegion = (typeof FOCUS_REGIONS)[number];

/** The F6 cycle over the currently-available regions (collapsed panels excluded). */
export function nextRegion(
  current: FocusRegion | null,
  direction: 1 | -1,
  available: readonly FocusRegion[] = FOCUS_REGIONS,
): FocusRegion | null {
  const cycle = FOCUS_REGIONS.filter((region) => available.includes(region));
  if (cycle.length === 0) return null;
  const index = current === null ? -1 : cycle.indexOf(current);
  if (index === -1) return direction === 1 ? cycle[0] : cycle[cycle.length - 1];
  return cycle[(index + direction + cycle.length) % cycle.length];
}

/** CSS selector for a region's primary focus target. */
export function regionTargetSelector(region: FocusRegion): string {
  return `[data-region="${region}"] [data-focus-target]`;
}

/** The stage header — the composer-Esc and PTY-F6 exit landing (design §5.3). */
export const STAGE_HEADER_SELECTOR = '[data-region="stage"] [data-stage-header]';

/** The PTY host — the explicit Focus-terminal command's destination. */
export const PTY_HOST_SELECTOR = '[data-kbzone="pty"]';

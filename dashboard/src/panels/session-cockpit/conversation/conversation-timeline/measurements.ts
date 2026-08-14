// Measurement cache + intent/restore constants for the conversation timeline: sessionStorage
// geometry persistence, the premeasure batching policy, the trusted-input scroll-key set, and the
// bounded restore-driver budget. Pure data — no React.
import type { VirtualItem } from "@tanstack/react-virtual";

export const BOTTOM_FOLLOW_PX = 120;
// A moderate transcript is cheaper to measure during initial settling than to let estimates surface
// row-by-row under the operator's upward scroll. A sliding batch warms TanStack's measurement cache
// without ever retaining the whole rich transcript in the DOM; truly large histories keep the
// ordinary bounded virtual window.
export const INITIAL_PREMEASURE_MAX_ROWS = 200;
// Ten-ish rich Markdown/tool rows stay below a frame-sized task in the live 103-row transcript.
// Timer-separated slices keep the chat interactive while the measurement frontier advances upward.
export const INITIAL_PREMEASURE_BATCH_ROWS = 12;
// A delay is intentional: re-queuing requestIdleCallback from its own React commit collapsed all
// slices into one 304ms Chrome idle task in the live transcript. Separate tasks preserved paint and
// input opportunities while still warming the full 103-row page in well under a second.
export const INITIAL_PREMEASURE_SLICE_DELAY_MS = 24;
// Continuous browser/rail drags can emit a width on every frame. Let the ordinary ResizeObserver
// keep mounted rows fluid, then rebuild offscreen measurements once the width settles.
export const MEASUREMENT_RESIZE_SETTLE_MS = 160;
export const MEASUREMENT_CACHE_PREFIX = "cockpit.chats.measurements.v1:";

export interface StoredMeasurementCache {
  windowWidth: number;
  viewportWidth: number;
  items: VirtualItem[];
}

function measurementStorageKey(cacheId: string): string {
  return `${MEASUREMENT_CACHE_PREFIX}${encodeURIComponent(cacheId)}`;
}

export function readStoredMeasurements(cacheId: string | undefined): StoredMeasurementCache | null {
  if (cacheId === undefined || typeof window === "undefined") return null;
  try {
    const raw = window.sessionStorage.getItem(measurementStorageKey(cacheId));
    if (raw === null) return null;
    const parsed = JSON.parse(raw) as StoredMeasurementCache;
    if (
      parsed.windowWidth !== window.innerWidth ||
      !Number.isFinite(parsed.viewportWidth) ||
      !Array.isArray(parsed.items)
    ) {
      return null;
    }
    return parsed;
  } catch {
    // This is an optional performance cache, never conversation authority. Storage can be denied
    // or externally corrupted; losing the cache must fall back to ordinary measurement rather
    // than making the transcript unavailable.
    return null;
  }
}

export function storeMeasurements(
  cacheId: string | undefined,
  viewportWidth: number,
  items: VirtualItem[],
): void {
  if (cacheId === undefined || typeof window === "undefined" || viewportWidth <= 0) return;
  try {
    window.sessionStorage.setItem(
      measurementStorageKey(cacheId),
      JSON.stringify({ windowWidth: window.innerWidth, viewportWidth, items }),
    );
  } catch {
    // Same optional-cache boundary as the read path: quota/privacy failures may cost a future
    // warm-up, but must never alter the current conversation.
  }
}

// Keys that mean "the operator is scrolling this feed" for the trusted-input restore cancel
// (a programmatic clamp never carries input) — mirror of the feed's own keyboard scrolling.
// ArrowDown is deliberately ABSENT: on a non-empty roster the conversation surface hijacks it
// into the agents line, so it is no longer a scroll key here — PageUp/PageDown, [/] and the
// wheel remain the scroll paths. Exported for the surface keyboard-contract tests.
export const OPERATOR_SCROLL_KEYS = new Set([
  "Home",
  "End",
  "PageUp",
  "PageDown",
  "ArrowUp",
  " ",
  "[",
  "]",
]);

// Bound on the rAF restore driver (the re-measure window may produce no renders).
export const RESTORE_DRIVE_MAX_MS = 2_500;
// The atBottom restore consumes only after the feed's height holds stable for this many
// consecutive driver frames (~160ms — the measured re-measure recovery is ~40–55ms), so an
// estimate-degenerate frame can never finalize the wrong end.
export const RESTORE_SETTLE_FRAMES = 10;
// Genuine operator input marks the interaction window the intent lock consults; the flag
// decays this long after the last input (the JSONLogConsole model — covers a scrollbar drag's
// event stream: one pointerdown, then the drag's scroll events within the window).
export const INTERACTION_DECAY_MS = 500;

// The terminal "well": the conversation feed inherits the SAME dark inset the xterm pty
// pane uses (background: well, 1px grid border), so the structured stage stops reading as a generic
// web panel sharing the page background. Horizontal inset only (vertical scroll math stays clean for

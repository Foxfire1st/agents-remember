// The one navigable role="feed" (design §14.2, §14.3). Virtualized by stable conversation item (never
// by rendered line): DOM pruning is independent of the store/history authority. Each row is an
// <article> with aria-posinset from the server globalOrdinal and aria-setsize ONLY when totalItems is
// honestly known (else omitted; paging copy says "total unknown"). A roving tabindex + a focus-pinning
// range extractor (which also pins the DEFAULT tab row) keep a tabbable article mounted even
// when it scrolls out, so incoming data can never relocate focus to the container. Bottom-follow is
// owned by an INTENT lock (the JSONLogConsole model): only genuine operator input (wheel, touch,
// scroll keys, pointerdown — a 500ms decay window) or true bottom-arrival may flip it; content-driven
// scroll events (stream deltas, block finalization, clamp echoes, measurement corrections) NEVER do.
// While locked, every content growth re-pins the honest pixel end (total-height change, not just row
// appends); while unlocked the position holds exactly and the state-aware latest chip counts arrivals.
// Older paging preserves the top stable row + pixel offset. While unlocked, a MEASUREMENT
// anchor (first visible row + pixel offset within it) is re-imposed on every measurement commit: rows
// entering the window measure from estimates to real heights and shift every start below them, which
// the browser's own scroll anchoring tracks poorly for absolute-transform rows — the visible content
// used to jump while scrolling up. Keyboard
// navigation is handled on the feed widget itself (the ARIA feed pattern), not a global document
// handler (§14.4); its exclusion list is complete and Home/End are exempt inside labeled overflow
// regions so they scroll the region instead of navigating. Consecutive identical unknown-vendor
// evidence collapses to one expandable row while every other item keeps its own article.

import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import {
  defaultRangeExtractor,
  useVirtualizer,
  type Range,
  type VirtualItem,
} from "@tanstack/react-virtual";

import { css, cx } from "../../../../styled-system/css";
import type { ConversationScrollMemory } from "../../../data/conversation/store";
import type { ConversationItem } from "../../../data/conversation/types";
import { groupUnknownVendorRuns, type DisplayRow } from "./collapse";
import { ConversationItemView, itemAccessibleName } from "./ConversationItemView";

const BOTTOM_FOLLOW_PX = 120;
// A moderate transcript is cheaper to measure during initial settling than to let estimates surface
// row-by-row under the operator's upward scroll. A sliding batch warms TanStack's measurement cache
// without ever retaining the whole rich transcript in the DOM; truly large histories keep the
// ordinary bounded virtual window.
const INITIAL_PREMEASURE_MAX_ROWS = 200;
// Ten-ish rich Markdown/tool rows stay below a frame-sized task in the live 103-row transcript.
// Timer-separated slices keep the chat interactive while the measurement frontier advances upward.
const INITIAL_PREMEASURE_BATCH_ROWS = 12;
// A delay is intentional: re-queuing requestIdleCallback from its own React commit collapsed all
// slices into one 304ms Chrome idle task in the live transcript. Separate tasks preserved paint and
// input opportunities while still warming the full 103-row page in well under a second.
const INITIAL_PREMEASURE_SLICE_DELAY_MS = 24;
// Continuous browser/rail drags can emit a width on every frame. Let the ordinary ResizeObserver
// keep mounted rows fluid, then rebuild offscreen measurements once the width settles.
const MEASUREMENT_RESIZE_SETTLE_MS = 160;
const MEASUREMENT_CACHE_PREFIX = "cockpit.chats.measurements.v1:";

interface StoredMeasurementCache {
  windowWidth: number;
  viewportWidth: number;
  items: VirtualItem[];
}

function measurementStorageKey(cacheId: string): string {
  return `${MEASUREMENT_CACHE_PREFIX}${encodeURIComponent(cacheId)}`;
}

function readStoredMeasurements(cacheId: string | undefined): StoredMeasurementCache | null {
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

function storeMeasurements(
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
const OPERATOR_SCROLL_KEYS = new Set([
  "Home",
  "End",
  "PageUp",
  "PageDown",
  "ArrowUp",
  "ArrowDown",
  " ",
  "[",
  "]",
]);

// Bound on the rAF restore driver (the re-measure window may produce no renders).
const RESTORE_DRIVE_MAX_MS = 2_500;
// The atBottom restore consumes only after the feed's height holds stable for this many
// consecutive driver frames (~160ms — the measured re-measure recovery is ~40–55ms), so an
// estimate-degenerate frame can never finalize the wrong end.
const RESTORE_SETTLE_FRAMES = 10;
// Genuine operator input marks the interaction window the intent lock consults; the flag
// decays this long after the last input (the JSONLogConsole model — covers a scrollbar drag's
// event stream: one pointerdown, then the drag's scroll events within the window).
const INTERACTION_DECAY_MS = 500;

// The terminal "well": the conversation feed inherits the SAME dark inset the xterm pty
// pane uses (background: well, 1px grid border), so the structured stage stops reading as a generic
// web panel sharing the page background. Horizontal inset only (vertical scroll math stays clean for
// the virtualizer). Content spans the FULL well width like a real TUI (no centered max-width
// column); the 2ch inline padding is the deliberate terminal gutter, so text never sits flush
// against the well border.
const viewport = css({
  flex: "1",
  minHeight: "0",
  overflowY: "auto",
  overflowX: "hidden",
  position: "relative",
  outline: "none",
  background: "well",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "grid",
  borderRadius: "3px",
  paddingInline: "2ch",
});
const feedInner = css({ position: "relative", width: "100%" });
// The empty conversation remains inside the terminal well, but gets enough space to behave like
// a real TUI landing screen. It is ordinary flow content so short viewports can still scroll it.
const emptyInWell = css({
  minHeight: "100%",
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
  paddingBlock: "clamp(2rem, 7vh, 5rem)",
});
// Line-grid rhythm: no per-article hairline rule (neither Toad nor Claude Code rules between
// items); one blank line of breathing below each item instead of a boxed web-list divider.
const rowShell = css({
  position: "absolute",
  top: "0",
  left: "0",
  width: "100%",
  paddingBlockStart: "0.15rem",
  paddingBlockEnd: "0.9rem",
  _focusVisible: { outline: "1px solid token(colors.amber)", outlineOffset: "-1px" },
});
// The viewport's positioning shell. Absolutely-positioned children of a SCROLL container scroll
// with its content (verified live), so anything meant to FLOAT over the visible frame — the latest
// chip — is a child of this shell, a sibling of the scroller, never a child of it. The shell hugs
// the scroller exactly (flex column, the well keeps its own flex:1), so
// the overlay coordinates coincide with the visible viewport.
const viewportShell = css({
  position: "relative",
  display: "flex",
  flexDirection: "column",
  flex: "1",
  minHeight: "0",
  minWidth: "0",
});
// One state-aware route back to the live edge. It sits clear of the native scrollbar rather
// than sharing its hit corridor, and folds the old "N new updates" pill into the same action.
const latestChip = css({
  position: "absolute",
  right: "1.6rem",
  bottom: "0.65rem",
  display: "inline-flex",
  alignItems: "center",
  gap: "0.35rem",
  font: "inherit",
  fontSize: "0.68rem",
  lineHeight: "1",
  color: "ink",
  background: "bgPanel",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "grid",
  borderRadius: "3px",
  paddingInline: "0.5rem",
  minHeight: "1.75rem",
  whiteSpace: "nowrap",
  cursor: "pointer",
  boxShadow: "0 2px 8px oklch(0 0 0 / 0.32)",
  transition: "transform 120ms cubic-bezier(0.23, 1, 0.32, 1)",
  _hover: { color: "amber", borderColor: "amber" },
  _active: { transform: "scale(0.97)" },
  _focusVisible: { outline: "1px solid token(colors.amber)", outlineOffset: "1px" },
});
const latestChipWithUpdates = css({
  color: "amber",
  borderColor: "amber",
});
const olderBar = css({ display: "flex", justifyContent: "center", paddingBlock: "0.3rem" });
const olderButton = css({
  font: "inherit",
  fontSize: "0.66rem",
  color: "muted",
  background: "transparent",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "grid",
  borderRadius: "2px",
  paddingInline: "0.5rem",
  paddingBlock: "0.1rem",
  cursor: "pointer",
  _hover: { color: "amber", borderColor: "amber" },
  _focusVisible: { outline: "1px solid token(colors.amber)", outlineOffset: "1px" },
});
// The collapsed unknown-vendor run is a dim gutter line, not a boxed web row.
const runRow = css({ display: "grid", gap: "0.15rem", color: "dormant", fontSize: "0.7rem", fontFamily: "mono" });
const runHead = css({ display: "flex", gap: "0.4rem", alignItems: "baseline", minWidth: "0" });
const runSummary = css({ minWidth: "0", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" });
// The clamp control reads as a text affordance and NEVER wraps its own label.
const runButton = css({
  font: "inherit",
  fontSize: "0.7rem",
  flex: "none",
  whiteSpace: "nowrap",
  color: "muted",
  background: "transparent",
  border: "none",
  paddingInline: "0",
  cursor: "pointer",
  textDecoration: "underline",
  textDecorationColor: "color-mix(in oklch, token(colors.grid) 60%, transparent)",
  textUnderlineOffset: "2px",
  _hover: { color: "amber" },
  _focusVisible: { outline: "1px solid token(colors.amber)", outlineOffset: "1px" },
});
const runMember = css({ fontFamily: "mono", fontSize: "0.66rem", color: "dormant", overflowWrap: "anywhere", paddingInlineStart: "2ch" });

function isEditableTarget(target: HTMLElement): boolean {
  return (
    target.tagName === "INPUT" ||
    target.tagName === "TEXTAREA" ||
    target.tagName === "SELECT" ||
    target.isContentEditable ||
    target.closest("button,a,[contenteditable],.cm-editor") !== null
  );
}

function inOverflowRegion(target: HTMLElement): boolean {
  // Labeled code/diff/output scroll regions own Home/End so they scroll rather than navigate.
  return target.closest('[role="group"], pre') !== null;
}

function UnknownVendorRun({
  row,
  expanded,
  onToggle,
}: {
  row: Extract<DisplayRow, { kind: "unknown-run" }>;
  expanded: boolean;
  onToggle: () => void;
}) {
  return (
    <div className={runRow} data-testid="unknown-vendor-run">
      <div className={runHead}>
        <span
          className={runSummary}
          title={`${row.items.length} unknown vendor events (same summary) — ${row.summary}`}
        >
          {/* "same summary", not "identical": the events share this summary but each carries its
              own distinct evidence id (they are not duplicates), so the copy must not imply sameness. */}
          {row.items.length} unknown vendor events (same summary) — {row.summary}
        </span>
        <button
          type="button"
          className={runButton}
          aria-expanded={expanded}
          onClick={onToggle}
          data-testid="unknown-vendor-run-toggle"
        >
          {expanded ? "collapse" : "show each"}
        </button>
      </div>
      {expanded ? (
        <div>
          {row.items.map((item) => (
            <div className={runMember} key={item.itemId} data-testid="unknown-vendor-run-member">
              #{item.globalOrdinal} · {item.evidenceRef ?? item.itemId}
            </div>
          ))}
        </div>
      ) : null}
    </div>
  );
}

export interface ConversationTimelineProps {
  items: ConversationItem[];
  totalItems?: number;
  hasOlder: boolean;
  busy: boolean;
  /** Empty-conversation content rendered INSIDE the well (the well always renders). */
  emptyNote?: ReactNode;
  onLoadOlder: () => void;
  /** False only while the timeline has no layout box (view switch or library overlay).
      A keep-alive chat hidden with visibility:hidden still has honest geometry and stays true:
      changing focus must not arm a restore or write to its preserved scrollbar. */
  visible?: boolean;
  /** The session's remembered position at this render (undefined = first-ever open: arms
      an atBottom pending-restore so the chat lands at the CURRENT end once geometry is honest,
      never a one-shot jump against a display:none box). Read on re-show/mount, never reactive. */
  restoreScroll?: ConversationScrollMemory;
  /** Called from the scroll listener with the current position (cheap, no react state). */
  onScrollMemory?: (memory: ConversationScrollMemory) => void;
  /** Stable session identity for the disposable, sessionStorage-backed row-height cache. */
  measurementCacheId?: string;
}

export function ConversationTimeline({
  items,
  totalItems,
  hasOlder,
  busy,
  emptyNote,
  onLoadOlder,
  visible = true,
  restoreScroll,
  onScrollMemory,
  measurementCacheId,
}: ConversationTimelineProps) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const rows = useMemo<DisplayRow[]>(() => groupUnknownVendorRuns(items), [items]);
  // TanStack 3.17 can round-trip measured rows through initialMeasurementsCache. Persisting this
  // disposable UI geometry is the only way a browser refresh can know offscreen DOM heights
  // without rendering those rows again. sessionStorage keeps it tab-local; a window-width mismatch
  // rejects it immediately, and the exact viewport width is checked pre-paint on first reveal.
  const [storedMeasurementCache] = useState(() => readStoredMeasurements(measurementCacheId));
  const initialMeasurementsCache =
    storedMeasurementCache?.items ?? [];
  const hasCompleteInitialMeasurementCache = useMemo(() => {
    if (rows.length === 0 || initialMeasurementsCache.length < rows.length) return false;
    const cachedKeys = new Set(initialMeasurementsCache.map((item) => String(item.key)));
    return rows.every((row) => cachedKeys.has(row.key));
  }, [initialMeasurementsCache, rows]);
  const [focusedKey, setFocusedKey] = useState<string | null>(null);
  const [expandedRuns, setExpandedRuns] = useState<Set<string>>(() => new Set());
  const [pendingUpdates, setPendingUpdates] = useState(0);
  const [awayFromLatest, setAwayFromLatest] = useState(() => restoreScroll?.atBottom === false);
  // -1 waits across an empty hydration window; null means ineligible or consumed; otherwise this
  // is the first row in the ONE sliding premeasurement batch. Once consumed it never re-arms on
  // live appends, whose last row is already mounted by the ordinary bottom-follow range.
  const [premeasureBatchStart, setPremeasureBatchStart] = useState<number | null>(() =>
    rows.length === 0
      ? -1
      : rows.length <= INITIAL_PREMEASURE_MAX_ROWS && !hasCompleteInitialMeasurementCache
        ? Math.max(0, rows.length - INITIAL_PREMEASURE_BATCH_ROWS)
        : null,
  );
  const nearBottomRef = useRef(true);
  // Intent machine (the JSONLogConsole model): `userInteractionRef` is set ONLY by genuine input
  // (wheel, touchstart, pointerdown, scroll keys) and decays after INTERACTION_DECAY_MS;
  // `userOverrideRef` is the STICKY disengagement — set when a deliberate scroll leaves the bottom
  // band, cleared only by true bottom-arrival or the explicit latest control.
  const userInteractionRef = useRef(false);
  const userOverrideRef = useRef(false);
  // The in-band hold. A genuine-input scroll can park SHORT of the exact end while staying
  // inside the follow band (distance ≤ BOTTOM_FOLLOW_PX) — by design the lock stays engaged
  // (in-band = following), so `userOverrideRef` is NOT set. But the parked offset must survive the
  // next growth, and survive it past the 500ms interaction window — the observed gap was a delta
  // ~500ms after a 50px wheel-up snapping the operator back to the end once `userInteractionRef`
  // decayed. This flag is the DURABLE, input-only record that the operator is parked below the
  // live end; it clears the instant they reach the true end (distance 0) so the follow resumes.
  const bandHoldRef = useRef(false);
  const interactionResetTimeoutRef = useRef<number | null>(null);
  const prevLastKeyRef = useRef<string | null>(null);
  const prevFirstKeyRef = useRef<string | null>(null);
  const anchorRef = useRef<{ itemId: string; offsetPx: number } | null>(null);
  // Width invalidation can arrive mid-gesture. Only that remeasurement pass pauses for scroll-end;
  // the ordinary cold-start warm-up keeps its deterministic timer schedule.
  const premeasurePausesForScrollRef = useRef(false);
  // Measurement anchor (the upscroll-jank fix): the first VISIBLE row at the last scroll event
  // plus its `start` and the viewport top's pixel offset within it — the reference point the
  // measurement-commit effect below re-imposes while the lock is disengaged. Distinct from the
  // prepend anchor above (which tracks the first MOUNTED row for older paging).
  const measureAnchorRef = useRef<{ index: number; key: string; start: number; offsetWithinRow: number } | null>(null);
  const measurePrevFirstKeyRef = useRef<string | null>(null);
  // A restore armed at mount (when a position is remembered) and on every re-show, consumed
  // when applied. null = nothing to restore (the ordinary live-follow path).
  // A FRESH mount with nothing remembered arms an atBottom restore instead of the old one-shot
  // scrollToIndex jump — that jump fired against a display:none box (cockpit boots on Operations)
  // or estimate-degenerate measurements, no-oped, and never retried (the restart-opens-at-top bug).
  // The armed restore rides the restore machinery: pixel re-drive, settle frames, box-less stay-armed,
  // trusted-input cancel. Its scrollTop is 0 and unused — the atBottom branch re-drives the CURRENT
  // DOM end, and echo gate 1 (maxScroll + 1 < pending.scrollTop) can never trip on it.
  const pendingRestoreRef = useRef<ConversationScrollMemory | null>(restoreScroll ?? { scrollTop: 0, atBottom: true });
  const wasVisibleRef = useRef(visible);
  // Render-time mirror so the scroll listener can read the current visibility without
  // re-subscribing on every flip (the chatsLibraryOpenRef pattern in SessionsView).
  const visibleRef = useRef(visible);
  visibleRef.current = visible;
  // Race 1: the rAF restore driver's per-run state lives in a REF
  // keyed to the ARM, never in the effect closure — the closure state reset every live frame
  // (the driver effect subscribed to tryApplyPendingRestore, a rows-keyed callback, so each
  // stream delta tore it down and re-entered with startedAt/lastFeedHeight/settledFrames back
  // at zero). With deltas faster than a settle window neither RESTORE_SETTLE_FRAMES nor
  // RESTORE_DRIVE_MAX_MS could ever trip: the restore rode a whole streaming turn armed,
  // disabling the follow, the measurement anchor, and the update affordance. Reset on (re-)arm below.
  const restoreDriveRef = useRef({ startedAt: 0, lastFeedHeight: -1, settledFrames: 0 });

  const focusedIndex = useMemo(
    () => (focusedKey === null ? -1 : rows.findIndex((row) => row.key === focusedKey)),
    [rows, focusedKey],
  );

  // Pin the focused row AND the default-tab row (the last row) so a tabbable article always exists,
  // even when both scroll out of the window (§14.3).
  const rangeExtractor = useCallback(
    (range: Range) => {
      const base = new Set(defaultRangeExtractor(range));
      if (visible && premeasureBatchStart !== null && premeasureBatchStart >= 0) {
        const batchStart = Math.min(premeasureBatchStart, rows.length);
        const batchEnd = Math.min(rows.length, batchStart + INITIAL_PREMEASURE_BATCH_ROWS);
        for (let index = batchStart; index < batchEnd; index += 1) {
          base.add(index);
        }
      }
      if (focusedIndex >= 0) base.add(focusedIndex);
      if (rows.length > 0) base.add(rows.length - 1);
      return [...base].sort((a, b) => a - b);
    },
    [focusedIndex, premeasureBatchStart, rows.length, visible],
  );

  const virtualizer = useVirtualizer({
    count: rows.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => 80,
    overscan: 8,
    getItemKey: (index) => rows[index]?.key ?? index,
    rangeExtractor,
    initialMeasurementsCache,
  });

  const virtualRows = virtualizer.getVirtualItems();
  // `scrollRect` is honestly `Rect | null` — TanStack backfills it from `initialRect` inside
  // getSize(), never on the field, so nothing here may assume an observed rect exists. Null means
  // "not observed yet", which is the same non-signal as 0: this value is ONLY a re-run trigger for
  // the width-invalidation effect below, which reads the element's own clientWidth and bails on
  // clientWidth <= 0. Reading it unguarded made `tsc -b` (strict) exit 2 and blocked `vite build`.
  const observedViewportWidth = virtualizer.scrollRect?.width ?? 0;
  const virtualizerIsScrolling = virtualizer.isScrolling;
  // The follow-on-growth signals. totalSize moves on every append-block delta (the last row
  // grows in place) and on every re-measure; lastRowSize is the last row's MEASURED size (the
  // range extractor pins the last row, so it is always mounted) — the streamed-update signal.
  const totalSize = virtualizer.getTotalSize();
  const lastVirtualRow = virtualRows.length > 0 ? virtualRows[virtualRows.length - 1] : undefined;
  const lastRowSize =
    lastVirtualRow !== undefined && lastVirtualRow.index === rows.length - 1 ? lastVirtualRow.size : undefined;

  // An initially empty projection decides eligibility when its first page arrives. If that page
  // crossed the moderate bound while hydrating, it stays on the ordinary large-history path.
  useEffect(() => {
    if (premeasureBatchStart !== -1 || rows.length === 0) return;
    setPremeasureBatchStart(
      rows.length <= INITIAL_PREMEASURE_MAX_ROWS && !hasCompleteInitialMeasurementCache
        ? Math.max(0, rows.length - INITIAL_PREMEASURE_BATCH_ROWS)
        : null,
    );
  }, [hasCompleteInitialMeasurementCache, premeasureBatchStart, rows.length]);

  // This pass owns these measurements, so record the mounted batch synchronously instead of
  // depending on ref/ResizeObserver delivery. TanStack deliberately skips sync ref measurement
  // during user scrolling; a width refresh that began near scroll-end could otherwise miss a few
  // rows and stall forever on the final batch.
  useLayoutEffect(() => {
    if (
      !visible ||
      (premeasurePausesForScrollRef.current && virtualizerIsScrolling) ||
      premeasureBatchStart === null ||
      premeasureBatchStart < 0 ||
      rows.length === 0
    ) {
      return;
    }
    const el = scrollRef.current;
    if (el === null) return;
    const batchStart = Math.min(premeasureBatchStart, rows.length);
    const batchEnd = Math.min(rows.length, batchStart + INITIAL_PREMEASURE_BATCH_ROWS);
    for (let index = batchStart; index < batchEnd; index += 1) {
      const rowElement = el.querySelector<HTMLElement>(`[data-index="${index}"]`);
      if (rowElement !== null) virtualizer.resizeItem(index, rowElement.offsetHeight);
    }
  }, [
    premeasureBatchStart,
    rows.length,
    virtualizer,
    virtualizerIsScrolling,
    visible,
  ]);

  // Replace one batch with the next older batch in timer-separated slices. Previously the range
  // accumulated every warmed row, so the final slice still mounted all 103 rich rows and caused a
  // measured 227–309ms task. Keeping only one batch lets React unmount each slice after TanStack has
  // retained its measured size, and yields back to the browser before the next slice. A display:none
  // keep-alive has no measurable geometry, so it must wait until the Chats layer is visible.
  useEffect(() => {
    if (
      !visible ||
      (premeasurePausesForScrollRef.current && virtualizerIsScrolling) ||
      premeasureBatchStart === null ||
      premeasureBatchStart <= 0 ||
      rows.length === 0
    ) {
      return undefined;
    }
    const advance = () => {
      setPremeasureBatchStart((current) =>
        current === null || current < 0
          ? current
          : Math.max(0, current - INITIAL_PREMEASURE_BATCH_ROWS),
      );
    };
    const timeoutId = window.setTimeout(advance, INITIAL_PREMEASURE_SLICE_DELAY_MS);
    return () => window.clearTimeout(timeoutId);
  }, [premeasureBatchStart, rows.length, virtualizerIsScrolling, visible]);

  // TanStack's documented snapshot contains only rows that have actually rendered and measured.
  // Once the sliding batch reached the start and the snapshot covers the page, remove the extra
  // range: upward scrolling now reads stable sizes while the DOM stays bounded.
  useLayoutEffect(() => {
    if (!visible || premeasureBatchStart !== 0 || rows.length === 0) return;
    const snapshot = virtualizer.takeSnapshot();
    if (snapshot.length < rows.length) return;
    const el = scrollRef.current;
    if (el !== null) storeMeasurements(measurementCacheId, el.clientWidth, snapshot);
    premeasurePausesForScrollRef.current = false;
    setPremeasureBatchStart(null);
  }, [measurementCacheId, premeasureBatchStart, rows.length, totalSize, virtualizer, visible]);

  // A measured height is width-specific because Markdown wrapping determines row height. The
  // window-width check above cheaply rejects a stale cache before mount; this pre-paint check
  // catches both layout-only width changes (rail/inspector sizing) and later browser resizes. A
  // mismatch clears TanStack's sizes and runs the same bounded warm-up used for a cold page.
  const measurementWidthRef = useRef<number | null>(null);
  useLayoutEffect(() => {
    if (!visible || rows.length === 0) return;
    const el = scrollRef.current;
    if (el === null || el.clientWidth <= 0) return;
    const previousWidth = measurementWidthRef.current;
    measurementWidthRef.current = el.clientWidth;
    const invalidateMeasurements = (viewportElement: HTMLDivElement) => {
      premeasurePausesForScrollRef.current = true;
      virtualizer.measure();
      // measure() clears the size cache but does not remount rows already in the ordinary virtual
      // window. Record those connected nodes directly (measureElement deliberately skips synchronous
      // reads during user scrolling); the sliding batches cover every unmounted row after scroll-end.
      for (const rowElement of viewportElement.querySelectorAll<HTMLElement>("[data-conversation-item]")) {
        const index = Number(rowElement.dataset.index);
        virtualizer.resizeItem(index, rowElement.offsetHeight);
      }
      setPremeasureBatchStart(
        rows.length <= INITIAL_PREMEASURE_MAX_ROWS
          ? Math.max(0, rows.length - INITIAL_PREMEASURE_BATCH_ROWS)
          : null,
      );
    };
    if (previousWidth === null) {
      if (
        initialMeasurementsCache.length === 0 ||
        storedMeasurementCache?.viewportWidth === el.clientWidth
      ) {
        return;
      }
      // A stale persisted cache must be gone before the first visible paint.
      invalidateMeasurements(el);
      return;
    } else if (previousWidth === el.clientWidth) {
      return;
    }
    const settledWidth = el.clientWidth;
    const timeoutId = window.setTimeout(() => {
      const current = scrollRef.current;
      if (current !== null && current.clientWidth === settledWidth) invalidateMeasurements(current);
    }, MEASUREMENT_RESIZE_SETTLE_MS);
    return () => window.clearTimeout(timeoutId);
  }, [
    initialMeasurementsCache.length,
    observedViewportWidth,
    rows.length,
    storedMeasurementCache?.viewportWidth,
    virtualizer,
    visible,
  ]);

  // Measurement anchor: record the row containing the viewport top (event-time accuracy for the
  // measurement-commit effect below). getVirtualItemForOffset reads the virtualizer's live
  // measurements, so the recording stays consistent even between commits.
  const recordMeasureAnchor = useCallback(
    (el: HTMLDivElement) => {
      const topItem = virtualizer.getVirtualItemForOffset(el.scrollTop);
      if (topItem === undefined) return;
      const row = rows[topItem.index];
      if (row === undefined) return;
      measureAnchorRef.current = {
        index: topItem.index,
        key: row.key,
        start: topItem.start,
        offsetWithinRow: el.scrollTop - topItem.start,
      };
    },
    [virtualizer, rows],
  );

  const handleScroll = useCallback(() => {
    const el = scrollRef.current;
    if (el === null) return;
    // A hidden or box-less timeline has NO honest scroll geometry. The cockpit's display:none
    // teardown collapses the box (st=0/sh=0/ch=0) and the browser CAN dispatch a scroll event for
    // the clamp (verified live via Playwright) — reporting it clobbered the remembered position,
    // so the re-show restored 0 and the chat stayed at the START. While hidden, only the restore
    // owns the offset; nothing here (position, near-bottom, anchor) may update.
    if (!visibleRef.current || el.clientHeight === 0) return;
    // Echo gates: the collapse's clamp event is dispatched LATE
    // (~0.5s), with the box already restored (clientHeight>0 — passes the guard above) but the
    // feed transiently collapsed (st=0, sh≈ch). It must neither report nor cancel.
    // Gate 1 (scoped to the ARMED restore): a geometry that cannot contain the armed offset
    // is an environmental clamp. It is deliberately NOT applied to ordinary events: a viewport
    // settle that lowers maxScroll (ch 722→741) would otherwise freeze the memory at a
    // stale-oversized reference forever — an honest clamp event simply
    // re-baselines the memory instead.
    // Gate 2: a DOM scrollHeight far below the virtualizer's own measured total (which survives
    // display:none and tracks legitimate shrinks) is the collapsed feed echoing. A genuinely
    // short conversation (sh ≤ ch from the start) can never trip either.
    const maxScroll = el.scrollHeight - el.clientHeight;
    if (pendingRestoreRef.current !== null && maxScroll + 1 < pendingRestoreRef.current.scrollTop) {
      return;
    }
    if (el.scrollHeight + 1 < virtualizer.getTotalSize() - el.clientHeight) return;
    // An accepted scroll event with a restore still armed is the operator's own scroll landing —
    // they win, UNLESS the armed restore is the atBottom drive: that drive re-sets the end
    // itself every frame while armed, so an event already AT the bottom is its own, not the
    // operator's. (Trusted input — wheel/touch/scroll-keys — also cancels either way, below.)
    if (pendingRestoreRef.current !== null) {
      const pending = pendingRestoreRef.current;
      const distanceNow = el.scrollHeight - el.scrollTop - el.clientHeight;
      if (!(pending.atBottom && distanceNow <= BOTTOM_FOLLOW_PX)) {
        pendingRestoreRef.current = null;
      }
    }
    const distance = el.scrollHeight - el.scrollTop - el.clientHeight;
    const isNearBottom = distance <= BOTTOM_FOLLOW_PX;
    setAwayFromLatest(!isNearBottom);
    // Intent machine (the JSONLogConsole model): the lock changes ONLY on genuine input or true
    // bottom-arrival. A deliberate scroll-up disengages and STAYS disengaged (sticky override) —
    // content-driven events (stream deltas, block finalization, clamp echoes, measurement
    // corrections) carry no input and flip it NEITHER way; in particular a content-driven event
    // landing AT the bottom never clears a standing override. A genuine scroll all the way down
    // re-engages fully (follow + the latest chip's update count clears). The memory report stays honest:
    // atBottom persists the intent lock, not raw geometry.
    if (isNearBottom) {
      if (!userOverrideRef.current || userInteractionRef.current) {
        nearBottomRef.current = true;
        userOverrideRef.current = false;
        setPendingUpdates(0);
      }
      // Arm/disarm the durable in-band hold. Genuine input that parks short of the exact end
      // (distance > 0) arms it — the follow must then hold this offset even after the interaction
      // window decays. Reaching the true end (distance 0) disarms it so the follow resumes. A
      // content-driven event carries no input, so it never arms the hold and (because nothing
      // writes scrollTop while the hold is engaged) only the operator's own scroll ever reaches
      // distance 0 to clear it.
      if (userInteractionRef.current && distance > 0) {
        bandHoldRef.current = true;
      } else if (distance === 0) {
        bandHoldRef.current = false;
      }
    } else if (userInteractionRef.current || userOverrideRef.current) {
      nearBottomRef.current = false;
      userOverrideRef.current = true;
      // Crossed clear of the band — the sticky override owns the hold now; retire the in-band flag
      // so a later return to the true end re-engages the follow cleanly.
      bandHoldRef.current = false;
    }
    // Remember the position continuously — the re-show restores the last value reported
    // while visible (any event from the hidden/box-less phase is dropped by the guard above).
    const memory = { scrollTop: el.scrollTop, atBottom: nearBottomRef.current };
    onScrollMemory?.(memory);
    const first = virtualizer.getVirtualItems()[0];
    if (first !== undefined) {
      const row = rows[first.index];
      if (row !== undefined) {
        // The timeline's OWN prepend anchor (first mounted row + offset) — consumed by the
        // older-paging preservation effect below. It never leaves this component: the dead
        // store-mirror of this anchor (per-scroll-event projection rebuild) was the re-render
        // half of the scroll-up trap and is deleted.
        const offsetPx = first.start - el.scrollTop;
        anchorRef.current = { itemId: row.key, offsetPx };
      }
    }
    recordMeasureAnchor(el);
  }, [rows, onScrollMemory, recordMeasureAnchor, virtualizer]);

  useLayoutEffect(() => {
    const lastKey = rows.length > 0 ? rows[rows.length - 1].key : null;
    if (lastKey !== prevLastKeyRef.current) {
      if (prevLastKeyRef.current === null) {
        // First content on a FRESH mount (first open, or the keep-alive pool's first mount of this
        // session): the armed atBottom pending-restore (mount arm) routes this,
        // so the one-shot jump below is only a fallback for a mount whose restore was already
        // stood down (the operator's trusted input beat the first content). This runs pre-paint,
        // so it is an alignment, never a visible jump; afterwards the ordinary near-bottom follow
        // below takes over. (Before this, a fresh mount with an already-populated page rendered at
        // the TOP and waited for the next live event to yank the view to the end — the visible
        // half of the observed glitch.)
        // Skipped when a remembered position is pending — the restore below then owns the
        // initial offset (a re-shown chat is not a first open).
        if (lastKey !== null && pendingRestoreRef.current === null) virtualizer.scrollToIndex(rows.length - 1, { align: "end" });
      } else if (nearBottomRef.current && !bandHoldRef.current) {
        // Never fire the follow while the feed is box-less (the display:none
        // phases) — degenerate targets only arm 5s tanstack reconciles that race the re-show
        // restore, which owns the landing. A visibility-hidden keep-alive chat (layout alive,
        // clientHeight>0) keeps following quietly.
        // An operator parked inside the band (bandHoldRef) is NOT yanked to the end by a new
        // row either — the same in-band hold the streaming-delta follow honors. The arrival counts
        // into the pill instead, so a small deliberate scroll-up survives new items, not just
        // in-place growth.
        const el = scrollRef.current;
        if (el !== null && el.clientHeight > 0) virtualizer.scrollToIndex(rows.length - 1, { align: "end" });
      } else {
        setPendingUpdates((count) => count + 1);
      }
    }
    prevLastKeyRef.current = lastKey;
  }, [rows, virtualizer]);

  // Follow-on-growth (streaming): while the intent lock is engaged, re-pin to the CURRENT end
  // on every content change — a TOTAL-HEIGHT change (every append-block delta grows the last row
  // in place; re-measurements move the end) and on rows refresh. Direct pixel write
  // (scrollToIndex computes degenerate targets against estimates). Gated on the lock, an
  // honest box, and NO armed restore — it never fights the restore drive or the hidden phases —
  // and idempotent: already at the end is a silent no-op (no scroll event).
  // The scroll-up trap: NEVER re-pin while the operator's landing is
  // displaced from the end. A wheel-up that is still inside the bottom band leaves the lock
  // engaged by design, so the next growth commit used to undo it on the spot — one 50px wheel-up
  // snapped straight back to the end. The hold is owned by `bandHoldRef` (a DURABLE, input-
  // only intent set in handleScroll), not by `userInteractionRef` alone — the earlier guard keyed
  // only on the 500ms interaction window, so a delta arriving after the decay retook an in-band
  // displacement. `userInteractionRef` is kept in the union for the transient case where genuine
  // input has fired but its scroll event has not yet recorded the offset. A gesture parked AT the
  // end (distance 0 — the operator wheeled back down, clearing bandHoldRef, or a re-measure
  // compensation landed on the pinned end) keeps the follow as the owner of the end, so a growth
  // above the viewport can never displace the pinned bottom (the upscroll-anchor suite's contract,
  // and the bottom-follow contract).
  useLayoutEffect(() => {
    if (!nearBottomRef.current) return;
    if (pendingRestoreRef.current !== null) return;
    const el = scrollRef.current;
    if (el === null || el.clientHeight === 0) return;
    if ((userInteractionRef.current || bandHoldRef.current) && el.scrollHeight - el.scrollTop - el.clientHeight > 0) return;
    el.scrollTop = el.scrollHeight - el.clientHeight;
  }, [rows, totalSize]);

  // The latest chip also counts STREAMED arrivals — an append delta grows the last
  // row IN PLACE (no key change, so the row-key effect above never sees it). Count only a growth
  // of the last row's MEASURED size against an adopted baseline: the first measurement after a
  // key change (mount, arrival correction) is adopted, never counted; shrinks (block
  // finalization) are not arrivals; while locked the growth re-pin above already kept the end in
  // view, so there is nothing to report.
  const streamBaselineRef = useRef<{ key: string | null; size: number | undefined }>({ key: null, size: undefined });
  useLayoutEffect(() => {
    const lastKey = rows.length > 0 ? rows[rows.length - 1].key : null;
    const prev = streamBaselineRef.current;
    if (lastKey !== prev.key) {
      streamBaselineRef.current = { key: lastKey, size: undefined };
      return;
    }
    const prevSize = prev.size;
    streamBaselineRef.current = { key: lastKey, size: lastRowSize };
    if (prevSize === undefined || lastRowSize === undefined || lastRowSize <= prevSize) return;
    if (nearBottomRef.current) return;
    setPendingUpdates((count) => count + 1);
  }, [rows, lastRowSize]);

  // Switching cockpit tabs must not reopen the chat at the START: the
  // cockpit's Chats layer hides with display:none, which destroys the scroll offset (unlike the
  // keep-alive pool's visibility:hidden, which preserves it across chat switches). The re-show
  // arms a restore from the remembered position. With nothing remembered, a STILL-armed restore
  // (the mount arm that never got to consume — e.g. the chat was hidden its whole life) rides
  // again instead of being dropped; an already-consumed or operator-canceled arm stays null, so a
  // memory-less re-show never re-aligns.
  useLayoutEffect(() => {
    const becameVisible = !wasVisibleRef.current && visible;
    wasVisibleRef.current = visible;
    if (becameVisible) {
      pendingRestoreRef.current = restoreScroll ?? pendingRestoreRef.current;
      // A (re-)armed restore starts a FRESH driver run (budget + settle frames) — the counters
      // live in restoreDriveRef so live-frame churn can never reset them mid-run (Race 1).
      restoreDriveRef.current = { startedAt: 0, lastFeedHeight: -1, settledFrames: 0 };
    }
  }, [visible, restoreScroll]);

  // The restore applies ONLY against honest geometry, and stays armed until it does. On a
  // real-browser re-show the virtualizer re-measures across a multi-frame window (Playwright: the
  // feed height reads 0px at +2ms, the true scrollHeight recovers ~54ms later) — a one-shot apply
  // in the re-show commit is clamped to 0 by the browser and nothing re-applies. Shared by the
  // render-driven pass below and the bounded rAF driver further down.
  const tryApplyPendingRestore = useCallback(() => {
    const pending = pendingRestoreRef.current;
    if (pending === null || !visibleRef.current || rows.length === 0) return;
    const el = scrollRef.current;
    if (el === null) return;
    if (pending.atBottom) {
      // Left at the bottom: re-drive the CURRENT DOM end on every pass while armed — never
      // consuming here. A single-shot scrollToIndex(end) fired against
      // estimate-degenerate measurements computed target 0, consumed, and tanstack's reconcile
      // cleared after ONE stable frame — the chat settled at 0 while the feed recovered 40ms
      // later. The pixel re-drive tracks the recovering feed (and inflow that arrived while
      // away); the rAF driver consumes once the feed's height SETTLES. The follow re-arms via the
      // drive's own scroll events (they land at the bottom, distance 0).
      if (el.clientHeight === 0) return; // box-less: stay armed
      // True bottom-arrival: the lock re-engages fully (intent machine) and any sticky override
      // clears — a chat left at the bottom re-opens following the end. The in-band hold clears
      // too (the restore lands the operator ON the end, nothing to hold below it).
      nearBottomRef.current = true;
      userOverrideRef.current = false;
      bandHoldRef.current = false;
      setPendingUpdates(0);
      setAwayFromLatest(false);
      el.scrollTop = el.scrollHeight - el.clientHeight;
      return;
    }
    // Left mid-conversation or at the very top: restore the exact pixel offset — but only once
    // the scrollable extent honestly CONTAINS it (content measured a full viewport beyond the
    // offset), so the browser cannot clamp the write. Items appended while away never shift
    // earlier rows, and the already-counting latest chip keeps covering what flowed in.
    const maxScroll = el.scrollHeight - el.clientHeight;
    if (el.clientHeight === 0 || maxScroll < pending.scrollTop - 1) return; // stay armed
    pendingRestoreRef.current = null;
    // A remembered non-bottom position disengages the lock with the sticky override set —
    // the restored position holds exactly, arrivals count into the latest chip, and only a genuine
    // scroll back to the bottom (or an explicit control) re-engages the follow.
    nearBottomRef.current = false;
    userOverrideRef.current = true;
    setAwayFromLatest(true);
    el.scrollTop = pending.scrollTop;
  }, [rows]);

  // Render-driven attempt: free on every render pass (the virtualizer re-renders as it
  // re-measures). Bounds: no timers — attempts are O(1) geometry checks; the operator's trusted
  // input cancels the armed restore (wheel/touch/scroll-keys, capture-phase below); a never-ready
  // geometry (content shrank while away) leaves it armed but inert until the next hide/show.
  useLayoutEffect(() => {
    tryApplyPendingRestore();
  });

  // Latest-apply bridge for the rAF driver: tryApplyPendingRestore is rows-keyed, so its identity
  // churns on every live frame. The driver reads the CURRENT apply through this ref instead of
  // subscribing to the callback (render-time ref write, the visibleRef pattern above).
  const tryApplyRef = useRef(tryApplyPendingRestore);
  tryApplyRef.current = tryApplyPendingRestore;

  // The re-measure window may produce NO renders at all (TanStack's measured sizes
  // survive display:none, so nothing setStates — the render-driven pass stays "armed but inert").
  // While armed, a bounded rAF driver retries the same gated apply frame-by-frame so the restore
  // still lands the moment the geometry honestly contains it; past the bound it stands down (the
  // memory itself is kept for the next hide/show). For an atBottom restore (the pixel re-drive)
  // the driver ALSO owns consumption: it consumes only once the feed's scrollHeight has held
  // stable for RESTORE_SETTLE_FRAMES consecutive frames (measurements caught up), so an
  // estimate-degenerate frame can never finalize the wrong end — and it keeps driving through
  // inflow until then.
  // Race 1: the driver's run state is restoreDriveRef (reset only on
  // (re-)arm), and the effect keys on [visible] + content-presence — the arm events (mount, the
  // becameVisible flip above) always coincide with a [visible] entry, and the rowsEmpty flip
  // starts the run when an armed mount restore first gets content. A rows/rows.length/apply-
  // identity dep would re-subscribe mid-run on every live frame and reset the counters.
  const rowsEmpty = rows.length === 0;
  useEffect(() => {
    if (!visible || rowsEmpty || pendingRestoreRef.current === null) return undefined;
    const drive = restoreDriveRef.current;
    // The budget opens when a run can first drive, not at arm time (an armed mount restore may
    // wait out a zero-content window or a box-less phase before its first honest frame).
    if (drive.startedAt === 0) drive.startedAt = Date.now();
    let rafId = 0;
    const attempt = () => {
      const pending = pendingRestoreRef.current;
      if (pending === null) return; // applied or canceled — stop
      if (Date.now() - drive.startedAt > RESTORE_DRIVE_MAX_MS) {
        pendingRestoreRef.current = null;
        return;
      }
      tryApplyRef.current();
      if (pendingRestoreRef.current === null) return; // pixel branch consumed
      if (!pending.atBottom) {
        rafId = requestAnimationFrame(attempt);
        return;
      }
      const el = scrollRef.current;
      const height = el !== null && el.clientHeight > 0 ? el.scrollHeight : -1;
      if (height === drive.lastFeedHeight) {
        drive.settledFrames += 1;
        if (drive.settledFrames >= RESTORE_SETTLE_FRAMES) {
          pendingRestoreRef.current = null;
          return;
        }
      } else {
        drive.settledFrames = 0;
        drive.lastFeedHeight = height;
      }
      rafId = requestAnimationFrame(attempt);
    };
    rafId = requestAnimationFrame(attempt);
    return () => cancelAnimationFrame(rafId);
  }, [visible, rowsEmpty]);

  useLayoutEffect(() => {
    const firstKey = rows.length > 0 ? rows[0].key : null;
    const anchor = anchorRef.current;
    if (firstKey !== prevFirstKeyRef.current && prevFirstKeyRef.current !== null && anchor !== null) {
      const anchorIndex = rows.findIndex((row) => row.key === anchor.itemId);
      if (anchorIndex >= 0) virtualizer.scrollToIndex(anchorIndex, { align: "start" });
    }
    prevFirstKeyRef.current = firstKey;
  }, [rows, virtualizer]);

  // Anchor preservation on measurement commits (the upscroll jank, JSONLogConsole lineage):
  // while the intent lock is DISENGAGED, rows entering the window measure from estimate to real
  // height and shift every start below them. The virtualizer's own adjustment covers only
  // above-viewport FIRST measurements — it skips re-measurements during a backward scroll and rows
  // measured while visible — and the browser's scroll anchoring tracks absolute-transform rows
  // poorly, so the visible content jumped while scrolling up. On every commit this effect
  // re-imposes the recorded anchor: if the anchor row's start moved since the last scroll event
  // (only measurements move starts), the SAME delta is added to scrollTop in this layout phase,
  // before paint. Delta-add (never an absolute target) so a concurrent operator scroll or
  // programmatic navigation is preserved, not yanked back; the drift check skips commits where the
  // position is already consistent (the virtualizer's own adjustment or the browser already
  // compensated). The write is a direct pixel write — it carries no input, so the intent refs never
  // flip (the echo scroll event is content-driven by construction). Gates mirror every other
  // writer: never while locked (the follow re-pin owns the end), never while a restore is armed
  // (the restore owns the offset), never on a prepend commit (the older-paging anchor above owns
  // the landing), never on a hidden/box-less feed.
  useLayoutEffect(() => {
    const firstKey = rows.length > 0 ? rows[0].key : null;
    const prepended = measurePrevFirstKeyRef.current !== null && firstKey !== measurePrevFirstKeyRef.current;
    measurePrevFirstKeyRef.current = firstKey;
    if (prepended) {
      measureAnchorRef.current = null;
      return;
    }
    const anchor = measureAnchorRef.current;
    if (anchor === null) return;
    if (nearBottomRef.current || pendingRestoreRef.current !== null) return;
    const el = scrollRef.current;
    if (el === null || !visibleRef.current || el.clientHeight === 0) return;
    if (anchor.index >= rows.length || rows[anchor.index].key !== anchor.key) {
      measureAnchorRef.current = null;
      return;
    }
    // The anchor compensates the viewport the operator is looking at; if the offset has moved more
    // than ~2 viewports away from it, an explicit navigation outran the scroll event — drop the
    // anchor instead of injecting a proxy shift at a distant position (the next event re-records).
    if (Math.abs(el.scrollTop - anchor.start) > el.clientHeight * 2) {
      measureAnchorRef.current = null;
      return;
    }
    const current = virtualRows.find((virtualRow) => virtualRow.key === anchor.key);
    if (current === undefined) return; // outside the mounted window this commit — retry the next one
    const shift = current.start - anchor.start;
    anchor.start = current.start; // absorb: each commit compensates only the NEW shift
    if (shift === 0) return;
    const expected = current.start + anchor.offsetWithinRow;
    if (Math.abs(expected - el.scrollTop) < 1) return; // already compensated elsewhere
    const maxScroll = el.scrollHeight - el.clientHeight;
    el.scrollTop = Math.max(0, Math.min(el.scrollTop + shift, maxScroll));
  });

  const focusRowByIndex = useCallback(
    (index: number) => {
      if (index < 0 || index >= rows.length) return;
      const row = rows[index];
      setFocusedKey(row.key);
      virtualizer.scrollToIndex(index, { align: "auto" });
      requestAnimationFrame(() => {
        scrollRef.current
          ?.querySelector<HTMLElement>(`[data-row-key="${CSS.escape(row.key)}"]`)
          ?.focus();
      });
    },
    [rows, virtualizer],
  );

  // Latest chip — the single explicit route back to the live edge. The operator wins over any
  // armed restore, the sticky override clears, and the honest pixel end is pinned immediately.
  const scrollToLatest = useCallback(() => {
    if (rows.length === 0) return;
    pendingRestoreRef.current = null;
    userOverrideRef.current = false;
    // An explicit jump to the live end retires any parked in-band hold, so the follow re-pins
    // subsequent growth instead of holding the pre-jump offset.
    bandHoldRef.current = false;
    nearBottomRef.current = true;
    setPendingUpdates(0);
    setAwayFromLatest(false);
    virtualizer.scrollToIndex(rows.length - 1, { align: "end" });
    // scrollToIndex computes estimate-degenerate targets — pin the honest pixel end.
    const el = scrollRef.current;
    if (el !== null && el.clientHeight > 0) el.scrollTop = el.scrollHeight - el.clientHeight;
  }, [rows.length, virtualizer]);

  const onKeyDown = useCallback(
    (event: React.KeyboardEvent<HTMLDivElement>) => {
      const target = event.target as HTMLElement;
      if (isEditableTarget(target)) return;
      const current = focusedIndex < 0 ? rows.length - 1 : focusedIndex;
      switch (event.key) {
        case "]":
          event.preventDefault();
          focusRowByIndex(Math.min(rows.length - 1, current + 1));
          break;
        case "[":
          event.preventDefault();
          focusRowByIndex(Math.max(0, current - 1));
          break;
        case "Home":
          // A labeled overflow region or an active text selection owns Home/End (do not hijack).
          if (inOverflowRegion(target) || !(window.getSelection()?.isCollapsed ?? true)) return;
          event.preventDefault();
          focusRowByIndex(0);
          break;
        case "End":
          if (inOverflowRegion(target) || !(window.getSelection()?.isCollapsed ?? true)) return;
          event.preventDefault();
          focusRowByIndex(rows.length - 1);
          break;
        default:
          break;
      }
    },
    [focusedIndex, focusRowByIndex, rows.length],
  );

  useEffect(() => {
    const el = scrollRef.current;
    if (el === null) return undefined;
    el.addEventListener("scroll", handleScroll, { passive: true });
    return () => {
      el.removeEventListener("scroll", handleScroll);
    };
  }, [handleScroll]);

  useEffect(() => {
    const el = scrollRef.current;
    if (el === null) return undefined;
    // The operator's TRUSTED input stands an armed restore down (a programmatic
    // clamp never carries input, and the echo gates in handleScroll also close the scroll-event
    // path during the partial-geometry window). Capture-phase, passive — never intercepts.
    // The same genuine input opens the intent lock's interaction window (500ms decay). The
    // set is wheel, touchstart, scroll keys, AND pointerdown — pointerdown is what unsticks a
    // scrollbar drag from the restore drive (a drag never wheels, touches, or keys).
    // This effect is deliberately SEPARATE from the scroll-listener effect above and mounts ONCE:
    // that effect re-subscribes on every rows change (handleScroll is a rows-keyed callback), and
    // its cleanup would otherwise clear the decay timeout mid-window — the interaction flag would
    // never decay while streaming, and a content-driven event at the bottom could re-engage.
    const markUserInteraction = () => {
      userInteractionRef.current = true;
      if (interactionResetTimeoutRef.current !== null) window.clearTimeout(interactionResetTimeoutRef.current);
      interactionResetTimeoutRef.current = window.setTimeout(() => {
        userInteractionRef.current = false;
        interactionResetTimeoutRef.current = null;
      }, INTERACTION_DECAY_MS);
    };
    const onTrustedInput = () => {
      markUserInteraction();
      pendingRestoreRef.current = null;
    };
    const onScrollKey = (event: KeyboardEvent) => {
      if (OPERATOR_SCROLL_KEYS.has(event.key)) onTrustedInput();
    };
    el.addEventListener("wheel", onTrustedInput, { passive: true, capture: true });
    el.addEventListener("touchstart", onTrustedInput, { passive: true, capture: true });
    el.addEventListener("pointerdown", onTrustedInput, { passive: true, capture: true });
    el.addEventListener("keydown", onScrollKey, { capture: true });
    return () => {
      el.removeEventListener("wheel", onTrustedInput, { capture: true });
      el.removeEventListener("touchstart", onTrustedInput, { capture: true });
      el.removeEventListener("pointerdown", onTrustedInput, { capture: true });
      el.removeEventListener("keydown", onScrollKey, { capture: true });
      if (interactionResetTimeoutRef.current !== null) {
        window.clearTimeout(interactionResetTimeoutRef.current);
        interactionResetTimeoutRef.current = null;
      }
    };
    // Refs only — mount once (see the comment above).
  }, []);

  const toggleRun = useCallback((key: string) => {
    setExpandedRuns((current) => {
      const next = new Set(current);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }, []);

  const knownTotal = typeof totalItems === "number" ? totalItems : undefined;

  return (
    <div className={viewportShell}>
      <div ref={scrollRef} className={viewport} data-testid="conversation-viewport" data-kbzone="chrome">
        {emptyNote !== undefined && rows.length === 0 ? (
          <div className={emptyInWell} data-testid="conversation-empty">
            {emptyNote}
          </div>
        ) : null}
        {hasOlder ? (
          <div className={olderBar}>
            <button
              type="button"
              className={olderButton}
              onClick={onLoadOlder}
              disabled={busy}
              data-testid="conversation-load-older"
            >
              {busy ? "loading…" : knownTotal !== undefined ? "Load older" : "Load older (total unknown)"}
            </button>
          </div>
        ) : null}
        <div
          role="feed"
          aria-label="Conversation"
          aria-busy={busy}
          onKeyDown={onKeyDown}
          className={feedInner}
          style={{ height: `${totalSize}px` }}
          data-testid="conversation-feed"
        >
          {virtualRows.map((virtualRow) => {
            const row = rows[virtualRow.index];
            if (row === undefined) return null;
            const posinset = row.kind === "item" ? row.item.globalOrdinal : row.ordinal;
            const label =
              row.kind === "item"
                ? itemAccessibleName(row.item)
                : `${row.items.length} identical unknown vendor events`;
            const tabbable =
              focusedKey === row.key || (focusedKey === null && virtualRow.index === rows.length - 1);
            return (
              <article
                key={virtualRow.key}
                ref={virtualizer.measureElement}
                data-index={virtualRow.index}
                data-row-key={row.key}
                data-conversation-item
                tabIndex={tabbable ? 0 : -1}
                aria-label={label}
                aria-posinset={posinset}
                {...(knownTotal !== undefined ? { "aria-setsize": knownTotal } : {})}
                aria-live="off"
                className={rowShell}
                style={{ transform: `translateY(${virtualRow.start}px)` }}
                onFocus={() => setFocusedKey(row.key)}
              >
                {row.kind === "item" ? (
                  <ConversationItemView item={row.item} />
                ) : (
                  <UnknownVendorRun row={row} expanded={expandedRuns.has(row.key)} onToggle={() => toggleRun(row.key)} />
                )}
              </article>
            );
          })}
        </div>
      </div>
      {/* The latest chip lives OUTSIDE the scroller (a child of the shell): absolutely positioned
          children of a scroll container scroll away with its content. */}
      {rows.length > 0 && (awayFromLatest || pendingUpdates > 0) ? (
        <button
          type="button"
          className={cx(latestChip, pendingUpdates > 0 && latestChipWithUpdates)}
          aria-label={
            pendingUpdates > 0
              ? `Jump to latest, ${pendingUpdates} new ${pendingUpdates === 1 ? "update" : "updates"}`
              : "Jump to latest"
          }
          title="jump to latest"
          data-testid="conversation-scroll-latest"
          onClick={scrollToLatest}
        >
          <span aria-hidden="true">↓</span>
          {pendingUpdates > 0 ? (
            <span data-testid="conversation-new-updates">
              {pendingUpdates} new
            </span>
          ) : (
            <span>latest</span>
          )}
        </button>
      ) : null}
    </div>
  );
}

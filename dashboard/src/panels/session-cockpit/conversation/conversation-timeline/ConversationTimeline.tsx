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
// The machinery lives in the sibling hook modules (timelineRefs/timelineRestore/timelineScroll/
// timelineFollow/timelinePremeasure/timelineControls/timelineController) and the render in
// timelineFeed.tsx.

import type { ReactNode } from "react";

import type { ConversationScrollMemory } from "../../../../data/conversation/store";
import type { ConversationItem } from "../../../../data/conversation/types";
import { useTimelineData, useTimelineEffects } from "./timelineController";
import { TimelineFeed } from "./timelineFeed";

export { OPERATOR_SCROLL_KEYS } from "./measurements";

export interface ConversationTimelineProps {
  items: ConversationItem[];
  totalItems?: number;
  hasOlder: boolean;
  busy: boolean;
  /** Empty-conversation content rendered INSIDE the well (the well always renders). */
  emptyNote?: ReactNode;
  onLoadOlder: () => void;
  /** False whenever this retained feed is not the operator's active timeline. The viewport DOM,
      scrollTop, virtual rows, and measurement cache stay mounted; TanStack and the manual
      scroll/measurement machinery detach from the element until it becomes active again. */
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
  const data = useTimelineData({
    items,
    visible,
    restoreScroll,
    measurementCacheId,
  });
  const { expandedRuns, toggleRun, scrollToLatest, onKeyDown } =
    useTimelineEffects(data, {
      visible,
      restoreScroll,
      onScrollMemory,
      measurementCacheId,
    });
  const knownTotal = typeof totalItems === "number" ? totalItems : undefined;
  return (
    <TimelineFeed
      scrollRef={data.scrollRef}
      rows={data.rows}
      emptyNote={emptyNote}
      hasOlder={hasOlder}
      busy={busy}
      knownTotal={knownTotal}
      onLoadOlder={onLoadOlder}
      onKeyDown={onKeyDown}
      totalSize={data.totalSize}
      virtualRows={data.virtualRows}
      focusedKey={data.focusedKey}
      onRowFocus={data.setFocusedKey}
      expandedRuns={expandedRuns}
      onToggleRun={toggleRun}
      rowRefFor={data.virtualizer.measureElement}
      latestVisible={
        data.rows.length > 0 && (data.awayFromLatest || data.pendingUpdates > 0)
      }
      pendingUpdates={data.pendingUpdates}
      onLatest={scrollToLatest}
    />
  );
}

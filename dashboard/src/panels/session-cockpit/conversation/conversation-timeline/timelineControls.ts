import {
  useCallback,
  useMemo,
  useState,
  type KeyboardEvent,
  type RefObject,
} from "react";
import {
  defaultRangeExtractor,
  elementScroll,
  useVirtualizer,
  type Range,
  type VirtualItem,
} from "@tanstack/react-virtual";

import type { DisplayRow } from "../collapse";
import { INITIAL_PREMEASURE_BATCH_ROWS } from "./measurements";
import { inOverflowRegion, isEditableTarget } from "./unknownRun";
import type { TimelineRefs } from "./timelineRefs";

export type TimelineVirtualizer = ReturnType<
  typeof useVirtualizer<HTMLDivElement, Element>
>;

export function useTimelineFocus(rows: DisplayRow[]) {
  const [focusedKey, setFocusedKey] = useState<string | null>(null);
  const focusedIndex = useMemo(
    () =>
      focusedKey === null
        ? -1
        : rows.findIndex((row) => row.key === focusedKey),
    [rows, focusedKey],
  );
  return { focusedKey, setFocusedKey, focusedIndex };
}

export function useTimelineVirtualizer({
  rows,
  visible,
  focusedIndex,
  premeasureBatchStart,
  becomingVisible,
  scrollRef,
  refs,
  initialMeasurementsCache,
}: {
  rows: DisplayRow[];
  visible: boolean;
  focusedIndex: number;
  premeasureBatchStart: number | null;
  becomingVisible: boolean;
  scrollRef: RefObject<HTMLDivElement | null>;
  refs: TimelineRefs;
  initialMeasurementsCache: VirtualItem[];
}): TimelineVirtualizer {
  // Pin the focused row AND the default-tab row (the last row) so a tabbable article always exists,
  // even when both scroll out of the window (§14.3).
  const rangeExtractor = useCallback(
    (range: Range) => {
      const base = new Set(defaultRangeExtractor(range));
      if (visible && premeasureBatchStart !== null && premeasureBatchStart >= 0) {
        const batchStart = Math.min(premeasureBatchStart, rows.length);
        const batchEnd = Math.min(
          rows.length,
          batchStart + INITIAL_PREMEASURE_BATCH_ROWS,
        );
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

  const virtualizer: TimelineVirtualizer = useVirtualizer({
    count: rows.length,
    // Returning null runs TanStack's cleanup path (ResizeObserver, scroll listener, rAF) without
    // `enabled:false`, whose core implementation deliberately clears every measurement cache.
    getScrollElement: () => (visible ? scrollRef.current : null),
    estimateSize: () => 80,
    overscan: 8,
    getItemKey: (index) => rows[index]?.key ?? index,
    rangeExtractor,
    initialMeasurementsCache,
    scrollToFn: (offset, options, instance) => {
      const element = instance.scrollElement;
      if (element === null) return;
      // Reattachment normally writes the virtualizer's retained offset immediately. A view-switch
      // restore may still be looking at a collapsed/partial box, so the bounded restore driver must
      // remain the sole writer until that geometry is honest. This also prevents TanStack's internal
      // reconcile from racing the exact remembered offset.
      if (becomingVisible || refs.pendingRestoreRef.current !== null) return;
      const target = offset + (options.adjustments ?? 0);
      const current = instance.options.horizontal
        ? element.scrollLeft
        : element.scrollTop;
      // TanStack syncs its retained offset whenever a detached element is reattached. The DOM already
      // retains that pixel for visibility-hidden chats; skip the no-op write and its reconcile rAF.
      if (Math.abs(current - target) < 1) return;
      elementScroll(offset, options, instance);
    },
  });
  return virtualizer;
}

function ownsHomeEnd(target: HTMLElement): boolean {
  return (
    inOverflowRegion(target) ||
    !(window.getSelection()?.isCollapsed ?? true)
  );
}

function handleTimelineKeyDown(
  event: KeyboardEvent<HTMLDivElement>,
  focusedIndex: number,
  rowsLength: number,
  focusRowByIndex: (index: number) => void,
) {
  const target = event.target as HTMLElement;
  if (isEditableTarget(target)) return;
  const current = focusedIndex < 0 ? rowsLength - 1 : focusedIndex;
  switch (event.key) {
    case "]":
      event.preventDefault();
      focusRowByIndex(Math.min(rowsLength - 1, current + 1));
      break;
    case "[":
      event.preventDefault();
      focusRowByIndex(Math.max(0, current - 1));
      break;
    case "Home":
      // A labeled overflow region or an active text selection owns Home/End (do not hijack).
      if (ownsHomeEnd(target)) return;
      event.preventDefault();
      focusRowByIndex(0);
      break;
    case "End":
      if (ownsHomeEnd(target)) return;
      event.preventDefault();
      focusRowByIndex(rowsLength - 1);
      break;
    default:
      break;
  }
}

export function useTimelineControls(
  rows: DisplayRow[],
  virtualizer: TimelineVirtualizer,
  setFocusedKey: (key: string | null) => void,
  focusedIndex: number,
  refs: TimelineRefs,
  scrollRef: RefObject<HTMLDivElement | null>,
  setPendingUpdates: (value: number) => void,
  setAwayFromLatest: (value: boolean) => void,
) {
  const [expandedRuns, setExpandedRuns] = useState<Set<string>>(
    () => new Set(),
  );
  const focusRowByIndex = useCallback(
    (index: number) => {
      if (index < 0 || index >= rows.length) return;
      const row = rows[index];
      setFocusedKey(row.key);
      virtualizer.scrollToIndex(index, { align: "auto" });
      requestAnimationFrame(() => {
        scrollRef.current
          ?.querySelector<HTMLElement>(
            `[data-row-key="${CSS.escape(row.key)}"]`,
          )
          ?.focus();
      });
    },
    [rows, virtualizer, setFocusedKey, scrollRef],
  );

  // Latest chip — the single explicit route back to the live edge. The operator wins over any
  // armed restore, the sticky override clears, and the honest pixel end is pinned immediately.
  const scrollToLatest = useCallback(() => {
    if (rows.length === 0) return;
    refs.pendingRestoreRef.current = null;
    refs.userOverrideRef.current = false;
    // An explicit jump to the live end retires any parked in-band hold, so the follow re-pins
    // subsequent growth instead of holding the pre-jump offset.
    refs.bandHoldRef.current = false;
    refs.nearBottomRef.current = true;
    setPendingUpdates(0);
    setAwayFromLatest(false);
    virtualizer.scrollToIndex(rows.length - 1, { align: "end" });
    // scrollToIndex computes estimate-degenerate targets — pin the honest pixel end.
    const el = scrollRef.current;
    if (el !== null && el.clientHeight > 0) {
      el.scrollTop = el.scrollHeight - el.clientHeight;
    }
  }, [rows.length, virtualizer, refs, scrollRef, setPendingUpdates, setAwayFromLatest]);

  const toggleRun = useCallback((key: string) => {
    setExpandedRuns((current) => {
      const next = new Set(current);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }, []);

  const onKeyDown = useCallback(
    (event: KeyboardEvent<HTMLDivElement>) =>
      handleTimelineKeyDown(event, focusedIndex, rows.length, focusRowByIndex),
    [focusedIndex, focusRowByIndex, rows.length],
  );

  return { expandedRuns, toggleRun, scrollToLatest, onKeyDown };
}

import {
  useCallback,
  useLayoutEffect,
  type Dispatch,
  type RefObject,
  type SetStateAction,
} from "react";

import type { DisplayRow } from "../collapse";
import type { TimelineVirtualizer } from "./timelineControls";
import type { TimelineRefs } from "./timelineRefs";

function followNewLastRow(
  lastKey: string | null,
  prevLastKey: string | null,
  rowsLength: number,
  virtualizer: TimelineVirtualizer,
  scrollRef: RefObject<HTMLDivElement | null>,
  refs: TimelineRefs,
  setPendingUpdates: Dispatch<SetStateAction<number>>,
) {
  if (prevLastKey === null) {
    // First content on a FRESH mount (first open, or the keep-alive pool's first mount of this
    // session): the armed atBottom pending-restore (mount arm) routes this,
    // so the one-shot jump below is only a fallback for a mount whose restore was already
    // stood down (the operator's trusted input beat the first content). This runs pre-paint,
    // so it is an alignment, never a visible jump; afterwards the ordinary near-bottom follow
    // below takes over.
    // Skipped when a remembered position is pending — the restore below then owns the
    // initial offset (a re-shown chat is not a first open).
    if (lastKey !== null && refs.pendingRestoreRef.current === null) {
      virtualizer.scrollToIndex(rowsLength - 1, { align: "end" });
    }
    return;
  }
  if (refs.nearBottomRef.current && !refs.bandHoldRef.current) {
    // Never fire the follow while the feed is box-less (the display:none
    // phases) — degenerate targets only arm 5s tanstack reconciles that race the re-show
    // restore, which owns the landing. An unfocused keep-alive chat never reaches this effect;
    // it preserves the last position while its stream and virtualizer are paused.
    // An operator parked inside the band (bandHoldRef) is NOT yanked to the end by a new
    // row either — the same in-band hold the streaming-delta follow honors. The arrival counts
    // into the pill instead, so a small deliberate scroll-up survives new items, not just
    // in-place growth.
    const el = scrollRef.current;
    if (el !== null && el.clientHeight > 0) {
      virtualizer.scrollToIndex(rowsLength - 1, { align: "end" });
    }
    return;
  }
  setPendingUpdates((count) => count + 1);
}

export function useFollowLayout(
  rows: DisplayRow[],
  visible: boolean,
  becomingVisible: boolean,
  virtualizer: TimelineVirtualizer,
  scrollRef: RefObject<HTMLDivElement | null>,
  refs: TimelineRefs,
  setPendingUpdates: Dispatch<SetStateAction<number>>,
) {
  useLayoutEffect(() => {
    const lastKey = rows.length > 0 ? rows[rows.length - 1].key : null;
    if (!visible || becomingVisible) {
      // Adopt any final in-flight page while paused; refocus must not synthesize a hidden arrival.
      refs.prevLastKeyRef.current = lastKey;
      return;
    }
    if (lastKey !== refs.prevLastKeyRef.current) {
      followNewLastRow(
        lastKey,
        refs.prevLastKeyRef.current,
        rows.length,
        virtualizer,
        scrollRef,
        refs,
        setPendingUpdates,
      );
    }
    refs.prevLastKeyRef.current = lastKey;
  }, [becomingVisible, rows, virtualizer, visible, scrollRef, refs, setPendingUpdates]);
}

export function useStreamedGrowthCount(
  rows: DisplayRow[],
  visible: boolean,
  becomingVisible: boolean,
  lastRowSize: number | undefined,
  refs: TimelineRefs,
  setPendingUpdates: Dispatch<SetStateAction<number>>,
) {
  // The latest chip also counts STREAMED arrivals — an append delta grows the last
  // row IN PLACE (no key change, so the row-key effect above never sees it). Count only a growth
  // of the last row's MEASURED size against an adopted baseline: the first measurement after a
  // key change (mount, arrival correction) is adopted, never counted; shrinks (block
  // finalization) are not arrivals; while locked the growth re-pin above already kept the end in
  // view, so there is nothing to report.
  useLayoutEffect(() => {
    const lastKey = rows.length > 0 ? rows[rows.length - 1].key : null;
    if (!visible || becomingVisible) {
      refs.streamBaselineRef.current = { key: lastKey, size: lastRowSize };
      return;
    }
    const prev = refs.streamBaselineRef.current;
    if (lastKey !== prev.key) {
      refs.streamBaselineRef.current = { key: lastKey, size: undefined };
      return;
    }
    const prevSize = prev.size;
    refs.streamBaselineRef.current = { key: lastKey, size: lastRowSize };
    if (prevSize === undefined || lastRowSize === undefined || lastRowSize <= prevSize) return;
    if (refs.nearBottomRef.current) return;
    setPendingUpdates((count) => count + 1);
  }, [becomingVisible, rows, lastRowSize, visible, refs, setPendingUpdates]);
}

export function useFollowOnGrowth(
  rows: DisplayRow[],
  visible: boolean,
  becomingVisible: boolean,
  totalSize: number,
  scrollRef: RefObject<HTMLDivElement | null>,
  refs: TimelineRefs,
) {
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
  // only intent set in handleScroll), not by `userInteractionRef` alone. `userInteractionRef` is
  // kept in the union for the transient case where genuine input has fired but its scroll event
  // has not yet recorded the offset. A gesture parked AT the end (distance 0 — the operator
  // wheeled back down, clearing bandHoldRef, or a re-measure compensation landed on the pinned
  // end) keeps the follow as the owner of the end, so a growth above the viewport can never
  // displace the pinned bottom (the upscroll-anchor suite's contract, and the bottom-follow
  // contract).
  useLayoutEffect(() => {
    if (!visible || becomingVisible) return;
    if (!refs.nearBottomRef.current) return;
    if (refs.pendingRestoreRef.current !== null) return;
    const el = scrollRef.current;
    if (el === null || el.clientHeight === 0) return;
    if (
      (refs.userInteractionRef.current || refs.bandHoldRef.current) &&
      el.scrollHeight - el.scrollTop - el.clientHeight > 0
    ) {
      return;
    }
    el.scrollTop = el.scrollHeight - el.clientHeight;
  }, [becomingVisible, rows, totalSize, visible, scrollRef, refs]);
}

export function usePrependAnchor(
  rows: DisplayRow[],
  visible: boolean,
  becomingVisible: boolean,
  virtualizer: TimelineVirtualizer,
  refs: TimelineRefs,
) {
  useLayoutEffect(() => {
    const firstKey = rows.length > 0 ? rows[0].key : null;
    if (!visible || becomingVisible) {
      refs.prevFirstKeyRef.current = firstKey;
      return;
    }
    const anchor = refs.anchorRef.current;
    if (
      firstKey !== refs.prevFirstKeyRef.current &&
      refs.prevFirstKeyRef.current !== null &&
      anchor !== null
    ) {
      const anchorIndex = rows.findIndex((row) => row.key === anchor.itemId);
      if (anchorIndex >= 0) virtualizer.scrollToIndex(anchorIndex, { align: "start" });
    }
    refs.prevFirstKeyRef.current = firstKey;
  }, [becomingVisible, rows, virtualizer, visible, refs]);
}

export function useMeasureAnchor(
  rows: DisplayRow[],
  virtualizer: TimelineVirtualizer,
  refs: TimelineRefs,
) {
  // Measurement anchor: record the row containing the viewport top (event-time accuracy for the
  // measurement-commit effect below). getVirtualItemForOffset reads the virtualizer's live
  // measurements, so the recording stays consistent even between commits.
  const recordMeasureAnchor = useCallback(
    (el: HTMLDivElement) => {
      const topItem = virtualizer.getVirtualItemForOffset(el.scrollTop);
      if (topItem === undefined) return;
      const row = rows[topItem.index];
      if (row === undefined) return;
      refs.measureAnchorRef.current = {
        index: topItem.index,
        key: row.key,
        start: topItem.start,
        offsetWithinRow: el.scrollTop - topItem.start,
      };
    },
    [rows, virtualizer, refs],
  );
  return recordMeasureAnchor;
}

function isPrependCommit(firstKey: string | null, refs: TimelineRefs): boolean {
  const prepended =
    refs.measurePrevFirstKeyRef.current !== null &&
    firstKey !== refs.measurePrevFirstKeyRef.current;
  refs.measurePrevFirstKeyRef.current = firstKey;
  return prepended;
}

function measureAnchorBox(
  refs: TimelineRefs,
  scrollRef: RefObject<HTMLDivElement | null>,
): HTMLDivElement | null {
  if (refs.nearBottomRef.current || refs.pendingRestoreRef.current !== null) {
    return null;
  }
  const el = scrollRef.current;
  if (el === null || !refs.visibleRef.current || el.clientHeight === 0) {
    return null;
  }
  return el;
}

function measureAnchorEligible(
  rows: DisplayRow[],
  becomingVisible: boolean,
  refs: TimelineRefs,
  scrollRef: RefObject<HTMLDivElement | null>,
) {
  const firstKey = rows.length > 0 ? rows[0].key : null;
  if (becomingVisible || !refs.visibleRef.current) {
    refs.measurePrevFirstKeyRef.current = firstKey;
    refs.measureAnchorRef.current = null;
    return null;
  }
  if (isPrependCommit(firstKey, refs)) {
    refs.measureAnchorRef.current = null;
    return null;
  }
  const anchor = refs.measureAnchorRef.current;
  if (anchor === null) return null;
  const el = measureAnchorBox(refs, scrollRef);
  if (el === null) return null;
  return { anchor, el };
}

function measureAnchorTarget(
  el: HTMLDivElement,
  anchor: { index: number; key: string; start: number; offsetWithinRow: number },
  virtualRows: readonly { key: unknown; start: number }[],
  rows: DisplayRow[],
) {
  if (anchor.index >= rows.length || rows[anchor.index].key !== anchor.key) {
    return { drop: true, shift: 0, expected: 0, maxScroll: 0, currentStart: 0 };
  }
  // The anchor compensates the viewport the operator is looking at; if the offset has moved more
  // than ~2 viewports away from it, an explicit navigation outran the scroll event — drop the
  // anchor instead of injecting a proxy shift at a distant position (the next event re-records).
  if (Math.abs(el.scrollTop - anchor.start) > el.clientHeight * 2) {
    return { drop: true, shift: 0, expected: 0, maxScroll: 0, currentStart: 0 };
  }
  const current = virtualRows.find((virtualRow) => virtualRow.key === anchor.key);
  if (current === undefined) return null; // outside the mounted window this commit — retry the next one
  return {
    drop: false,
    shift: current.start - anchor.start,
    expected: current.start + anchor.offsetWithinRow,
    maxScroll: el.scrollHeight - el.clientHeight,
    currentStart: current.start,
  };
}

export function useMeasureAnchorCommit(
  rows: DisplayRow[],
  visible: boolean,
  becomingVisible: boolean,
  virtualizer: TimelineVirtualizer,
  virtualRows: readonly { key: unknown; start: number }[],
  scrollRef: RefObject<HTMLDivElement | null>,
  refs: TimelineRefs,
) {
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
  // position is already consistent. The write is a direct pixel write — it carries no input, so the
  // intent refs never flip. Gates mirror every other writer: never while locked (the follow re-pin
  // owns the end), never while a restore is armed, never on a prepend commit, never on a
  // hidden/box-less feed.
  useLayoutEffect(() => {
    const gate = measureAnchorEligible(rows, becomingVisible, refs, scrollRef);
    if (gate === null) return;
    const target = measureAnchorTarget(gate.el, gate.anchor, virtualRows, rows);
    if (target === null) return;
    if (target.drop) {
      refs.measureAnchorRef.current = null;
      return;
    }
    gate.anchor.start = target.currentStart; // absorb: each commit compensates only the NEW shift
    if (target.shift === 0) return;
    if (Math.abs(target.expected - gate.el.scrollTop) < 1) return; // already compensated elsewhere
    gate.el.scrollTop = Math.max(
      0,
      Math.min(gate.el.scrollTop + target.shift, target.maxScroll),
    );
  }, [becomingVisible, rows, virtualizer, visible, virtualRows, refs, scrollRef]);
}

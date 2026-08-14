import { useCallback, useEffect, useLayoutEffect, type RefObject } from "react";

import type { ConversationScrollMemory } from "../../../../data/conversation/store";
import type { DisplayRow } from "../collapse";
import { RESTORE_DRIVE_MAX_MS, RESTORE_SETTLE_FRAMES } from "./measurements";
import type { TimelineRefs } from "./timelineRefs";

export function useRestoreArm(
  visible: boolean,
  restoreScroll: ConversationScrollMemory | undefined,
  refs: TimelineRefs,
) {
  // Switching cockpit tabs must not reopen the chat at the START: the
  // cockpit's Chats layer hides with display:none, which destroys the scroll offset (unlike the
  // keep-alive pool's visibility:hidden, which preserves it across chat switches). The re-show
  // arms a restore from the remembered position. With nothing remembered, a STILL-armed restore
  // (the mount arm that never got to consume — e.g. the chat was hidden its whole life) rides
  // again instead of being dropped; an already-consumed or operator-canceled arm stays null, so a
  // memory-less re-show never re-aligns.
  useLayoutEffect(() => {
    const becameVisible = !refs.wasVisibleRef.current && visible;
    refs.wasVisibleRef.current = visible;
    if (becameVisible) {
      refs.pendingRestoreRef.current =
        restoreScroll ?? refs.pendingRestoreRef.current;
      // A (re-)armed restore starts a FRESH driver run (budget + settle frames) — the counters
      // live in restoreDriveRef so live-frame churn can never reset them mid-run (Race 1).
      refs.restoreDriveRef.current = {
        startedAt: 0,
        lastFeedHeight: -1,
        settledFrames: 0,
      };
    }
  }, [visible, restoreScroll, refs]);
}

export function useRestoreApply(
  rows: DisplayRow[],
  scrollRef: RefObject<HTMLDivElement | null>,
  refs: TimelineRefs,
  setPendingUpdates: (value: number) => void,
  setAwayFromLatest: (value: boolean) => void,
) {
  // The restore applies ONLY against honest geometry, and stays armed until it does. On a
  // real-browser re-show the virtualizer re-measures across a multi-frame window (Playwright: the
  // feed height reads 0px at +2ms, the true scrollHeight recovers ~54ms later) — a one-shot apply
  // in the re-show commit is clamped to 0 by the browser and nothing re-applies. Shared by the
  // render-driven pass below and the bounded rAF driver further down.
  const tryApplyPendingRestore = useCallback(() => {
    const pending = refs.pendingRestoreRef.current;
    if (pending === null || !refs.visibleRef.current || rows.length === 0) return;
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
      refs.nearBottomRef.current = true;
      refs.userOverrideRef.current = false;
      refs.bandHoldRef.current = false;
      setPendingUpdates(0);
      setAwayFromLatest(false);
      const end = el.scrollHeight - el.clientHeight;
      if (Math.abs(el.scrollTop - end) >= 1) el.scrollTop = end;
      return;
    }
    // Left mid-conversation or at the very top: restore the exact pixel offset — but only once
    // the scrollable extent honestly CONTAINS it (content measured a full viewport beyond the
    // offset), so the browser cannot clamp the write. Items appended while away never shift
    // earlier rows, and the already-counting latest chip keeps covering what flowed in.
    const maxScroll = el.scrollHeight - el.clientHeight;
    if (el.clientHeight === 0 || maxScroll < pending.scrollTop - 1) return; // stay armed
    refs.pendingRestoreRef.current = null;
    // A remembered non-bottom position disengages the lock with the sticky override set —
    // the restored position holds exactly, arrivals count into the latest chip, and only a genuine
    // scroll back to the bottom (or an explicit control) re-engages the follow.
    refs.nearBottomRef.current = false;
    refs.userOverrideRef.current = true;
    setAwayFromLatest(true);
    el.scrollTop = pending.scrollTop;
  }, [rows, scrollRef, refs, setPendingUpdates, setAwayFromLatest]);
  refs.tryApplyRef.current = tryApplyPendingRestore;
  // Render-driven attempt: free on every render pass (the virtualizer re-renders as it
  // re-measures). Bounds: no timers — attempts are O(1) geometry checks; the operator's trusted
  // input cancels the armed restore (wheel/touch/scroll-keys, capture-phase below); a never-ready
  // geometry (content shrank while away) leaves it armed but inert until the next hide/show.
  useLayoutEffect(() => {
    tryApplyPendingRestore();
  });
  return tryApplyPendingRestore;
}

export function useRestoreDriver(
  visible: boolean,
  rows: DisplayRow[],
  scrollRef: RefObject<HTMLDivElement | null>,
  refs: TimelineRefs,
) {
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
    if (!visible || rowsEmpty || refs.pendingRestoreRef.current === null) {
      return undefined;
    }
    const drive = refs.restoreDriveRef.current;
    // The budget opens when a run can first drive, not at arm time (an armed mount restore may
    // wait out a zero-content window or a box-less phase before its first honest frame).
    if (drive.startedAt === 0) drive.startedAt = Date.now();
    let rafId = 0;
    const attempt = () => {
      const pending = refs.pendingRestoreRef.current;
      if (pending === null) return; // applied or canceled — stop
      if (Date.now() - drive.startedAt > RESTORE_DRIVE_MAX_MS) {
        refs.pendingRestoreRef.current = null;
        return;
      }
      refs.tryApplyRef.current();
      if (refs.pendingRestoreRef.current === null) return; // pixel branch consumed
      if (!pending.atBottom) {
        rafId = requestAnimationFrame(attempt);
        return;
      }
      const el = scrollRef.current;
      const height = el !== null && el.clientHeight > 0 ? el.scrollHeight : -1;
      if (height === drive.lastFeedHeight) {
        drive.settledFrames += 1;
        if (drive.settledFrames >= RESTORE_SETTLE_FRAMES) {
          refs.pendingRestoreRef.current = null;
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
  }, [visible, rowsEmpty, scrollRef, refs]);
}

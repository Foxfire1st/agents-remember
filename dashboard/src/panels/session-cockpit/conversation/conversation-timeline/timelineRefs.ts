import { useMemo, useRef, type RefObject } from "react";

import type { ConversationScrollMemory } from "../../../../data/conversation/store";

export interface TimelineRefs {
  nearBottomRef: RefObject<boolean>;
  userInteractionRef: RefObject<boolean>;
  userOverrideRef: RefObject<boolean>;
  bandHoldRef: RefObject<boolean>;
  interactionResetTimeoutRef: RefObject<number | null>;
  prevLastKeyRef: RefObject<string | null>;
  prevFirstKeyRef: RefObject<string | null>;
  anchorRef: RefObject<{ itemId: string; offsetPx: number } | null>;
  premeasurePausesForScrollRef: RefObject<boolean>;
  measureAnchorRef: RefObject<{
    index: number;
    key: string;
    start: number;
    offsetWithinRow: number;
  } | null>;
  measurePrevFirstKeyRef: RefObject<string | null>;
  pendingRestoreRef: RefObject<ConversationScrollMemory | null>;
  wasVisibleRef: RefObject<boolean>;
  visibleRef: RefObject<boolean>;
  restoreDriveRef: RefObject<{
    startedAt: number;
    lastFeedHeight: number;
    settledFrames: number;
  }>;
  measurementWidthRef: RefObject<number | null>;
  streamBaselineRef: RefObject<{ key: string | null; size: number | undefined }>;
  tryApplyRef: RefObject<() => void>;
}

export function useTimelineRefs(
  restoreScroll: ConversationScrollMemory | undefined,
  visible: boolean,
): TimelineRefs {
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
  // next growth, and survive it past the 500ms interaction window. This flag is the DURABLE,
  // input-only record that the operator is parked below the live end; it clears the instant they
  // reach the true end (distance 0) so the follow resumes.
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
  const measureAnchorRef = useRef<{
    index: number;
    key: string;
    start: number;
    offsetWithinRow: number;
  } | null>(null);
  const measurePrevFirstKeyRef = useRef<string | null>(null);
  // A restore armed at mount (when a position is remembered) and on every re-show, consumed
  // when applied. null = nothing to restore (the ordinary live-follow path).
  // A FRESH mount with nothing remembered arms an atBottom restore instead of the old one-shot
  // scrollToIndex jump — that jump fired against a display:none box (cockpit boots on Operations)
  // or estimate-degenerate measurements, no-oped, and never retried (the restart-opens-at-top bug).
  // The armed restore rides the restore machinery: pixel re-drive, settle frames, box-less stay-armed,
  // trusted-input cancel. Its scrollTop is 0 and unused — the atBottom branch re-drives the CURRENT
  // DOM end, and echo gate 1 can never trip on it.
  const pendingRestoreRef = useRef<ConversationScrollMemory | null>(
    restoreScroll ?? { scrollTop: 0, atBottom: true },
  );
  const wasVisibleRef = useRef(visible);
  // Render-time mirror so the scroll listener can read the current visibility without
  // re-subscribing on every flip (the chatsLibraryOpenRef pattern in SessionsView).
  const visibleRef = useRef(visible);
  visibleRef.current = visible;
  // Race 1: the rAF restore driver's per-run state lives in a REF keyed to the ARM, never in the
  // effect closure — the closure state reset every live frame. Reset on (re-)arm below.
  const restoreDriveRef = useRef({ startedAt: 0, lastFeedHeight: -1, settledFrames: 0 });
  const measurementWidthRef = useRef<number | null>(null);
  const streamBaselineRef = useRef<{ key: string | null; size: number | undefined }>({
    key: null,
    size: undefined,
  });
  // Latest-apply bridge for the rAF driver: tryApplyPendingRestore is rows-keyed, so its identity
  // churns on every live frame. The driver reads the CURRENT apply through this ref instead of
  // subscribing to the callback (render-time ref write, the visibleRef pattern above).
  const tryApplyRef = useRef<() => void>(() => undefined);
  return useMemo(
    () => ({
      nearBottomRef,
      userInteractionRef,
      userOverrideRef,
      bandHoldRef,
      interactionResetTimeoutRef,
      prevLastKeyRef,
      prevFirstKeyRef,
      anchorRef,
      premeasurePausesForScrollRef,
      measureAnchorRef,
      measurePrevFirstKeyRef,
      pendingRestoreRef,
      wasVisibleRef,
      visibleRef,
      restoreDriveRef,
      measurementWidthRef,
      streamBaselineRef,
      tryApplyRef,
    }),
    [
      nearBottomRef,
      userInteractionRef,
      userOverrideRef,
      bandHoldRef,
      interactionResetTimeoutRef,
      prevLastKeyRef,
      prevFirstKeyRef,
      anchorRef,
      premeasurePausesForScrollRef,
      measureAnchorRef,
      measurePrevFirstKeyRef,
      pendingRestoreRef,
      wasVisibleRef,
      visibleRef,
      restoreDriveRef,
      measurementWidthRef,
      streamBaselineRef,
      tryApplyRef,
    ],
  );
}

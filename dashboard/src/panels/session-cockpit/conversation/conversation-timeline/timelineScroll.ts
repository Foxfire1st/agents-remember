import {
  useCallback,
  useEffect,
  useRef,
  type RefObject,
} from "react";

import type { ConversationScrollMemory } from "../../../../data/conversation/store";
import type { DisplayRow } from "../collapse";
import {
  BOTTOM_FOLLOW_PX,
  INTERACTION_DECAY_MS,
  OPERATOR_SCROLL_KEYS,
} from "./measurements";
import type { TimelineVirtualizer } from "./timelineControls";
import type { TimelineRefs } from "./timelineRefs";

function scrollEchoAllowed(
  el: HTMLDivElement,
  pending: ConversationScrollMemory | null,
  virtualizer: TimelineVirtualizer,
): boolean {
  const maxScroll = el.scrollHeight - el.clientHeight;
  // Gate 1 (scoped to the ARMED restore): a geometry that cannot contain the armed offset
  // is an environmental clamp. It is deliberately NOT applied to ordinary events: a viewport
  // settle that lowers maxScroll would otherwise freeze the memory at a stale-oversized
  // reference forever — an honest clamp event simply re-baselines the memory instead.
  if (pending !== null && maxScroll + 1 < pending.scrollTop) return false;
  // Gate 2: a DOM scrollHeight far below the virtualizer's own measured total (which survives
  // display:none and tracks legitimate shrinks) is the collapsed feed echoing. A genuinely
  // short conversation (sh ≤ ch from the start) can never trip either.
  if (el.scrollHeight + 1 < virtualizer.getTotalSize() - el.clientHeight) {
    return false;
  }
  return true;
}

function applyScrollIntent(
  el: HTMLDivElement,
  refs: TimelineRefs,
  setAwayFromLatest: (value: boolean) => void,
  setPendingUpdates: (value: number) => void,
) {
  const distance = el.scrollHeight - el.scrollTop - el.clientHeight;
  const isNearBottom = distance <= BOTTOM_FOLLOW_PX;
  setAwayFromLatest(!isNearBottom);
  // Intent machine (the JSONLogConsole model): the lock changes ONLY on genuine input or true
  // bottom-arrival. A deliberate scroll-up disengages and STAYS disengaged (sticky override) —
  // content-driven events carry no input and flip it NEITHER way; in particular a content-driven
  // event landing AT the bottom never clears a standing override. A genuine scroll all the way down
  // re-engages fully (follow + the latest chip's update count clears).
  if (isNearBottom) {
    if (!refs.userOverrideRef.current || refs.userInteractionRef.current) {
      refs.nearBottomRef.current = true;
      refs.userOverrideRef.current = false;
      setPendingUpdates(0);
    }
    // Arm/disarm the durable in-band hold. Genuine input that parks short of the exact end
    // (distance > 0) arms it — the follow must then hold this offset even after the interaction
    // window decays. Reaching the true end (distance 0) disarms it so the follow resumes.
    if (refs.userInteractionRef.current && distance > 0) {
      refs.bandHoldRef.current = true;
    } else if (distance === 0) {
      refs.bandHoldRef.current = false;
    }
  } else if (refs.userInteractionRef.current || refs.userOverrideRef.current) {
    refs.nearBottomRef.current = false;
    refs.userOverrideRef.current = true;
    // Crossed clear of the band — the sticky override owns the hold now; retire the in-band flag
    // so a later return to the true end re-engages the follow cleanly.
    refs.bandHoldRef.current = false;
  }
}

function cancelRestoreIfOperatorOwned(
  el: HTMLDivElement,
  pending: ConversationScrollMemory | null,
  refs: TimelineRefs,
) {
  if (pending === null) return;
  // An accepted scroll event with a restore still armed is the operator's own scroll landing —
  // they win, UNLESS the armed restore is the atBottom drive: that drive re-sets the end
  // itself every frame while armed, so an event already AT the bottom is its own, not the
  // operator's. (Trusted input — wheel/touch/scroll-keys — also cancels either way, below.)
  const distanceNow = el.scrollHeight - el.scrollTop - el.clientHeight;
  if (!(pending.atBottom && distanceNow <= BOTTOM_FOLLOW_PX)) {
    refs.pendingRestoreRef.current = null;
  }
}

export function useScrollGeometry(
  rows: DisplayRow[],
  virtualizer: TimelineVirtualizer,
  scrollRef: RefObject<HTMLDivElement | null>,
  refs: TimelineRefs,
  onScrollMemory: ((memory: ConversationScrollMemory) => void) | undefined,
  setAwayFromLatest: (value: boolean) => void,
  setPendingUpdates: (value: number) => void,
  recordMeasureAnchor: (el: HTMLDivElement) => void,
) {
  const handleScroll = useCallback(() => {
    const el = scrollRef.current;
    if (el === null) return;
    // An inactive or box-less timeline owns NO scroll geometry. A cockpit display:none teardown can
    // dispatch a clamp event, while a retained visibility:hidden chat still has geometry that the
    // operator cannot be interacting with. Neither may overwrite position/intent/anchors while the
    // feed is paused; the re-show restore owns that boundary.
    if (!refs.visibleRef.current || el.clientHeight === 0) return;
    // Echo gates: the collapse's clamp event is dispatched LATE
    // (~0.5s), with the box already restored (clientHeight>0 — passes the guard above) but the
    // feed transiently collapsed (st=0, sh≈ch). It must neither report nor cancel.
    const pending = refs.pendingRestoreRef.current;
    if (!scrollEchoAllowed(el, pending, virtualizer)) return;
    cancelRestoreIfOperatorOwned(el, pending, refs);
    applyScrollIntent(el, refs, setAwayFromLatest, setPendingUpdates);
    // Remember the position continuously — the re-show restores the last value reported
    // while visible (any event from the hidden/box-less phase is dropped by the guard above).
    const memory = { scrollTop: el.scrollTop, atBottom: refs.nearBottomRef.current };
    onScrollMemory?.(memory);
    const first = virtualizer.getVirtualItems()[0];
    if (first !== undefined) {
      const row = rows[first.index];
      if (row !== undefined) {
        // The timeline's OWN prepend anchor (first mounted row + offset) — consumed by the
        // older-paging preservation effect below. It never leaves this component.
        const offsetPx = first.start - el.scrollTop;
        refs.anchorRef.current = { itemId: row.key, offsetPx };
      }
    }
    recordMeasureAnchor(el);
  }, [
    rows,
    onScrollMemory,
    recordMeasureAnchor,
    virtualizer,
    scrollRef,
    refs,
    setAwayFromLatest,
    setPendingUpdates,
  ]);
  return handleScroll;
}

export function useScrollListener(
  visible: boolean,
  scrollRef: RefObject<HTMLDivElement | null>,
  handleScroll: () => void,
) {
  // The handler is rows-keyed and churns per render; keep ONE subscription per active interval
  // and read the current handler through a render-time ref mirror (the visibleRef pattern above).
  const handlerRef = useRef(handleScroll);
  handlerRef.current = handleScroll;
  useEffect(() => {
    if (!visible) return undefined;
    const el = scrollRef.current;
    if (el === null) return undefined;
    const wrapped = () => {
      handlerRef.current();
    };
    el.addEventListener("scroll", wrapped, { passive: true });
    return () => {
      el.removeEventListener("scroll", wrapped);
    };
  }, [visible, scrollRef]);
}

export function useTrustedInput(
  visible: boolean,
  scrollRef: RefObject<HTMLDivElement | null>,
  refs: TimelineRefs,
) {
  useEffect(() => {
    if (!visible) return undefined;
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
      refs.userInteractionRef.current = true;
      if (refs.interactionResetTimeoutRef.current !== null) {
        window.clearTimeout(refs.interactionResetTimeoutRef.current);
      }
      refs.interactionResetTimeoutRef.current = window.setTimeout(() => {
        refs.userInteractionRef.current = false;
        refs.interactionResetTimeoutRef.current = null;
      }, INTERACTION_DECAY_MS);
    };
    const onTrustedInput = () => {
      markUserInteraction();
      refs.pendingRestoreRef.current = null;
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
      if (refs.interactionResetTimeoutRef.current !== null) {
        window.clearTimeout(refs.interactionResetTimeoutRef.current);
        refs.interactionResetTimeoutRef.current = null;
      }
    };
    // Refs only — one subscription per active interval; blur tears it and its decay timer down.
  }, [visible, scrollRef, refs]);
}

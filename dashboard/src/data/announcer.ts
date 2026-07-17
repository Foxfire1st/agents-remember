import { useStore } from "zustand";
import { createStore } from "zustand/vanilla";

import { sessionCockpitStore } from "./sessionCockpitStore";
import { sessionStore, type OpenSession } from "./sessions";
import {
  sessionAwaitingInputAnnouncement,
  sessionFailedAnnouncement,
} from "./setControlsCopy";
import { seatVisualState } from "./stateGrammar";

// The cockpit announcer (260715-FEUI-L4 R8, design §9.5): exactly TWO live regions —
//   polite    — SetResult arrivals/promotions for the FOCUSED session (the set client calls it);
//   assertive — ANY session ENTERING failed or awaiting-input (the watcher below).
// COORDINATION WITH L6 (never double-announce): the InteractionBar already owns an assertive
// alert for the FOCUSED session's pending interaction — the watcher SKIPS the awaiting-input
// announcement exactly when that bar is announcing (focused + controlPendingInteraction). Every
// string comes from data/setControlsCopy so tests assert the words.

export interface AnnouncerState {
  polite: { text: string; seq: number };
  assertive: { text: string; seq: number };
}

export const announcerStore = createStore<AnnouncerState>(() => ({
  polite: { text: "", seq: 0 },
  assertive: { text: "", seq: 0 },
}));

export const useAnnouncer = <T>(selector: (state: AnnouncerState) => T): T =>
  useStore(announcerStore, selector);

export function announcePolite(text: string): void {
  announcerStore.setState((state) => ({ polite: { text, seq: state.polite.seq + 1 } }));
}

export function announceAssertive(text: string): void {
  announcerStore.setState((state) => ({ assertive: { text, seq: state.assertive.seq + 1 } }));
}

// ── The seat-state assertive watcher ────────────────────────────────────────────────────────────

/** Pure transition detector: previous state-keys → announcements + the updated map. A session's
 *  FIRST observation seeds the map silently — page load must not announce the whole fleet. */
export function stateEntryAnnouncements(
  previous: ReadonlyMap<string, string>,
  sessions: readonly OpenSession[],
  focusedSessionId: string | null,
): { announcements: string[]; next: Map<string, string> } {
  const next = new Map<string, string>();
  const announcements: string[] = [];
  for (const session of sessions) {
    const key = seatVisualState(session).key;
    next.set(session.id, key);
    const before = previous.get(session.id);
    if (before === undefined || before === key) continue;
    if (key === "failed") {
      announcements.push(sessionFailedAnnouncement(session.label));
    } else if (key === "awaiting-input") {
      // L6's InteractionBar alert covers the focused seat's pending question — skip exactly
      // that case so the same event is never announced twice.
      const barAnnounces =
        session.id === focusedSessionId && session.controlPendingInteraction !== undefined;
      if (!barAnnounces) announcements.push(sessionAwaitingInputAnnouncement(session.label));
    }
  }
  return { announcements, next };
}

let watcherRefs = 0;
let unsubscribeWatcher: (() => void) | null = null;

/** Refcounted sessionStore subscription driving the assertive region. */
export function startSeatStateAnnouncer(): () => void {
  watcherRefs += 1;
  if (watcherRefs === 1) {
    let previous = new Map<string, string>();
    // Seed silently from the current registry (transitions only, never the initial roster).
    for (const session of sessionStore.getState().sessions) {
      previous.set(session.id, seatVisualState(session).key);
    }
    unsubscribeWatcher = sessionStore.subscribe((state) => {
      const { announcements, next } = stateEntryAnnouncements(
        previous,
        state.sessions,
        sessionCockpitStore.getState().focusedSessionId,
      );
      previous = next;
      for (const text of announcements) announceAssertive(text);
    });
  }
  let released = false;
  return () => {
    if (released) return;
    released = true;
    watcherRefs -= 1;
    if (watcherRefs === 0) {
      unsubscribeWatcher?.();
      unsubscribeWatcher = null;
    }
  };
}

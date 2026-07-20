// The developer-ruled GLOBAL hide-thinking preference (design §12.2, §14.2). It is a UI preference,
// not per-harness behavior, and toggling it is instant and never deletes normalized content — the
// reducer keeps thinking items; only their rendering is suppressed. House persisted-store idiom
// (vanilla zustand seeded from localStorage), matching keymap/preferences.ts.

import { useStore } from "zustand";
import { createStore } from "zustand/vanilla";

const STORAGE_KEY = "cockpit.chats.hide-thinking.v1";

function readInitial(): boolean {
  try {
    return globalThis.localStorage?.getItem(STORAGE_KEY) === "1";
  } catch {
    return false;
  }
}

interface ThinkingPreferenceState {
  hidden: boolean;
  setHidden: (hidden: boolean) => void;
  toggle: () => void;
}

export const thinkingPreferenceStore = createStore<ThinkingPreferenceState>((set, get) => ({
  hidden: readInitial(),
  setHidden: (hidden) => {
    try {
      globalThis.localStorage?.setItem(STORAGE_KEY, hidden ? "1" : "0");
    } catch {
      // A private-mode storage failure must not break the toggle; it just won't persist.
    }
    set({ hidden });
  },
  toggle: () => get().setHidden(!get().hidden),
}));

export const useHideThinking = (): boolean =>
  useStore(thinkingPreferenceStore, (state) => state.hidden);

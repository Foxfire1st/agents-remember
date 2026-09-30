// The reviewer's inline / side-by-side choice for changed intent wording (MIK-R35 rule 4). It is a
// browser-local UI preference, in the house persisted-store idiom (vanilla zustand seeded from
// localStorage, as data/conversation/thinkingPreference.ts). It is not review state and no server
// store holds it. When storage is unavailable, the passage defaults to inline and the toggle still
// works for the page's lifetime.

import { useStore } from 'zustand';
import { createStore } from 'zustand/vanilla';

export type ProseLayout = 'inline' | 'side-by-side';

export const PROSE_LAYOUT_KEY = 'review.intent-diff.layout.v1';

export function readProseLayout(storage: Pick<Storage, 'getItem'> | undefined): ProseLayout {
  try {
    return storage?.getItem(PROSE_LAYOUT_KEY) === 'side-by-side' ? 'side-by-side' : 'inline';
  } catch {
    return 'inline';
  }
}

interface ProseLayoutState {
  layout: ProseLayout;
  setLayout: (layout: ProseLayout) => void;
}

function storage(): Storage | undefined {
  try {
    return globalThis.localStorage;
  } catch {
    return undefined;
  }
}

export const proseLayoutStore = createStore<ProseLayoutState>((set) => ({
  layout: readProseLayout(storage()),
  setLayout: (layout) => {
    try {
      storage()?.setItem(PROSE_LAYOUT_KEY, layout);
    } catch {
      // A private-mode storage failure must not break the toggle; it just won't persist.
    }
    set({ layout });
  },
}));

export const useProseLayout = (): ProseLayout =>
  useStore(proseLayoutStore, (state) => state.layout);

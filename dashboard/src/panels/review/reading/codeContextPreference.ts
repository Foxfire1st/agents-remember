// "Changes only | Full context" (MIK-R89 v2 rule 4): the review page's one control over how much of a
// card's recorded range its code shows. Changes only (the default) folds every unchanged stretch to
// "N unchanged lines" with three lines of context around each change; Full context shows the whole
// recorded range. The choice is a browser-local preference in the house idiom (vanilla zustand seeded
// from localStorage, as triageOrderPreference.ts), so it survives a remount and a reload. An unknown
// stored value, or no storage, reads as Changes only, and the control still works for the page's life.
import { useStore } from 'zustand';
import { createStore } from 'zustand/vanilla';

export type CodeContext = 'changes' | 'full';

export const CODE_CONTEXT_KEY = 'review.code-context.v1';

function storage(): Storage | undefined {
  try {
    return globalThis.localStorage;
  } catch {
    return undefined;
  }
}

function readCodeContext(store: Pick<Storage, 'getItem'> | undefined): CodeContext {
  try {
    return store?.getItem(CODE_CONTEXT_KEY) === 'full' ? 'full' : 'changes';
  } catch {
    return 'changes';
  }
}

interface CodeContextState {
  context: CodeContext;
  setContext: (context: CodeContext) => void;
}

export const codeContextStore = createStore<CodeContextState>((set) => ({
  context: readCodeContext(storage()),
  setContext: (context) => {
    try {
      storage()?.setItem(CODE_CONTEXT_KEY, context);
    } catch {
      // A private-mode storage failure keeps the choice for this page's life only.
    }
    set({ context });
  },
}));

export const useCodeContext = (): CodeContext => useStore(codeContextStore, (state) => state.context);

// Reads the stored choice again: a test that writes storage before mounting, or a second tab.
export function reloadCodeContextForTests(): void {
  codeContextStore.setState({ context: readCodeContext(storage()) });
}

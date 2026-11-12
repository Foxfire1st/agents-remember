// Browser-local outline view, stored like the existing tree order preference.
import { useStore } from 'zustand';
import { createStore } from 'zustand/vanilla';

export type TreeView = 'changed' | 'all';

export const TREE_VIEW_KEY = 'review.tree-view.v1';

export function readTreeView(storage: Pick<Storage, 'getItem'> | undefined): TreeView {
  try {
    return storage?.getItem(TREE_VIEW_KEY) === 'all' ? 'all' : 'changed';
  } catch {
    return 'changed';
  }
}

interface TreeViewState {
  view: TreeView;
  setView: (view: TreeView) => void;
}

function storage(): Storage | undefined {
  try {
    return globalThis.localStorage;
  } catch {
    return undefined;
  }
}

export const treeViewStore = createStore<TreeViewState>((set) => ({
  view: readTreeView(storage()),
  setView: (view) => {
    try {
      storage()?.setItem(TREE_VIEW_KEY, view);
    } catch {
      // A private-mode storage failure must not break the control; it just won't persist.
    }
    set({ view });
  },
}));

export const useTreeView = (): TreeView => useStore(treeViewStore, (state) => state.view);

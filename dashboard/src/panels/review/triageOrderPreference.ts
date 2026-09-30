// The family tree's order choice (MIK-R33, ICR-R32 rule 8): triage order by default, or pure authored
// order. It is a browser-local UI preference in the house persisted-store idiom (vanilla zustand
// seeded from localStorage, as intentDiffPreference.ts). It is not review state and no server store
// holds it. When storage is unavailable, triage order applies and the control still works for the
// page's lifetime.

import { useStore } from 'zustand';
import { createStore } from 'zustand/vanilla';

export type TreeOrder = 'triage' | 'authored';

export const TREE_ORDER_KEY = 'review.tree-order.v1';

export function readTreeOrder(storage: Pick<Storage, 'getItem'> | undefined): TreeOrder {
  try {
    return storage?.getItem(TREE_ORDER_KEY) === 'authored' ? 'authored' : 'triage';
  } catch {
    return 'triage';
  }
}

interface TreeOrderState {
  order: TreeOrder;
  setOrder: (order: TreeOrder) => void;
}

function storage(): Storage | undefined {
  try {
    return globalThis.localStorage;
  } catch {
    return undefined;
  }
}

export const treeOrderStore = createStore<TreeOrderState>((set) => ({
  order: readTreeOrder(storage()),
  setOrder: (order) => {
    try {
      storage()?.setItem(TREE_ORDER_KEY, order);
    } catch {
      // A private-mode storage failure must not break the control; it just won't persist.
    }
    set({ order });
  },
}));

export const useTreeOrder = (): TreeOrder => useStore(treeOrderStore, (state) => state.order);

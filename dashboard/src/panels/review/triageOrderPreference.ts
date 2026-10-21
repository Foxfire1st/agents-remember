// The review page's one Sort choice (MIK-R89 v2 rule 3; MIK-R86 v2 rule 4): Priority by default, or
// one of the other deterministic sorts. It is a browser-local UI preference in the house
// persisted-store idiom (vanilla zustand seeded from localStorage, as intentDiffPreference.ts). It is
// not review state and no server store holds it. When storage is unavailable, Priority applies and the
// control still works for the page's lifetime.
//
// The key `review.tree-order.v1` keeps the earlier Order control's stored value (W7): a stored
// `triage` reads as Priority, `authored` as Authored, and anything unknown as Priority (MIK-R89 v2
// Failure and Recovery).

import { useStore } from 'zustand';
import { createStore } from 'zustand/vanilla';

import { REVIEW_SORTS, type ReviewSort } from './reviewSelection';

export const TREE_ORDER_KEY = 'review.tree-order.v1';

export function sortOf(value: unknown): ReviewSort {
  if (value === 'triage') return 'priority';
  return REVIEW_SORTS.includes(value as ReviewSort) ? (value as ReviewSort) : 'priority';
}

export function readTreeOrder(storage: Pick<Storage, 'getItem'> | undefined): ReviewSort {
  try {
    return sortOf(storage?.getItem(TREE_ORDER_KEY));
  } catch {
    return 'priority';
  }
}

interface TreeOrderState {
  order: ReviewSort;
  setOrder: (order: ReviewSort) => void;
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

export const useTreeOrder = (): ReviewSort => useStore(treeOrderStore, (state) => state.order);

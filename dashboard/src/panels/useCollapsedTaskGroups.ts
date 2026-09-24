import { useCallback, useState } from "react";

const STORAGE_KEY = "operations.tasks.collapsed.v1";
// The second half of the collapse state (ICR-R33): a row the projection holds CLOSED BY DEFAULT (a
// master with no live worktree work, whose only children are its landed leaves) needs the reader's
// own "open it" to be remembered, exactly as an explicitly collapsed live master does. Keeping the
// two sets apart is what lets the v1 array keep its published meaning — a key in it is a row the
// READER collapsed — while the default rule stays a function of the projection and can therefore
// change as work lands or starts.
const OPENED_KEY = "operations.tasks.opened.v1";

function readKeys(key: string): Set<string> {
  if (typeof window === "undefined") return new Set();
  const stored = window.localStorage.getItem(key);
  return stored === null ? new Set() : new Set(JSON.parse(stored) as string[]);
}

function writeKeys(key: string, values: Set<string>): void {
  if (typeof window !== "undefined") {
    window.localStorage.setItem(key, JSON.stringify([...values]));
  }
}

export function useCollapsedTaskGroups(): {
  collapsedKeys: ReadonlySet<string>;
  openedKeys: ReadonlySet<string>;
  setCollapsed: (key: string, collapsed: boolean) => void;
} {
  const [collapsedKeys, setCollapsedKeys] = useState<Set<string>>(() => readKeys(STORAGE_KEY));
  const [openedKeys, setOpenedKeys] = useState<Set<string>>(() => readKeys(OPENED_KEY));

  // The RESOLVED next state arrives from the caller, which is the only layer that knows the row's
  // default (a function of the projection): `true` records the reader's own collapse, `false` the
  // reader's own open. A key never lives in both sets, so the effective state is unambiguous
  // whatever the projection does afterwards.
  const setCollapsed = useCallback((key: string, collapsed: boolean) => {
    setCollapsedKeys((current) => {
      const next = new Set(current);
      if (collapsed) next.add(key);
      else next.delete(key);
      writeKeys(STORAGE_KEY, next);
      return next;
    });
    setOpenedKeys((current) => {
      const next = new Set(current);
      if (collapsed) next.delete(key);
      else next.add(key);
      writeKeys(OPENED_KEY, next);
      return next;
    });
  }, []);

  return { collapsedKeys, openedKeys, setCollapsed };
}

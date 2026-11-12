import { useCallback, useState } from "react";

const STORAGE_KEY = "operations.tasks.collapsed.v1";

export function useCollapsedTaskGroups(): {
  collapsedKeys: ReadonlySet<string>;
  toggleCollapsed: (key: string) => void;
  collapse: (key: string) => void;
} {
  const [collapsedKeys, setCollapsedKeys] = useState<Set<string>>(() => {
    if (typeof window === "undefined") return new Set();
    const stored = window.localStorage.getItem(STORAGE_KEY);
    return stored === null ? new Set() : new Set(JSON.parse(stored) as string[]);
  });

  const update = useCallback((key: string, close: (current: Set<string>) => boolean) => {
    setCollapsedKeys((current) => {
      const next = new Set(current);
      if (close(current)) next.add(key);
      else next.delete(key);
      if (typeof window !== "undefined") {
        window.localStorage.setItem(STORAGE_KEY, JSON.stringify([...next]));
      }
      return next;
    });
  }, []);
  const toggleCollapsed = useCallback(
    (key: string) => update(key, (current) => !current.has(key)),
    [update],
  );
  // Closes the parent whatever the stored state says: a held parent is drawn open over a closed one.
  const collapse = useCallback((key: string) => update(key, () => true), [update]);

  return { collapsedKeys, toggleCollapsed, collapse };
}

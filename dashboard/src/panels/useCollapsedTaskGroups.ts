import { useCallback, useState } from "react";

const STORAGE_KEY = "operations.tasks.collapsed.v1";

export function useCollapsedTaskGroups(): {
  collapsedKeys: ReadonlySet<string>;
  toggleCollapsed: (key: string) => void;
} {
  const [collapsedKeys, setCollapsedKeys] = useState<Set<string>>(() => {
    if (typeof window === "undefined") return new Set();
    const stored = window.localStorage.getItem(STORAGE_KEY);
    return stored === null ? new Set() : new Set(JSON.parse(stored) as string[]);
  });

  const toggleCollapsed = useCallback((key: string) => {
    setCollapsedKeys((current) => {
      const next = new Set(current);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      if (typeof window !== "undefined") {
        window.localStorage.setItem(STORAGE_KEY, JSON.stringify([...next]));
      }
      return next;
    });
  }, []);

  return { collapsedKeys, toggleCollapsed };
}

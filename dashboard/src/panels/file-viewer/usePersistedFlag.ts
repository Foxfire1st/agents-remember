// A boolean useState backed by localStorage — the same pattern Cockpit.tsx uses for the
// effects ("calm-cockpit") toggle. Lets a view-mode choice (split/single, highlight-off)
// survive a page reload AND a file switch (the value lives outside the file-scoped state).
import { useCallback, useState } from "react";

export function usePersistedFlag(
  key: string,
  fallback: boolean,
): [boolean, (next: boolean) => void] {
  const [value, setValue] = useState<boolean>(() => {
    if (typeof window === "undefined") return fallback;
    const raw = window.localStorage.getItem(key);
    return raw === null ? fallback : raw === "1";
  });

  const set = useCallback(
    (next: boolean) => {
      setValue(next);
      if (typeof window !== "undefined") window.localStorage.setItem(key, next ? "1" : "0");
    },
    [key],
  );

  return [value, set];
}

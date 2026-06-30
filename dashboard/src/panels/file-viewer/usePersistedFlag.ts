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

// The numeric sibling of usePersistedFlag — a number useState backed by localStorage. The cockpit's
// resizable rails persist their pixel widths through this so a dragged column survives a page reload.
// A non-finite stored value (corrupt key) falls back instead of poisoning the layout with NaN.
export function usePersistedNumber(
  key: string,
  fallback: number,
): [number, (next: number) => void] {
  const [value, setValue] = useState<number>(() => {
    if (typeof window === "undefined") return fallback;
    const raw = window.localStorage.getItem(key);
    if (raw === null) return fallback;
    const parsed = Number(raw);
    return Number.isFinite(parsed) ? parsed : fallback;
  });

  const set = useCallback(
    (next: number) => {
      setValue(next);
      if (typeof window !== "undefined") window.localStorage.setItem(key, String(next));
    },
    [key],
  );

  return [value, set];
}

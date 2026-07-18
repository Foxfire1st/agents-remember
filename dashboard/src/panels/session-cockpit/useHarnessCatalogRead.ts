import { useCallback, useEffect, useRef, useState } from "react";

import { readHarnessCatalog, type HarnessCatalogRead } from "../../data/harnessCatalog";

export const HARNESS_CATALOG_TIMEOUT_MS = 5_000;

export type HarnessCatalogState =
  | { status: "idle" }
  | { status: "loading" }
  | HarnessCatalogRead
  | { status: "timeout" };

interface ActiveRead {
  controller: AbortController;
  timeout: number;
}

/**
 * Own one dialog-local catalog request at a time. Superseding, closing, and timeout all revoke the
 * old AbortController; a changed serving boot identity can start exactly one replacement read.
 */
export function useHarnessCatalogRead({
  open,
  servingBootedAt,
  timeoutMs = HARNESS_CATALOG_TIMEOUT_MS,
}: {
  open: boolean;
  servingBootedAt: string | null;
  timeoutMs?: number;
}) {
  const [catalog, setCatalog] = useState<HarnessCatalogState>({ status: "idle" });
  const activeRef = useRef<ActiveRead | null>(null);
  const observedBootRef = useRef<string | null>(null);

  const abortActive = useCallback(() => {
    const active = activeRef.current;
    if (!active) return;
    activeRef.current = null;
    window.clearTimeout(active.timeout);
    active.controller.abort();
  }, []);

  const read = useCallback(() => {
    abortActive();
    const controller = new AbortController();
    const active: ActiveRead = {
      controller,
      timeout: window.setTimeout(() => {
        if (activeRef.current !== active) return;
        activeRef.current = null;
        controller.abort();
        setCatalog({ status: "timeout" });
      }, timeoutMs),
    };
    activeRef.current = active;
    setCatalog({ status: "loading" });
    void readHarnessCatalog("", { signal: controller.signal }).then((result) => {
      if (activeRef.current !== active || controller.signal.aborted) return;
      activeRef.current = null;
      window.clearTimeout(active.timeout);
      setCatalog(result);
    });
  }, [abortActive, timeoutMs]);

  useEffect(() => {
    if (!open) {
      abortActive();
      setCatalog({ status: "idle" });
      return undefined;
    }
    observedBootRef.current = servingBootedAt;
    read();
    return abortActive;
  }, [abortActive, open, read]);

  useEffect(() => {
    if (!open || servingBootedAt === null) return;
    const previous = observedBootRef.current;
    observedBootRef.current = servingBootedAt;
    if (previous !== servingBootedAt) read();
  }, [open, read, servingBootedAt]);

  return { catalog, retry: read };
}

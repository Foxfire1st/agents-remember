import {
  useEffect,
  useLayoutEffect,
  useMemo,
  useState,
  type Dispatch,
  type RefObject,
  type SetStateAction,
} from "react";

import type { DisplayRow } from "../collapse";
import type { TimelineVirtualizer } from "./timelineControls";
import {
  INITIAL_PREMEASURE_BATCH_ROWS,
  INITIAL_PREMEASURE_MAX_ROWS,
  INITIAL_PREMEASURE_SLICE_DELAY_MS,
  MEASUREMENT_RESIZE_SETTLE_MS,
  readStoredMeasurements,
  storeMeasurements,
} from "./measurements";
import type { TimelineRefs } from "./timelineRefs";

type StoredMeasurements = ReturnType<typeof readStoredMeasurements>;

export function useStoredMeasurements(measurementCacheId: string | undefined) {
  // TanStack 3.17 can round-trip measured rows through initialMeasurementsCache. Persisting this
  // disposable UI geometry is the only way a browser refresh can know offscreen DOM heights
  // without rendering those rows again. sessionStorage keeps it tab-local; a window-width mismatch
  // rejects it immediately, and the exact viewport width is checked pre-paint on first reveal.
  const [storedMeasurementCache] = useState(() =>
    readStoredMeasurements(measurementCacheId),
  );
  const initialMeasurementsCache = useMemo(
    () => storedMeasurementCache?.items ?? [],
    [storedMeasurementCache],
  );
  return { storedMeasurementCache, initialMeasurementsCache };
}

export function useMeasurementCacheCompleteness(
  rows: DisplayRow[],
  initialMeasurementsCache: readonly { key: unknown; size: number }[],
) {
  return useMemo(() => {
    if (rows.length === 0 || initialMeasurementsCache.length < rows.length) {
      return false;
    }
    const cachedKeys = new Set(
      initialMeasurementsCache.map((item) => String(item.key)),
    );
    return rows.every((row) => cachedKeys.has(row.key));
  }, [initialMeasurementsCache, rows]);
}

export function usePremeasureEligibility(
  rows: DisplayRow[],
  hasCompleteInitialMeasurementCache: boolean,
  premeasureBatchStart: number | null,
  setPremeasureBatchStart: Dispatch<SetStateAction<number | null>>,
) {
  // An initially empty projection decides eligibility when its first page arrives. If that page
  // crossed the moderate bound while hydrating, it stays on the ordinary large-history path.
  useEffect(() => {
    if (premeasureBatchStart !== -1 || rows.length === 0) return;
    setPremeasureBatchStart(
      rows.length <= INITIAL_PREMEASURE_MAX_ROWS && !hasCompleteInitialMeasurementCache
        ? Math.max(0, rows.length - INITIAL_PREMEASURE_BATCH_ROWS)
        : null,
    );
  }, [hasCompleteInitialMeasurementCache, premeasureBatchStart, rows.length, setPremeasureBatchStart]);
}

export function usePremeasureSync(
  rows: DisplayRow[],
  visible: boolean,
  premeasureBatchStart: number | null,
  virtualizer: TimelineVirtualizer,
  virtualizerIsScrolling: boolean,
  scrollRef: RefObject<HTMLDivElement | null>,
  refs: TimelineRefs,
) {
  // This pass owns these measurements, so record the mounted batch synchronously instead of
  // depending on ref/ResizeObserver delivery. TanStack deliberately skips sync ref measurement
  // during user scrolling; a width refresh that began near scroll-end could otherwise miss a few
  // rows and stall forever on the final batch.
  useLayoutEffect(() => {
    if (
      !visible ||
      (refs.premeasurePausesForScrollRef.current && virtualizerIsScrolling) ||
      premeasureBatchStart === null ||
      premeasureBatchStart < 0 ||
      rows.length === 0
    ) {
      return;
    }
    const el = scrollRef.current;
    if (el === null) return;
    const batchStart = Math.min(premeasureBatchStart, rows.length);
    const batchEnd = Math.min(rows.length, batchStart + INITIAL_PREMEASURE_BATCH_ROWS);
    for (let index = batchStart; index < batchEnd; index += 1) {
      const rowElement = el.querySelector<HTMLElement>(`[data-index="${index}"]`);
      if (rowElement !== null) virtualizer.resizeItem(index, rowElement.offsetHeight);
    }
  }, [
    premeasureBatchStart,
    rows.length,
    virtualizer,
    virtualizerIsScrolling,
    visible,
    scrollRef,
    refs,
  ]);
}

export function usePremeasureSlice(
  rows: DisplayRow[],
  visible: boolean,
  premeasureBatchStart: number | null,
  virtualizerIsScrolling: boolean,
  refs: TimelineRefs,
  setPremeasureBatchStart: Dispatch<SetStateAction<number | null>>,
) {
  // Replace one batch with the next older batch in timer-separated slices. Previously the range
  // accumulated every warmed row, so the final slice still mounted all rich rows and caused a
  // measured 227–309ms task. Keeping only one batch lets React unmount each slice after TanStack has
  // retained its measured size, and yields back to the browser before the next slice. A display:none
  // inactive keep-alive deliberately owns no measurement work, so it waits until focused.
  useEffect(() => {
    if (
      !visible ||
      (refs.premeasurePausesForScrollRef.current && virtualizerIsScrolling) ||
      premeasureBatchStart === null ||
      premeasureBatchStart <= 0 ||
      rows.length === 0
    ) {
      return undefined;
    }
    const advance = () => {
      setPremeasureBatchStart((current) =>
        current === null || current < 0
          ? current
          : Math.max(0, current - INITIAL_PREMEASURE_BATCH_ROWS),
      );
    };
    const timeoutId = window.setTimeout(advance, INITIAL_PREMEASURE_SLICE_DELAY_MS);
    return () => window.clearTimeout(timeoutId);
  }, [
    premeasureBatchStart,
    rows.length,
    virtualizerIsScrolling,
    visible,
    refs,
    setPremeasureBatchStart,
  ]);
}

export function usePremeasureCompletion(
  rows: DisplayRow[],
  visible: boolean,
  premeasureBatchStart: number | null,
  totalSize: number,
  virtualizer: TimelineVirtualizer,
  scrollRef: RefObject<HTMLDivElement | null>,
  measurementCacheId: string | undefined,
  refs: TimelineRefs,
  setPremeasureBatchStart: Dispatch<SetStateAction<number | null>>,
) {
  // TanStack's documented snapshot contains only rows that have actually rendered and measured.
  // Once the sliding batch reached the start and the snapshot covers the page, remove the extra
  // range: upward scrolling now reads stable sizes while the DOM stays bounded.
  useLayoutEffect(() => {
    if (!visible || premeasureBatchStart !== 0 || rows.length === 0) return;
    const snapshot = virtualizer.takeSnapshot();
    if (snapshot.length < rows.length) return;
    const el = scrollRef.current;
    if (el !== null) {
      storeMeasurements(measurementCacheId, el.clientWidth, snapshot);
    }
    refs.premeasurePausesForScrollRef.current = false;
    setPremeasureBatchStart(null);
  }, [
    measurementCacheId,
    premeasureBatchStart,
    rows.length,
    totalSize,
    virtualizer,
    visible,
    scrollRef,
    refs,
    setPremeasureBatchStart,
  ]);
}

export function useMeasurementWidthInvalidation(
  rows: DisplayRow[],
  visible: boolean,
  initialMeasurementsCacheLength: number,
  storedMeasurementCache: StoredMeasurements,
  observedViewportWidth: number,
  virtualizer: TimelineVirtualizer,
  scrollRef: RefObject<HTMLDivElement | null>,
  refs: TimelineRefs,
  setPremeasureBatchStart: Dispatch<SetStateAction<number | null>>,
) {
  // A measured height is width-specific because Markdown wrapping determines row height. The
  // window-width check above cheaply rejects a stale cache before mount; this pre-paint check
  // catches both layout-only width changes (rail/inspector sizing) and later browser resizes. A
  // mismatch clears TanStack's sizes and runs the same bounded warm-up used for a cold page.
  useLayoutEffect(() => {
    if (!visible || rows.length === 0) return;
    const el = scrollRef.current;
    if (el === null || el.clientWidth <= 0) return;
    const previousWidth = refs.measurementWidthRef.current;
    refs.measurementWidthRef.current = el.clientWidth;
    const invalidateMeasurements = (viewportElement: HTMLDivElement) => {
      refs.premeasurePausesForScrollRef.current = true;
      virtualizer.measure();
      // measure() clears the size cache but does not remount rows already in the ordinary virtual
      // window. Record those connected nodes directly (measureElement deliberately skips synchronous
      // reads during user scrolling); the sliding batches cover every unmounted row after scroll-end.
      for (const rowElement of viewportElement.querySelectorAll<HTMLElement>(
        "[data-conversation-item]",
      )) {
        const index = Number(rowElement.dataset.index);
        virtualizer.resizeItem(index, rowElement.offsetHeight);
      }
      setPremeasureBatchStart(
        rows.length <= INITIAL_PREMEASURE_MAX_ROWS
          ? Math.max(0, rows.length - INITIAL_PREMEASURE_BATCH_ROWS)
          : null,
      );
    };
    if (previousWidth === null) {
      if (
        initialMeasurementsCacheLength === 0 ||
        storedMeasurementCache?.viewportWidth === el.clientWidth
      ) {
        return;
      }
      // A stale persisted cache must be gone before the first visible paint.
      invalidateMeasurements(el);
      return;
    }
    if (previousWidth === el.clientWidth) return;
    const settledWidth = el.clientWidth;
    const timeoutId = window.setTimeout(() => {
      const current = scrollRef.current;
      if (current !== null && current.clientWidth === settledWidth) {
        invalidateMeasurements(current);
      }
    }, MEASUREMENT_RESIZE_SETTLE_MS);
    return () => window.clearTimeout(timeoutId);
  }, [
    initialMeasurementsCacheLength,
    observedViewportWidth,
    rows.length,
    storedMeasurementCache?.viewportWidth,
    virtualizer,
    visible,
    scrollRef,
    refs,
    setPremeasureBatchStart,
  ]);
}

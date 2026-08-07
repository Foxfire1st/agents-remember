import { useMemo, useRef, useState } from "react";

import type { ConversationScrollMemory } from "../../../../data/conversation/store";
import type { ConversationItem } from "../../../../data/conversation/types";
import { groupUnknownVendorRuns, type DisplayRow } from "../collapse";
import {
  INITIAL_PREMEASURE_BATCH_ROWS,
  INITIAL_PREMEASURE_MAX_ROWS,
} from "./measurements";
import {
  useTimelineControls,
  useTimelineFocus,
  useTimelineVirtualizer,
} from "./timelineControls";
import {
  useFollowOnGrowth,
  useFollowLayout,
  useMeasureAnchor,
  useMeasureAnchorCommit,
  usePrependAnchor,
  useStreamedGrowthCount,
} from "./timelineFollow";
import {
  useMeasurementCacheCompleteness,
  useMeasurementWidthInvalidation,
  usePremeasureCompletion,
  usePremeasureEligibility,
  usePremeasureSlice,
  usePremeasureSync,
  useStoredMeasurements,
} from "./timelinePremeasure";
import { useTimelineRefs } from "./timelineRefs";
import {
  useRestoreApply,
  useRestoreArm,
  useRestoreDriver,
} from "./timelineRestore";
import {
  useScrollGeometry,
  useScrollListener,
  useTrustedInput,
} from "./timelineScroll";

export function useTimelineData({
  items,
  visible,
  restoreScroll,
  measurementCacheId,
}: {
  items: ConversationItem[];
  visible: boolean;
  restoreScroll: ConversationScrollMemory | undefined;
  measurementCacheId: string | undefined;
}) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const rows = useMemo<DisplayRow[]>(() => groupUnknownVendorRuns(items), [items]);
  const refs = useTimelineRefs(restoreScroll, visible);
  const becomingVisible = visible && !refs.wasVisibleRef.current;
  const { storedMeasurementCache, initialMeasurementsCache } =
    useStoredMeasurements(measurementCacheId);
  const hasCompleteInitialMeasurementCache = useMeasurementCacheCompleteness(
    rows,
    initialMeasurementsCache,
  );
  const [pendingUpdates, setPendingUpdates] = useState(0);
  const [awayFromLatest, setAwayFromLatest] = useState(
    () => restoreScroll?.atBottom === false,
  );
  const { focusedKey, setFocusedKey, focusedIndex } = useTimelineFocus(rows);
  // -1 waits across an empty hydration window; null means ineligible or consumed; otherwise this
  // is the first row in the ONE sliding premeasurement batch. Once consumed it never re-arms on
  // live appends, whose last row is already mounted by the ordinary bottom-follow range.
  const [premeasureBatchStart, setPremeasureBatchStart] = useState<number | null>(
    () =>
      rows.length === 0
        ? -1
        : rows.length <= INITIAL_PREMEASURE_MAX_ROWS &&
            !hasCompleteInitialMeasurementCache
          ? Math.max(0, rows.length - INITIAL_PREMEASURE_BATCH_ROWS)
          : null,
  );
  const virtualizer = useTimelineVirtualizer({
    rows,
    visible,
    focusedIndex,
    premeasureBatchStart,
    becomingVisible,
    scrollRef,
    refs,
    initialMeasurementsCache,
  });
  const virtualRows = virtualizer.getVirtualItems();
  // `scrollRect` is honestly `Rect | null` — TanStack backfills it from `initialRect` inside
  // getSize(), never on the field, so nothing here may assume an observed rect exists.
  const observedViewportWidth = virtualizer.scrollRect?.width ?? 0;
  const virtualizerIsScrolling = virtualizer.isScrolling;
  // The follow-on-growth signals. totalSize moves on every append-block delta (the last row
  // grows in place) and on every re-measure; lastRowSize is the last row's MEASURED size (the
  // range extractor pins the last row, so it is always mounted) — the streamed-update signal.
  const totalSize = virtualizer.getTotalSize();
  const lastVirtualRow =
    virtualRows.length > 0 ? virtualRows[virtualRows.length - 1] : undefined;
  const lastRowSize =
    lastVirtualRow !== undefined && lastVirtualRow.index === rows.length - 1
      ? lastVirtualRow.size
      : undefined;
  return {
    scrollRef,
    rows,
    refs,
    becomingVisible,
    storedMeasurementCache,
    initialMeasurementsCache,
    hasCompleteInitialMeasurementCache,
    pendingUpdates,
    setPendingUpdates,
    awayFromLatest,
    setAwayFromLatest,
    focusedKey,
    setFocusedKey,
    focusedIndex,
    premeasureBatchStart,
    setPremeasureBatchStart,
    virtualizer,
    virtualRows,
    observedViewportWidth,
    virtualizerIsScrolling,
    totalSize,
    lastRowSize,
  };
}

export function useTimelineEffects(
  data: ReturnType<typeof useTimelineData>,
  {
    visible,
    restoreScroll,
    onScrollMemory,
    measurementCacheId,
  }: {
    visible: boolean;
    restoreScroll: ConversationScrollMemory | undefined;
    onScrollMemory: ((memory: ConversationScrollMemory) => void) | undefined;
    measurementCacheId: string | undefined;
  },
) {
  usePremeasureEligibility(data.rows, data.hasCompleteInitialMeasurementCache, data.premeasureBatchStart, data.setPremeasureBatchStart);
  usePremeasureSync(data.rows, visible, data.premeasureBatchStart, data.virtualizer, data.virtualizerIsScrolling, data.scrollRef, data.refs);
  usePremeasureSlice(data.rows, visible, data.premeasureBatchStart, data.virtualizerIsScrolling, data.refs, data.setPremeasureBatchStart);
  usePremeasureCompletion(data.rows, visible, data.premeasureBatchStart, data.totalSize, data.virtualizer, data.scrollRef, measurementCacheId, data.refs, data.setPremeasureBatchStart);
  useMeasurementWidthInvalidation(data.rows, visible, data.initialMeasurementsCache.length, data.storedMeasurementCache, data.observedViewportWidth, data.virtualizer, data.scrollRef, data.refs, data.setPremeasureBatchStart);
  const recordMeasureAnchor = useMeasureAnchor(data.rows, data.virtualizer, data.refs);
  const handleScroll = useScrollGeometry(data.rows, data.virtualizer, data.scrollRef, data.refs, onScrollMemory, data.setAwayFromLatest, data.setPendingUpdates, recordMeasureAnchor);
  useScrollListener(visible, data.scrollRef, handleScroll);
  useTrustedInput(visible, data.scrollRef, data.refs);
  useFollowLayout(data.rows, visible, data.becomingVisible, data.virtualizer, data.scrollRef, data.refs, data.setPendingUpdates);
  useFollowOnGrowth(data.rows, visible, data.becomingVisible, data.totalSize, data.scrollRef, data.refs);
  useStreamedGrowthCount(data.rows, visible, data.becomingVisible, data.lastRowSize, data.refs, data.setPendingUpdates);
  useRestoreArm(visible, restoreScroll, data.refs);
  useRestoreApply(data.rows, data.scrollRef, data.refs, data.setPendingUpdates, data.setAwayFromLatest);
  useRestoreDriver(visible, data.rows, data.scrollRef, data.refs);
  usePrependAnchor(data.rows, visible, data.becomingVisible, data.virtualizer, data.refs);
  useMeasureAnchorCommit(data.rows, visible, data.becomingVisible, data.virtualizer, data.virtualRows, data.scrollRef, data.refs);
  return useTimelineControls(data.rows, data.virtualizer, data.setFocusedKey, data.focusedIndex, data.refs, data.scrollRef, data.setPendingUpdates, data.setAwayFromLatest);
}

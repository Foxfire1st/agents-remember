// The one navigable role="feed" (design §14.2, §14.3). Virtualized by stable conversation item (never
// by rendered line): DOM pruning is independent of the store/history authority. Each row is an
// <article> with aria-posinset from the server globalOrdinal and aria-setsize ONLY when totalItems is
// honestly known (else omitted; paging copy says "total unknown"). A roving tabindex + a focus-pinning
// range extractor (which also pins the DEFAULT tab row — F18) keep a tabbable article mounted even
// when it scrolls out, so incoming data can never relocate focus to the container. Bottom-follow keeps
// alignment only when the operator is near bottom; otherwise scroll stays fixed and a NON-animated
// "N new updates" button appears. Older paging preserves the top stable row + pixel offset. Keyboard
// navigation is handled on the feed widget itself (the ARIA feed pattern), not a global document
// handler (§14.4); its exclusion list is complete and Home/End are exempt inside labeled overflow
// regions so they scroll the region instead of navigating (F14). Consecutive identical unknown-vendor
// evidence collapses to one expandable row (F10) while every other item keeps its own article.

import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { defaultRangeExtractor, useVirtualizer, type Range } from "@tanstack/react-virtual";

import { css } from "../../../../styled-system/css";
import type { ConversationItem } from "../../../data/conversation/types";
import { groupUnknownVendorRuns, type DisplayRow } from "./collapse";
import { ConversationItemView, itemAccessibleName } from "./ConversationItemView";

const BOTTOM_FOLLOW_PX = 120;

const viewport = css({
  flex: "1",
  minHeight: "0",
  overflowY: "auto",
  overflowX: "hidden",
  position: "relative",
  outline: "none",
});
const feedInner = css({ position: "relative", width: "100%" });
const rowShell = css({
  position: "absolute",
  top: "0",
  left: "0",
  width: "100%",
  paddingBlock: "0.35rem",
  borderBottomWidth: "1px",
  borderBottomStyle: "solid",
  borderBottomColor: "color-mix(in oklch, token(colors.grid) 45%, transparent)",
  _focusVisible: { outline: "1px solid token(colors.amber)", outlineOffset: "-1px" },
});
const newUpdates = css({
  position: "absolute",
  bottom: "0.6rem",
  left: "50%",
  transform: "translateX(-50%)",
  font: "inherit",
  fontSize: "0.68rem",
  color: "ink",
  background: "bgPanel",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "amber",
  borderRadius: "3px",
  paddingInline: "0.5rem",
  paddingBlock: "0.15rem",
  cursor: "pointer",
  boxShadow: "0 4px 14px oklch(0 0 0 / 0.4)",
  _focusVisible: { outline: "1px solid token(colors.amber)", outlineOffset: "1px" },
});
const olderBar = css({ display: "flex", justifyContent: "center", paddingBlock: "0.3rem" });
const olderButton = css({
  font: "inherit",
  fontSize: "0.66rem",
  color: "muted",
  background: "transparent",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "grid",
  borderRadius: "2px",
  paddingInline: "0.5rem",
  paddingBlock: "0.1rem",
  cursor: "pointer",
  _hover: { color: "amber", borderColor: "amber" },
  _focusVisible: { outline: "1px solid token(colors.amber)", outlineOffset: "1px" },
});
const runRow = css({ display: "grid", gap: "0.15rem", color: "dormant", fontSize: "0.68rem" });
const runHead = css({ display: "flex", gap: "0.4rem", alignItems: "baseline" });
const runButton = css({
  font: "inherit",
  fontSize: "0.62rem",
  color: "muted",
  background: "transparent",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "grid",
  borderRadius: "2px",
  paddingInline: "0.35rem",
  cursor: "pointer",
  _hover: { color: "amber", borderColor: "amber" },
  _focusVisible: { outline: "1px solid token(colors.amber)", outlineOffset: "1px" },
});
const runMember = css({ fontFamily: "mono", fontSize: "0.6rem", color: "dormant", overflowWrap: "anywhere" });

function isEditableTarget(target: HTMLElement): boolean {
  return (
    target.tagName === "INPUT" ||
    target.tagName === "TEXTAREA" ||
    target.tagName === "SELECT" ||
    target.isContentEditable ||
    target.closest("button,a,[contenteditable],.cm-editor") !== null
  );
}

function inOverflowRegion(target: HTMLElement): boolean {
  // Labeled code/diff/output scroll regions own Home/End so they scroll rather than navigate (F14).
  return target.closest('[role="group"], pre') !== null;
}

function UnknownVendorRun({
  row,
  expanded,
  onToggle,
}: {
  row: Extract<DisplayRow, { kind: "unknown-run" }>;
  expanded: boolean;
  onToggle: () => void;
}) {
  return (
    <div className={runRow} data-testid="unknown-vendor-run">
      <div className={runHead}>
        <span>
          {row.items.length} identical unknown vendor events — {row.summary}
        </span>
        <button
          type="button"
          className={runButton}
          aria-expanded={expanded}
          onClick={onToggle}
          data-testid="unknown-vendor-run-toggle"
        >
          {expanded ? "collapse" : "show each"}
        </button>
      </div>
      {expanded ? (
        <div>
          {row.items.map((item) => (
            <div className={runMember} key={item.itemId} data-testid="unknown-vendor-run-member">
              #{item.globalOrdinal} · {item.evidenceRef ?? item.itemId}
            </div>
          ))}
        </div>
      ) : null}
    </div>
  );
}

export interface ConversationTimelineProps {
  items: ConversationItem[];
  totalItems?: number;
  hasOlder: boolean;
  busy: boolean;
  onLoadOlder: () => void;
  /** Records the top-visible stable row + offset so an older prepend can restore the anchor. */
  onScrollAnchor?: (anchor: { itemId: string; offsetPx: number }) => void;
}

export function ConversationTimeline({
  items,
  totalItems,
  hasOlder,
  busy,
  onLoadOlder,
  onScrollAnchor,
}: ConversationTimelineProps) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const [focusedKey, setFocusedKey] = useState<string | null>(null);
  const [expandedRuns, setExpandedRuns] = useState<Set<string>>(() => new Set());
  const [pendingUpdates, setPendingUpdates] = useState(0);
  const nearBottomRef = useRef(true);
  const prevLastKeyRef = useRef<string | null>(null);
  const prevFirstKeyRef = useRef<string | null>(null);
  const anchorRef = useRef<{ itemId: string; offsetPx: number } | null>(null);

  const rows = useMemo<DisplayRow[]>(() => groupUnknownVendorRuns(items), [items]);

  const focusedIndex = useMemo(
    () => (focusedKey === null ? -1 : rows.findIndex((row) => row.key === focusedKey)),
    [rows, focusedKey],
  );

  // Pin the focused row AND the default-tab row (the last row) so a tabbable article always exists,
  // even when both scroll out of the window (§14.3, F18).
  const rangeExtractor = useCallback(
    (range: Range) => {
      const base = new Set(defaultRangeExtractor(range));
      if (focusedIndex >= 0) base.add(focusedIndex);
      if (rows.length > 0) base.add(rows.length - 1);
      return [...base].sort((a, b) => a - b);
    },
    [focusedIndex, rows.length],
  );

  const virtualizer = useVirtualizer({
    count: rows.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => 80,
    overscan: 8,
    getItemKey: (index) => rows[index]?.key ?? index,
    rangeExtractor,
  });

  const handleScroll = useCallback(() => {
    const el = scrollRef.current;
    if (el === null) return;
    const distance = el.scrollHeight - el.scrollTop - el.clientHeight;
    nearBottomRef.current = distance <= BOTTOM_FOLLOW_PX;
    if (nearBottomRef.current) setPendingUpdates(0);
    const first = virtualizer.getVirtualItems()[0];
    if (first !== undefined) {
      const row = rows[first.index];
      if (row !== undefined) {
        const offsetPx = first.start - el.scrollTop;
        anchorRef.current = { itemId: row.key, offsetPx };
        onScrollAnchor?.({ itemId: row.key, offsetPx });
      }
    }
  }, [rows, onScrollAnchor, virtualizer]);

  useLayoutEffect(() => {
    const lastKey = rows.length > 0 ? rows[rows.length - 1].key : null;
    if (lastKey !== prevLastKeyRef.current && prevLastKeyRef.current !== null) {
      if (nearBottomRef.current) {
        virtualizer.scrollToIndex(rows.length - 1, { align: "end" });
      } else {
        setPendingUpdates((count) => count + 1);
      }
    }
    prevLastKeyRef.current = lastKey;
  }, [rows, virtualizer]);

  useLayoutEffect(() => {
    const firstKey = rows.length > 0 ? rows[0].key : null;
    const anchor = anchorRef.current;
    if (firstKey !== prevFirstKeyRef.current && prevFirstKeyRef.current !== null && anchor !== null) {
      const anchorIndex = rows.findIndex((row) => row.key === anchor.itemId);
      if (anchorIndex >= 0) virtualizer.scrollToIndex(anchorIndex, { align: "start" });
    }
    prevFirstKeyRef.current = firstKey;
  }, [rows, virtualizer]);

  const focusRowByIndex = useCallback(
    (index: number) => {
      if (index < 0 || index >= rows.length) return;
      const row = rows[index];
      setFocusedKey(row.key);
      virtualizer.scrollToIndex(index, { align: "auto" });
      requestAnimationFrame(() => {
        scrollRef.current
          ?.querySelector<HTMLElement>(`[data-row-key="${CSS.escape(row.key)}"]`)
          ?.focus();
      });
    },
    [rows, virtualizer],
  );

  const onKeyDown = useCallback(
    (event: React.KeyboardEvent<HTMLDivElement>) => {
      const target = event.target as HTMLElement;
      if (isEditableTarget(target)) return;
      const current = focusedIndex < 0 ? rows.length - 1 : focusedIndex;
      switch (event.key) {
        case "]":
          event.preventDefault();
          focusRowByIndex(Math.min(rows.length - 1, current + 1));
          break;
        case "[":
          event.preventDefault();
          focusRowByIndex(Math.max(0, current - 1));
          break;
        case "Home":
          // A labeled overflow region or an active text selection owns Home/End (do not hijack).
          if (inOverflowRegion(target) || !(window.getSelection()?.isCollapsed ?? true)) return;
          event.preventDefault();
          focusRowByIndex(0);
          break;
        case "End":
          if (inOverflowRegion(target) || !(window.getSelection()?.isCollapsed ?? true)) return;
          event.preventDefault();
          focusRowByIndex(rows.length - 1);
          break;
        default:
          break;
      }
    },
    [focusedIndex, focusRowByIndex, rows.length],
  );

  useEffect(() => {
    const el = scrollRef.current;
    if (el === null) return undefined;
    el.addEventListener("scroll", handleScroll, { passive: true });
    return () => el.removeEventListener("scroll", handleScroll);
  }, [handleScroll]);

  const toggleRun = useCallback((key: string) => {
    setExpandedRuns((current) => {
      const next = new Set(current);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }, []);

  const virtualRows = virtualizer.getVirtualItems();
  const knownTotal = typeof totalItems === "number" ? totalItems : undefined;

  return (
    <div ref={scrollRef} className={viewport} data-testid="conversation-viewport" data-kbzone="chrome">
      {hasOlder ? (
        <div className={olderBar}>
          <button
            type="button"
            className={olderButton}
            onClick={onLoadOlder}
            disabled={busy}
            data-testid="conversation-load-older"
          >
            {busy ? "loading…" : knownTotal !== undefined ? "Load older" : "Load older (total unknown)"}
          </button>
        </div>
      ) : null}
      <div
        role="feed"
        aria-label="Conversation"
        aria-busy={busy}
        onKeyDown={onKeyDown}
        className={feedInner}
        style={{ height: `${virtualizer.getTotalSize()}px` }}
        data-testid="conversation-feed"
      >
        {virtualRows.map((virtualRow) => {
          const row = rows[virtualRow.index];
          if (row === undefined) return null;
          const posinset = row.kind === "item" ? row.item.globalOrdinal : row.ordinal;
          const label =
            row.kind === "item"
              ? itemAccessibleName(row.item)
              : `${row.items.length} identical unknown vendor events`;
          const tabbable =
            focusedKey === row.key || (focusedKey === null && virtualRow.index === rows.length - 1);
          return (
            <article
              key={virtualRow.key}
              ref={virtualizer.measureElement}
              data-index={virtualRow.index}
              data-row-key={row.key}
              data-conversation-item
              tabIndex={tabbable ? 0 : -1}
              aria-label={label}
              aria-posinset={posinset}
              {...(knownTotal !== undefined ? { "aria-setsize": knownTotal } : {})}
              aria-live="off"
              className={rowShell}
              style={{ transform: `translateY(${virtualRow.start}px)` }}
              onFocus={() => setFocusedKey(row.key)}
            >
              {row.kind === "item" ? (
                <ConversationItemView item={row.item} />
              ) : (
                <UnknownVendorRun row={row} expanded={expandedRuns.has(row.key)} onToggle={() => toggleRun(row.key)} />
              )}
            </article>
          );
        })}
      </div>
      {pendingUpdates > 0 ? (
        <button
          type="button"
          className={newUpdates}
          data-testid="conversation-new-updates"
          onClick={() => {
            setPendingUpdates(0);
            nearBottomRef.current = true;
            virtualizer.scrollToIndex(rows.length - 1, { align: "end" });
          }}
        >
          {pendingUpdates} new {pendingUpdates === 1 ? "update" : "updates"}
        </button>
      ) : null}
    </div>
  );
}

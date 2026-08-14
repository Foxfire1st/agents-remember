import { useState, type ReactNode } from "react";

import { useVirtualizer } from "@tanstack/react-virtual";

import { css } from "../../../styled-system/css";

// Inspector ledgers stay ordinary DOM lists through 100 rows (better find/AT behavior for the
// common case) and virtualize only beyond that threshold. Both paths keep the same 2px raw-ledger
// accent and list semantics; virtualization is a render boundary, never a silent data cap.

export const INSPECTOR_VIRTUALIZE_THRESHOLD = 100;

const viewport = css({ maxHeight: "20rem", minHeight: "3rem", overflowY: "auto" });
const list = css({
  position: "relative",
  display: "grid",
  gap: "0.25rem",
  margin: "0",
  padding: "0",
  listStyle: "none",
  minWidth: "0",
});
const item = css({
  minWidth: "0",
  paddingInlineStart: "0.4rem",
  paddingBlock: "0.2rem",
  borderLeftWidth: "2px",
  borderLeftStyle: "solid",
  borderLeftColor: "amber",
  background: "bg",
  color: "ink",
  fontSize: "0.66rem",
  lineHeight: "1.4",
  overflowWrap: "anywhere",
});

const ROW_ESTIMATE = 58;

export function VirtualizedInspectorList<T>({
  rows,
  rowKey,
  renderRow,
  label,
  testId,
}: {
  rows: readonly T[];
  rowKey: (row: T, index: number) => string;
  renderRow: (row: T, index: number) => ReactNode;
  label: string;
  testId: string;
}) {
  const virtualized = rows.length > INSPECTOR_VIRTUALIZE_THRESHOLD;
  const [scrollEl, setScrollEl] = useState<HTMLDivElement | null>(null);
  const virtualizer = useVirtualizer({
    count: virtualized ? rows.length : 0,
    getScrollElement: () => scrollEl,
    estimateSize: () => ROW_ESTIMATE,
    overscan: 8,
    getItemKey: (index) => rowKey(rows[index], index),
  });

  if (!virtualized) {
    return (
      <ul className={list} aria-label={label} data-testid={testId} data-virtualized="false">
        {rows.map((row, index) => (
          <li key={rowKey(row, index)} className={item} data-testid={`${testId}-item`}>
            {renderRow(row, index)}
          </li>
        ))}
      </ul>
    );
  }

  return (
    <div ref={setScrollEl} className={viewport} data-testid={`${testId}-scroll`}>
      <ul
        className={list}
        aria-label={label}
        data-testid={testId}
        data-virtualized="true"
        style={{ height: `${virtualizer.getTotalSize()}px`, display: "block" }}
      >
        {virtualizer.getVirtualItems().map((virtualRow) => (
          <li
            key={virtualRow.key}
            ref={virtualizer.measureElement}
            data-index={virtualRow.index}
            aria-posinset={virtualRow.index + 1}
            aria-setsize={rows.length}
            className={item}
            data-testid={`${testId}-item`}
            style={{
              position: "absolute",
              top: 0,
              left: 0,
              width: "100%",
              transform: `translateY(${virtualRow.start}px)`,
            }}
          >
            {renderRow(rows[virtualRow.index], virtualRow.index)}
          </li>
        ))}
      </ul>
    </div>
  );
}

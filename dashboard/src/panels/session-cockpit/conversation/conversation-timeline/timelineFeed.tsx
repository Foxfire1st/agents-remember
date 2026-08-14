import type {
  HTMLAttributes,
  ReactNode,
  RefObject,
} from "react";

import { cx } from "../../../../../styled-system/css";
import type { ConversationItem } from "../../../../data/conversation/types";
import type { DisplayRow } from "../collapse";
import { ConversationItemView, itemAccessibleName } from "../ConversationItemView";
import { ThinkingItem } from "../ThinkingItem";
import {
  emptyInWell,
  feedInner,
  latestChip,
  latestChipWithUpdates,
  olderBar,
  olderButton,
  rowShell,
  viewport,
  viewportShell,
} from "./styles";
import { UnknownVendorRun } from "./unknownRun";

// The feed surface owns the ARIA feed contract (label + busy); layout, keyboard, and test
// attributes arrive through the component boundary so the widget pattern stays intact.
function FeedSurface({
  busy,
  children,
  ...props
}: { busy: boolean; children: ReactNode } & HTMLAttributes<HTMLDivElement>) {
  return (
    <div role="feed" aria-label="Conversation" aria-busy={busy} {...props}>
      {children}
    </div>
  );
}

// One feed row: a focus-managed article. The roving tab stop is computed here from the row's
// focus state so the ARIA feed pattern (one tabbable article at a time) survives the a11y rail.
function FeedArticle({
  rowRef,
  tabbable,
  children,
  ...props
}: {
  rowRef: (node: HTMLElement | null) => void;
  tabbable: boolean;
  children: ReactNode;
} & HTMLAttributes<HTMLElement>) {
  return (
    <article
      ref={rowRef}
      data-conversation-item
      {...props}
      {...(tabbable ? { tabIndex: 0 } : { tabIndex: -1 })}
    >
      {children}
    </article>
  );
}

function OlderBar({
  busy,
  knownTotal,
  onLoadOlder,
}: {
  busy: boolean;
  knownTotal: number | undefined;
  onLoadOlder: () => void;
}) {
  return (
    <div className={olderBar}>
      <button
        type="button"
        className={olderButton}
        onClick={onLoadOlder}
        disabled={busy}
        data-testid="conversation-load-older"
      >
        {busy
          ? "loading…"
          : knownTotal !== undefined
            ? "Load older"
            : "Load older (total unknown)"}
      </button>
    </div>
  );
}

function LatestChip({
  pendingUpdates,
  onLatest,
}: {
  pendingUpdates: number;
  onLatest: () => void;
}) {
  return (
    <button
      type="button"
      className={cx(latestChip, pendingUpdates > 0 && latestChipWithUpdates)}
      aria-label={
        pendingUpdates > 0
          ? `Jump to latest, ${pendingUpdates} new ${pendingUpdates === 1 ? "update" : "updates"}`
          : "Jump to latest"
      }
      title="jump to latest"
      data-testid="conversation-scroll-latest"
      onClick={onLatest}
    >
      <span aria-hidden="true">↓</span>
      {pendingUpdates > 0 ? (
        <span data-testid="conversation-new-updates">{pendingUpdates} new</span>
      ) : (
        <span>latest</span>
      )}
    </button>
  );
}

function FeedRow({
  virtualRow,
  rows,
  focusedKey,
  onRowFocus,
  expandedRuns,
  onToggleRun,
  rowRefFor,
  knownTotal,
}: {
  virtualRow: {
    key: unknown;
    index: number;
    start: number;
    size: number;
  };
  rows: DisplayRow[];
  focusedKey: string | null;
  onRowFocus: (key: string) => void;
  expandedRuns: ReadonlySet<string>;
  onToggleRun: (key: string) => void;
  rowRefFor: (node: HTMLElement | null) => void;
  knownTotal: number | undefined;
}) {
  const row = rows[virtualRow.index];
  if (row === undefined) return null;
  const posinset = row.kind === "item" ? row.item.globalOrdinal : row.ordinal;
  const label =
    row.kind === "item"
      ? itemAccessibleName(row.item)
      : row.kind === "live-thinking"
        ? "thinking in progress"
        : `${row.items.length} identical unknown vendor events`;
  const tabbable =
    focusedKey === row.key ||
    (focusedKey === null && virtualRow.index === rows.length - 1);
  return (
    <FeedArticle
      key={String(virtualRow.key)}
      rowRef={rowRefFor}
      tabbable={tabbable}
      data-index={virtualRow.index}
      data-row-key={row.key}
      aria-label={label}
      aria-posinset={posinset}
      {...(knownTotal !== undefined ? { "aria-setsize": knownTotal } : {})}
      aria-live="off"
      className={rowShell}
      style={{ transform: `translateY(${virtualRow.start}px)` }}
      onFocus={() => onRowFocus(row.key)}
    >
      {row.kind === "item" ? (
        <ConversationItemView item={row.item} />
      ) : row.kind === "live-thinking" ? (
        <ThinkingItem item={row.item} animated />
      ) : (
        <UnknownVendorRun
          row={row}
          expanded={expandedRuns.has(row.key)}
          onToggle={() => onToggleRun(row.key)}
        />
      )}
    </FeedArticle>
  );
}

function FeedViewport({
  scrollRef,
  rows,
  emptyNote,
  hasOlder,
  busy,
  knownTotal,
  onLoadOlder,
  onKeyDown,
  totalSize,
  virtualRows,
  focusedKey,
  onRowFocus,
  expandedRuns,
  onToggleRun,
  rowRefFor,
}: {
  scrollRef: RefObject<HTMLDivElement | null>;
  rows: DisplayRow[];
  emptyNote: ReactNode;
  hasOlder: boolean;
  busy: boolean;
  knownTotal: number | undefined;
  onLoadOlder: () => void;
  onKeyDown: (event: React.KeyboardEvent<HTMLDivElement>) => void;
  totalSize: number;
  virtualRows: readonly {
    key: unknown;
    index: number;
    start: number;
    size: number;
  }[];
  focusedKey: string | null;
  onRowFocus: (key: string) => void;
  expandedRuns: ReadonlySet<string>;
  onToggleRun: (key: string) => void;
  rowRefFor: (node: HTMLElement | null) => void;
}) {
  return (
    <div
      ref={scrollRef}
      className={viewport}
      data-testid="conversation-viewport"
      data-kbzone="chrome"
    >
      {emptyNote !== undefined && rows.length === 0 ? (
        <div className={emptyInWell} data-testid="conversation-empty">
          {emptyNote}
        </div>
      ) : null}
      {hasOlder ? (
        <OlderBar busy={busy} knownTotal={knownTotal} onLoadOlder={onLoadOlder} />
      ) : null}
      <FeedSurface
        busy={busy}
        onKeyDown={onKeyDown}
        className={feedInner}
        style={{ height: `${totalSize}px` }}
        data-testid="conversation-feed"
      >
        {virtualRows.map((virtualRow) => (
          <FeedRow
            key={String(virtualRow.key)}
            virtualRow={virtualRow}
            rows={rows}
            focusedKey={focusedKey}
            onRowFocus={onRowFocus}
            expandedRuns={expandedRuns}
            onToggleRun={onToggleRun}
            rowRefFor={rowRefFor}
            knownTotal={knownTotal}
          />
        ))}
      </FeedSurface>
    </div>
  );
}

export function TimelineFeed({
  scrollRef,
  rows,
  emptyNote,
  hasOlder,
  busy,
  knownTotal,
  onLoadOlder,
  onKeyDown,
  totalSize,
  virtualRows,
  focusedKey,
  onRowFocus,
  expandedRuns,
  onToggleRun,
  rowRefFor,
  latestVisible,
  pendingUpdates,
  onLatest,
}: {
  scrollRef: RefObject<HTMLDivElement | null>;
  rows: DisplayRow[];
  emptyNote: ReactNode;
  hasOlder: boolean;
  busy: boolean;
  knownTotal: number | undefined;
  onLoadOlder: () => void;
  onKeyDown: (event: React.KeyboardEvent<HTMLDivElement>) => void;
  totalSize: number;
  virtualRows: readonly {
    key: unknown;
    index: number;
    start: number;
    size: number;
  }[];
  focusedKey: string | null;
  onRowFocus: (key: string) => void;
  expandedRuns: ReadonlySet<string>;
  onToggleRun: (key: string) => void;
  rowRefFor: (node: HTMLElement | null) => void;
  latestVisible: boolean;
  pendingUpdates: number;
  onLatest: () => void;
}) {
  return (
    <div className={viewportShell}>
      <FeedViewport
        scrollRef={scrollRef}
        rows={rows}
        emptyNote={emptyNote}
        hasOlder={hasOlder}
        busy={busy}
        knownTotal={knownTotal}
        onLoadOlder={onLoadOlder}
        onKeyDown={onKeyDown}
        totalSize={totalSize}
        virtualRows={virtualRows}
        focusedKey={focusedKey}
        onRowFocus={onRowFocus}
        expandedRuns={expandedRuns}
        onToggleRun={onToggleRun}
        rowRefFor={rowRefFor}
      />
      {/* The latest chip lives OUTSIDE the scroller (a child of the shell): absolutely positioned
          children of a scroll container scroll away with its content. */}
      {latestVisible ? (
        <LatestChip pendingUpdates={pendingUpdates} onLatest={onLatest} />
      ) : null}
    </div>
  );
}

export type { ConversationItem };

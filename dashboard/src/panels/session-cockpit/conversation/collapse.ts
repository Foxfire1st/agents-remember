// Visual grouping for the feed's flat display list (design §12.2; round-1 F10; 260731-EFA-L7 R15).
// A brand-new codex session can emit a wall of identical `unknown vendor event` rows; §12.2
// permits summarizing consecutive items visually AS LONG AS each underlying item stays addressable
// and identity is never mutated. A run of >=3 identical unknown-vendor items collapses to one
// de-emphasized row (expandable to its members); every other item passes through unchanged,
// keeping its own article + server ordinal.
//
// Live reasoning coalescing (R15): repeated empty/in-progress thinking updates for one active
// turn render as ONE stable animated `live-thinking` row keyed by the active turn/activity
// identity (turn id + agent thread), never one row per vendor reasoning item id. The indicator
// opens on the first empty in-progress thinking item of a turn and closes on the first
// substantive or completed reasoning item for that same identity (or its turn-result), so
// completion finalizes it exactly once. Completed reasoning that contains real user-visible
// content is untouched and renders as ordinary transcript content. In-progress reasoning that
// carries real content while the indicator is open UPDATES that one live row (F3 pin): the
// content is shown inside the stable animated row instead of adding a second ordinary row
// alongside the still-open indicator.

import type { ConversationItem } from "../../../data/conversation/types";

const MIN_RUN = 3;

export type DisplayRow =
  | { kind: "item"; key: string; item: ConversationItem }
  | { kind: "unknown-run"; key: string; items: ConversationItem[]; summary: string; ordinal: number }
  | {
      kind: "live-thinking";
      key: string;
      ordinal: number;
      turnId: string | null;
      agentId: string | null;
      item: ConversationItem;
    };

type LiveThinkingRow = Extract<DisplayRow, { kind: "live-thinking" }>;

export function unknownVendorSummary(item: ConversationItem): string {
  for (const block of item.blocks) {
    if (block.type === "unknown-vendor") return `${block.vendorType}: ${block.safeSummary}`;
  }
  return "unrecognized vendor event";
}

const LIVE_THINKING_PHASES = new Set(["pending", "streaming", "waiting"]);

function blockText(block: ConversationItem["blocks"][number]): string | null {
  if (block.type === "thinking" || block.type === "markdown") return block.markdown;
  if (block.type === "text") return block.text;
  return null;
}

export function isLiveEmptyThinking(item: ConversationItem): boolean {
  if (item.kind !== "thinking" || !LIVE_THINKING_PHASES.has(item.phase)) return false;
  return item.blocks.every((block) => {
    const text = blockText(block);
    return text === null || text.trim() === "";
  });
}

function liveThinkingKey(item: ConversationItem): string {
  const turn = item.turnId ?? "no-turn";
  const agent = item.agent?.agentId ?? "root";
  return `${turn}|${agent}`;
}

function liveKeyFor(item: ConversationItem): string | null {
  if (item.kind === "thinking" || item.kind === "turn-result") return liveThinkingKey(item);
  return null;
}

function handleLiveOpen(
  item: ConversationItem,
  liveKey: string,
  openLiveThinking: Set<string>,
  openLiveRow: Map<string, LiveThinkingRow>,
  rows: DisplayRow[],
): boolean {
  if (!(item.kind === "thinking" && isLiveEmptyThinking(item))) return false;
  if (!openLiveThinking.has(liveKey)) {
    openLiveThinking.add(liveKey);
    const liveRow: LiveThinkingRow = {
      kind: "live-thinking",
      key: `live-thinking:${liveKey}`,
      ordinal: item.globalOrdinal,
      turnId: item.turnId ?? null,
      agentId: item.agent?.agentId ?? null,
      item,
    };
    rows.push(liveRow);
    openLiveRow.set(liveKey, liveRow);
  }
  return true;
}

function handleLiveUpdate(
  item: ConversationItem,
  liveKey: string,
  openLiveThinking: Set<string>,
  openLiveRow: Map<string, LiveThinkingRow>,
): boolean {
  if (
    !(
      item.kind === "thinking" &&
      LIVE_THINKING_PHASES.has(item.phase) &&
      !isLiveEmptyThinking(item) &&
      openLiveThinking.has(liveKey)
    )
  ) {
    return false;
  }
  const liveRow = openLiveRow.get(liveKey);
  if (liveRow !== undefined) {
    liveRow.item = item;
    liveRow.ordinal = item.globalOrdinal;
  }
  return true;
}

function handleLiveFinalize(
  item: ConversationItem,
  liveKey: string,
  openLiveThinking: Set<string>,
  openLiveRow: Map<string, LiveThinkingRow>,
  rows: DisplayRow[],
): void {
  if (
    item.kind !== "turn-result" &&
    !(item.kind === "thinking" && !LIVE_THINKING_PHASES.has(item.phase))
  ) {
    return;
  }
  openLiveThinking.delete(liveKey);
  const liveRow = openLiveRow.get(liveKey);
  if (liveRow !== undefined) {
    const rowIndex = rows.indexOf(liveRow);
    if (rowIndex !== -1) {
      rows.splice(rowIndex, 1);
    }
    openLiveRow.delete(liveKey);
  }
}

function unknownRunFor(
  source: readonly ConversationItem[],
  index: number,
): { end: number; items: ConversationItem[]; summary: string } | null {
  const first = source[index];
  if (first.kind !== "unknown-vendor") return null;
  const summary = unknownVendorSummary(first);
  let end = index + 1;
  while (
    end < source.length &&
    source[end].kind === "unknown-vendor" &&
    unknownVendorSummary(source[end]) === summary
  ) {
    end += 1;
  }
  const run = source.slice(index, end);
  if (run.length < MIN_RUN) return null;
  return { end, items: run, summary };
}

export function groupDisplayRows(items: readonly ConversationItem[]): DisplayRow[] {
  const rows: DisplayRow[] = [];
  const openLiveThinking = new Set<string>();
  // Stable row-object references, not array indices: finalizing an earlier live row splices
  // the array and shifts later rows, so an index would silently point at the wrong row.
  const openLiveRow = new Map<string, LiveThinkingRow>();
  for (let index = 0; index < items.length; index += 1) {
    const item = items[index];
    const liveKey = liveKeyFor(item);
    if (liveKey !== null) {
      if (handleLiveOpen(item, liveKey, openLiveThinking, openLiveRow, rows)) continue;
      if (handleLiveUpdate(item, liveKey, openLiveThinking, openLiveRow)) continue;
      handleLiveFinalize(item, liveKey, openLiveThinking, openLiveRow, rows);
    }
    const collapsed = unknownRunFor(items, index);
    if (collapsed !== null) {
      rows.push({
        kind: "unknown-run",
        key: `unknown-run-${collapsed.items[0].itemId}`,
        items: collapsed.items,
        summary: collapsed.summary,
        ordinal: collapsed.items[0].globalOrdinal,
      });
      index = collapsed.end - 1;
      continue;
    }
    rows.push({ kind: "item", key: item.itemId, item });
  }
  return rows;
}

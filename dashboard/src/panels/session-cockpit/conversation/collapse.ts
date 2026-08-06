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

export function groupDisplayRows(items: readonly ConversationItem[]): DisplayRow[] {
  const rows: DisplayRow[] = [];
  const openLiveThinking = new Set<string>();
  const openLiveRowIndex = new Map<string, number>();
  let index = 0;
  while (index < items.length) {
    const item = items[index];
    const liveKey =
      item.kind === "thinking" || item.kind === "turn-result" ? liveThinkingKey(item) : null;
    if (liveKey !== null && item.kind === "thinking" && isLiveEmptyThinking(item)) {
      if (!openLiveThinking.has(liveKey)) {
        openLiveThinking.add(liveKey);
        openLiveRowIndex.set(liveKey, rows.length);
        rows.push({
          kind: "live-thinking",
          key: `live-thinking:${liveKey}`,
          ordinal: item.globalOrdinal,
          turnId: item.turnId ?? null,
          agentId: item.agent?.agentId ?? null,
          item,
        });
      }
      index += 1;
      continue;
    }
    if (liveKey !== null) {
      // A live-phase reasoning item carrying real content is still the active turn's thinking:
      // it updates the already-open live row in place (one stable animated row, R15) rather than
      // rendering as a normal row next to the still-open indicator (two rows, one turn -- F3).
      if (
        item.kind === "thinking" &&
        LIVE_THINKING_PHASES.has(item.phase) &&
        !isLiveEmptyThinking(item) &&
        openLiveThinking.has(liveKey)
      ) {
        const rowIndex = openLiveRowIndex.get(liveKey);
        const liveRow = rowIndex !== undefined ? rows[rowIndex] : undefined;
        if (liveRow !== undefined && liveRow.kind === "live-thinking") {
          liveRow.item = item;
          liveRow.ordinal = item.globalOrdinal;
        }
        index += 1;
        continue;
      }
      // A completed/failed/interrupted reasoning item, or the turn's result, finalizes the
      // live indicator exactly once -- the ephemeral indicator row is REMOVED, while the
      // substantive reasoning item itself still renders as ordinary transcript content.
      if (item.kind === "turn-result" || (item.kind === "thinking" && !LIVE_THINKING_PHASES.has(item.phase))) {
        openLiveThinking.delete(liveKey);
        const rowIndex = openLiveRowIndex.get(liveKey);
        if (rowIndex !== undefined) {
          rows.splice(rowIndex, 1);
          openLiveRowIndex.delete(liveKey);
        }
      }
    }
    if (item.kind === "unknown-vendor") {
      const summary = unknownVendorSummary(item);
      let end = index + 1;
      while (
        end < items.length &&
        items[end].kind === "unknown-vendor" &&
        unknownVendorSummary(items[end]) === summary
      ) {
        end += 1;
      }
      const run = items.slice(index, end);
      if (run.length >= MIN_RUN) {
        rows.push({
          kind: "unknown-run",
          key: `unknown-run-${run[0].itemId}`,
          items: run,
          summary,
          ordinal: run[0].globalOrdinal,
        });
        index = end;
        continue;
      }
    }
    rows.push({ kind: "item", key: item.itemId, item });
    index += 1;
  }
  return rows;
}

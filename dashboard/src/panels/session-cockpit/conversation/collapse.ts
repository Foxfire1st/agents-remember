// Visual grouping of consecutive identical-summary unknown-vendor evidence (design §12.2; round-1
// F10). A brand-new codex session can emit a wall of identical `unknown vendor event` rows; §12.2
// permits summarizing consecutive items visually AS LONG AS each underlying item stays addressable
// and identity is never mutated. This produces a flat display list the feed virtualizes: a run of >=3
// identical unknown-vendor items collapses to one de-emphasized row (expandable to its members); every
// other item passes through unchanged, keeping its own article + server ordinal.

import type { ConversationItem } from "../../../data/conversation/types";

const MIN_RUN = 3;

export type DisplayRow =
  | { kind: "item"; key: string; item: ConversationItem }
  | { kind: "unknown-run"; key: string; items: ConversationItem[]; summary: string; ordinal: number };

export function unknownVendorSummary(item: ConversationItem): string {
  for (const block of item.blocks) {
    if (block.type === "unknown-vendor") return `${block.vendorType}: ${block.safeSummary}`;
  }
  return "unrecognized vendor event";
}

export function groupUnknownVendorRuns(items: readonly ConversationItem[]): DisplayRow[] {
  const rows: DisplayRow[] = [];
  let index = 0;
  while (index < items.length) {
    const item = items[index];
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

import { describe, expect, it } from "vitest";

import type { ConversationItem } from "../../../data/conversation/types";
import { groupUnknownVendorRuns } from "./collapse";

function item(id: string, ordinal: number, kind: ConversationItem["kind"], summary?: string): ConversationItem {
  return {
    itemId: id,
    revision: 1,
    globalOrdinal: ordinal,
    lane: "system",
    source: "harness-live",
    provenance: { strength: "unknown", origin: "codex" },
    role: "system",
    kind,
    phase: "completed",
    blocks:
      kind === "unknown-vendor"
        ? [{ blockId: `${id}-b`, type: "unknown-vendor", vendorType: "codex:notification", safeSummary: summary ?? "x", evidenceRef: `ev-${id}` }]
        : [{ blockId: `${id}-b`, type: "markdown", markdown: "hi" }],
  };
}

describe("groupUnknownVendorRuns (F10)", () => {
  it("collapses a run of >=3 identical-summary unknown-vendor items into one expandable row", () => {
    const items = [
      item("a", 1, "message"),
      item("u1", 2, "unknown-vendor", "same"),
      item("u2", 3, "unknown-vendor", "same"),
      item("u3", 4, "unknown-vendor", "same"),
      item("b", 5, "message"),
    ];
    const rows = groupUnknownVendorRuns(items);
    expect(rows.map((r) => r.kind)).toEqual(["item", "unknown-run", "item"]);
    const run = rows[1];
    expect(run.kind === "unknown-run" && run.items).toHaveLength(3);
    // The run's ordinal is its first member's server ordinal (posinset honesty).
    expect(run.kind === "unknown-run" && run.ordinal).toBe(2);
  });

  it("does NOT collapse a short run (<3) — each keeps its own article", () => {
    const items = [item("u1", 1, "unknown-vendor", "s"), item("u2", 2, "unknown-vendor", "s")];
    expect(groupUnknownVendorRuns(items).map((r) => r.kind)).toEqual(["item", "item"]);
  });

  it("does not merge runs of different summaries", () => {
    const items = [
      item("u1", 1, "unknown-vendor", "a"),
      item("u2", 2, "unknown-vendor", "a"),
      item("u3", 3, "unknown-vendor", "a"),
      item("u4", 4, "unknown-vendor", "b"),
      item("u5", 5, "unknown-vendor", "b"),
      item("u6", 6, "unknown-vendor", "b"),
    ];
    const rows = groupUnknownVendorRuns(items);
    expect(rows).toHaveLength(2);
    expect(rows.every((r) => r.kind === "unknown-run")).toBe(true);
  });
});

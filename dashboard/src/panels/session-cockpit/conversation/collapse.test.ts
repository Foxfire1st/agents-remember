import { describe, expect, it } from "vitest";

import type { ConversationItem } from "../../../data/conversation/types";
import { groupDisplayRows } from "./collapse";

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
    const rows = groupDisplayRows(items);
    expect(rows.map((r) => r.kind)).toEqual(["item", "unknown-run", "item"]);
    const run = rows[1];
    expect(run.kind === "unknown-run" && run.items).toHaveLength(3);
    // The run's ordinal is its first member's server ordinal (posinset honesty).
    expect(run.kind === "unknown-run" && run.ordinal).toBe(2);
  });

  it("does NOT collapse a short run (<3) — each keeps its own article", () => {
    const items = [item("u1", 1, "unknown-vendor", "s"), item("u2", 2, "unknown-vendor", "s")];
    expect(groupDisplayRows(items).map((r) => r.kind)).toEqual(["item", "item"]);
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
    const rows = groupDisplayRows(items);
    expect(rows).toHaveLength(2);
    expect(rows.every((r) => r.kind === "unknown-run")).toBe(true);
  });
});

function liveThinking(id: string, ordinal: number, turnId: string | undefined, phase: ConversationItem["phase"]): ConversationItem {
  return {
    itemId: id,
    revision: 1,
    globalOrdinal: ordinal,
    turnId,
    lane: "harness",
    source: "harness-live",
    provenance: { strength: "correlated", origin: "codex" },
    role: "assistant",
    kind: "thinking",
    phase,
    blocks: [],
  };
}

describe("groupDisplayRows — live thinking coalescing (260731-EFA-L7 R15)", () => {
  it("renders ONE live-thinking row for repeated empty in-progress thinking of the same turn", () => {
    const items: ConversationItem[] = [
      liveThinking("r1", 1, "turn-1", "streaming"),
      liveThinking("r2", 2, "turn-1", "streaming"),
      liveThinking("r3", 3, "turn-1", "streaming"),
    ];
    const rows = groupDisplayRows(items);
    expect(rows.map((r) => r.kind)).toEqual(["live-thinking"]);
    expect(rows[0].kind === "live-thinking" && rows[0].key).toBe("live-thinking:turn-1|root");
  });

  it("removes the indicator on a completed reasoning item and reopens only if streaming resumes", () => {
    const items: ConversationItem[] = [
      liveThinking("r1", 1, "turn-1", "streaming"),
      liveThinking("r2", 2, "turn-1", "streaming"),
      {
        ...liveThinking("r3", 3, "turn-1", "completed"),
        blocks: [{ blockId: "r3-b", type: "thinking", markdown: "the actual reasoning" }],
      },
      liveThinking("r4", 4, "turn-1", "streaming"),
    ];
    const rows = groupDisplayRows(items);
    // The ephemeral indicator for the first live stretch is REMOVED on completion; the
    // substantive reasoning renders normally; resumed streaming opens one fresh indicator.
    expect(rows.map((r) => r.kind)).toEqual(["item", "live-thinking"]);
    expect(rows[0].kind === "item" && rows[0].item.itemId).toBe("r3");
  });

  it.each(["pending", "streaming"] as const)(
    "keeps ONE live row when a %s thinking item carries real content (F3 pin)",
    (phase) => {
      const items: ConversationItem[] = [
        liveThinking("r1", 1, "turn-1", "streaming"),
        {
          ...liveThinking("r2", 2, "turn-1", phase),
          blocks: [{ blockId: "r2-b", type: "thinking", markdown: "real streamed reasoning" }],
        },
        liveThinking("r3", 3, "turn-1", "streaming"),
      ];
      const rows = groupDisplayRows(items);
      // The content-bearing in-progress item updates the open live row; it must NOT render as a
      // normal row alongside the still-open indicator (two rows, one turn).
      expect(rows.map((r) => r.kind)).toEqual(["live-thinking"]);
      expect(rows[0].kind === "live-thinking" && rows[0].item.itemId).toBe("r2");
      expect(rows[0].kind === "live-thinking" && rows[0].ordinal).toBe(2);
    },
  );

  it("keeps separate live indicators per turn and per agent thread", () => {
    const items: ConversationItem[] = [
      liveThinking("r1", 1, "turn-1", "streaming"),
      {
        ...liveThinking("r2", 2, "turn-1", "streaming"),
        agent: { agentId: "agent-1", status: "running" },
      },
      liveThinking("r3", 3, "turn-2", "streaming"),
    ];
    const rows = groupDisplayRows(items);
    expect(rows.map((r) => r.kind)).toEqual(["live-thinking", "live-thinking", "live-thinking"]);
  });

  it("a turn-result removes the live indicator exactly once", () => {
    const items: ConversationItem[] = [
      liveThinking("r1", 1, "turn-1", "streaming"),
      liveThinking("r2", 2, "turn-1", "streaming"),
      {
        itemId: "tr",
        revision: 1,
        globalOrdinal: 3,
        turnId: "turn-1",
        lane: "harness",
        source: "harness-live",
        provenance: { strength: "correlated", origin: "codex" },
        role: "system",
        kind: "turn-result",
        phase: "completed",
        blocks: [],
      },
    ];
    const rows = groupDisplayRows(items);
    expect(rows.map((r) => r.kind)).toEqual(["item"]);
  });
});

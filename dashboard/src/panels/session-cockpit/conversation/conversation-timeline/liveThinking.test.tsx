import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { ConversationItem } from "../../../../data/conversation/types";
import { ConversationTimeline } from "./ConversationTimeline";
import { msg } from "./test-utils";

describe("ConversationTimeline — live thinking coalescing acceptance (260731-EFA-L7 R15/R17)", () => {
  it("renders one live indicator for repeated empty reasoning and preserves unrelated unknown evidence", () => {
    const emptyThinking = (id: string, ordinal: number): ConversationItem =>
      msg({
        itemId: id,
        globalOrdinal: ordinal,
        kind: "thinking",
        role: "assistant",
        phase: "streaming",
        turnId: "turn-1",
        blocks: [],
      });
    const items: ConversationItem[] = [
      emptyThinking("r1", 1),
      emptyThinking("r2", 2),
      emptyThinking("r3", 3),
      msg({
        itemId: "u1",
        globalOrdinal: 4,
        kind: "unknown-vendor",
        role: "system",
        phase: "completed",
        blocks: [
          {
            blockId: "u1-b",
            type: "unknown-vendor",
            vendorType: "codex:event",
            safeSummary: "genuinely unknown vendor notification",
            evidenceRef: "ar-ev:test:1",
          },
        ],
      }),
      msg({
        itemId: "r4",
        globalOrdinal: 5,
        kind: "thinking",
        role: "assistant",
        phase: "completed",
        turnId: "turn-1",
        blocks: [{ blockId: "r4-b", type: "thinking", markdown: "substantive reasoning" }],
      }),
      emptyThinking("r5", 6),
    ];

    render(
      <ConversationTimeline
        items={items}
        totalItems={items.length}
        hasOlder={false}
        busy={false}
        onLoadOlder={() => {}}
      />,
    );

    // One stable live indicator for the active turn, not one per reasoning item id.
    expect(screen.getAllByTestId("live-thinking-indicator")).toHaveLength(1);
    // The substantive completed reasoning still renders as ordinary transcript content.
    expect(screen.getByText(/substantive reasoning/)).not.toBeNull();
    // The unrelated truly unknown notification stays addressable as evidence.
    expect(screen.getByText(/genuinely unknown vendor notification/)).not.toBeNull();
  });

  it("keeps ONE live row when an in-progress thinking item carries real content (F3 pin)", () => {
    const thinking = (
      id: string,
      ordinal: number,
      phase: ConversationItem["phase"],
      blocks: ConversationItem["blocks"],
    ): ConversationItem =>
      msg({
        itemId: id,
        globalOrdinal: ordinal,
        kind: "thinking",
        role: "assistant",
        phase,
        turnId: "turn-1",
        blocks,
      });
    const items: ConversationItem[] = [
      thinking("r1", 1, "streaming", []),
      thinking("r2", 2, "streaming", [
        { blockId: "r2-b", type: "thinking", markdown: "real streamed reasoning" },
      ]),
      thinking("r3", 3, "pending", []),
    ];

    render(
      <ConversationTimeline
        items={items}
        totalItems={items.length}
        hasOlder={false}
        busy={false}
        onLoadOlder={() => {}}
      />,
    );

    // The content-bearing in-progress item updates the one stable animated row instead of adding
    // a second ordinary row alongside the still-open indicator (two rows, one turn -- F3).
    const feed = screen.getByRole("feed");
    expect(within(feed).getAllByRole("article")).toHaveLength(1);
    expect(screen.getAllByTestId("live-thinking-indicator")).toHaveLength(1);
    expect(screen.queryAllByTestId("conversation-thinking")).toHaveLength(0);
    // The real streamed content is visible inside the live row, not dropped.
    expect(screen.getByText(/real streamed reasoning/)).not.toBeNull();
  });
});

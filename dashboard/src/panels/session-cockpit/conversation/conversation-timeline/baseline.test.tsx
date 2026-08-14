import { act, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import axe from "axe-core";

import type { ConversationItem } from "../../../../data/conversation/types";
import { ConversationTimeline } from "./ConversationTimeline";
import { msg } from "./test-utils";

describe("ConversationTimeline — 10k tool-heavy DOM/interaction baseline (R5.2/R5.10, L4.4)", () => {
  const TOOL_HEAVY_KINDS = ["message", "thinking", "tool-call", "tool-result", "message"] as const;

  function bigHistory(count: number): ConversationItem[] {
    const items: ConversationItem[] = [];
    for (let index = 0; index < count; index += 1) {
      const ordinal = index + 1;
      const kind = TOOL_HEAVY_KINDS[index % TOOL_HEAVY_KINDS.length];
      items.push(
        msg({
          itemId: `item-${ordinal}`,
          globalOrdinal: ordinal, // 1-based server ordinal, stable across paging
          kind,
          role: kind === "message" ? "assistant" : kind === "tool-result" ? "tool" : "assistant",
          blocks:
            kind === "tool-call"
              ? [{ blockId: `item-${ordinal}-t`, type: "tool-input", summary: `tool call ${ordinal}` }]
              : kind === "tool-result"
                ? [{ blockId: `item-${ordinal}-o`, type: "tool-output", text: `output ${ordinal}` }]
                : kind === "thinking"
                  ? [{ blockId: `item-${ordinal}-k`, type: "thinking", markdown: `reasoning ${ordinal}` }]
                  : [{ blockId: `item-${ordinal}-b`, type: "markdown", markdown: `message ${ordinal}` }],
        }),
      );
    }
    return items;
  }

  it("waits out hidden geometry, then premeasures a moderate transcript in bounded slices", async () => {
    vi.useFakeTimers();
    try {
      const measuredIndexes = new Set<number>();
      Object.defineProperty(HTMLElement.prototype, "offsetHeight", {
        configurable: true,
        get() {
          const index = this.getAttribute?.("data-index");
          if (index === null || index === undefined) return 600;
          const parsed = Number(index);
          measuredIndexes.add(parsed);
          return parsed % 3 === 0 ? 320 : parsed % 2 === 0 ? 44 : 180;
        },
      });
      const items = bigHistory(120);
      const { container, rerender, unmount } = render(
        <ConversationTimeline
          items={items}
          totalItems={items.length}
          hasOlder={false}
          busy={false}
          onLoadOlder={() => {}}
          visible={false}
          measurementCacheId="moderate"
        />,
      );
      Object.defineProperty(screen.getByTestId("conversation-viewport"), "clientWidth", {
        configurable: true,
        value: 800,
      });
      let peakMounted = container.querySelectorAll("[data-conversation-item]").length;
      // A keep-alive hidden under display:none must not consume the warm-up against box-less
      // geometry. Waiting longer than the whole visible pass leaves most rows untouched.
      await act(async () => {
        await vi.advanceTimersByTimeAsync(500);
      });
      expect(measuredIndexes.size).toBeLessThan(items.length);
      rerender(
        <ConversationTimeline
          items={items}
          totalItems={items.length}
          hasOlder={false}
          busy={false}
          onLoadOlder={() => {}}
          visible
          measurementCacheId="moderate"
        />,
      );
      // The first visible render adds only one measurement batch to the ordinary bounded window;
      // subsequent timer turns replace it with the next older batch.
      for (let slice = 0; slice < 12; slice += 1) {
        await act(async () => {
          await vi.advanceTimersByTimeAsync(25);
        });
        peakMounted = Math.max(
          peakMounted,
          container.querySelectorAll("[data-conversation-item]").length,
        );
      }

      // Every initial row got a real measurement before the operator could discover it by
      // scrolling upward. No intermediate slice retained the accumulated transcript, and the
      // completed pass returned to the ordinary bounded virtual range.
      expect(measuredIndexes.size).toBe(items.length);
      expect(peakMounted).toBeLessThan(50);
      expect(container.querySelectorAll("[data-conversation-item]").length).toBeLessThan(80);

      const stored = JSON.parse(
        window.sessionStorage.getItem("cockpit.chats.measurements.v1:moderate") ?? "null",
      ) as { items?: unknown[] } | null;
      expect(stored?.items).toHaveLength(items.length);

      // A fresh mount models a browser refresh: the stored exact heights seed TanStack, so opening
      // the same transcript does not replay the measurement pass.
      unmount();
      measuredIndexes.clear();
      const refreshed = render(
        <ConversationTimeline
          items={items}
          totalItems={items.length}
          hasOlder={false}
          busy={false}
          onLoadOlder={() => {}}
          visible
          measurementCacheId="moderate"
        />,
      );
      await act(async () => {
        await vi.advanceTimersByTimeAsync(500);
      });
      expect(measuredIndexes.size).toBeLessThan(50);
      refreshed.unmount();

      // The cache is deliberately width-qualified: a resized chat invalidates wrapped heights and
      // returns to the bounded pass instead of replaying stale geometry.
      measuredIndexes.clear();
      const resized = render(
        <ConversationTimeline
          items={items}
          totalItems={items.length}
          hasOlder={false}
          busy={false}
          onLoadOlder={() => {}}
          visible={false}
          measurementCacheId="moderate"
        />,
      );
      Object.defineProperty(screen.getByTestId("conversation-viewport"), "clientWidth", {
        configurable: true,
        value: 700,
      });
      resized.rerender(
        <ConversationTimeline
          items={items}
          totalItems={items.length}
          hasOlder={false}
          busy={false}
          onLoadOlder={() => {}}
          visible
          measurementCacheId="moderate"
        />,
      );
      for (let slice = 0; slice < 12; slice += 1) {
        await act(async () => {
          await vi.advanceTimersByTimeAsync(25);
        });
      }
      expect(measuredIndexes.size).toBe(items.length);
      resized.unmount();
    } finally {
      window.sessionStorage.removeItem("cockpit.chats.measurements.v1:moderate");
      Object.defineProperty(HTMLElement.prototype, "offsetHeight", { configurable: true, value: 600 });
      vi.useRealTimers();
    }
  });

  it("keeps the mounted DOM bounded and the ordinals honest at 10,000 items", () => {
    const items = bigHistory(10_000);
    const startedAt = performance.now();
    const { container } = render(
      <ConversationTimeline
        items={items}
        totalItems={items.length}
        hasOlder
        busy={false}
        onLoadOlder={() => {}}
      />,
    );
    const mountMs = performance.now() - startedAt;

    const mountedArticles = container.querySelectorAll("[data-conversation-item]");
    // The DOM baseline: the virtualized window is a small constant, never the 10k history depth.
    // Recorded baseline: 10 mounted articles / ~42-55 ms initial mount at 10,000 tool-heavy items.
    expect(mountedArticles.length).toBeGreaterThan(0);
    expect(mountedArticles.length).toBeLessThan(80);
    expect(mountedArticles.length).toBeLessThan(items.length / 100);
    // Interaction baseline tripwire: an initial mount of 10k items must not stall the main thread.
    // Generous ceiling for shared-runner jitter; the observed value sits well under it.
    expect(mountMs).toBeLessThan(3000);

    // aria-posinset rides the server globalOrdinal (never the array index); aria-setsize is the honest total.
    const feed = screen.getByRole("feed");
    const first = within(feed).getAllByRole("article")[0];
    expect(Number(first.getAttribute("aria-posinset"))).toBeGreaterThanOrEqual(1);
    expect(first.getAttribute("aria-setsize")).toBe("10000");
    expect(first.getAttribute("aria-live")).toBe("off");
  });

  it("passes axe on the 10k feed (feed/article/posinset semantics stay clean at depth)", async () => {
    const { container } = render(
      <ConversationTimeline
        items={bigHistory(10_000)}
        totalItems={10_000}
        hasOlder
        busy={false}
        onLoadOlder={() => {}}
      />,
    );
    const results = await axe.run(container, {
      rules: { "color-contrast": { enabled: false }, region: { enabled: false } },
    });
    expect(results.violations).toEqual([]);
  });
});

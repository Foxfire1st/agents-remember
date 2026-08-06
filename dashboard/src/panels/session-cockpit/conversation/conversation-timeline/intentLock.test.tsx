import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { ConversationItem } from "../../../../data/conversation/types";
import { ConversationTimeline } from "./ConversationTimeline";
import { msg } from "./test-utils";

describe("ConversationTimeline — intent lock, follow-on-growth, latest chip (B3)", () => {
  // Same jsdom geometry shim as the scroll-memory describe: the virtualizer needs a fixed box to render
  // rows, and the scroll listener needs honest scrollHeight/clientHeight numbers.
  const alignedTops: number[] = [];
  beforeEach(() => {
    alignedTops.length = 0;
    Object.defineProperty(HTMLElement.prototype, "scrollTo", {
      configurable: true,
      value: function (this: HTMLElement, options?: { top?: number }) {
        const top = options?.top ?? 0;
        alignedTops.push(top);
        this.scrollTop = top;
      },
    });
    Object.defineProperty(HTMLElement.prototype, "scrollHeight", { configurable: true, value: 6000 });
    Object.defineProperty(HTMLElement.prototype, "clientHeight", { configurable: true, value: 600 });
  });
  afterEach(() => {
    const proto = HTMLElement.prototype as unknown as Record<string, unknown>;
    delete proto.scrollTo;
    delete proto.scrollHeight;
    delete proto.clientHeight;
  });

  function feedOf(count: number): ConversationItem[] {
    return Array.from({ length: count }, (_, index) =>
      msg({ itemId: `m-${index + 1}`, globalOrdinal: index + 1 }),
    );
  }

  function pinGeometry(viewport: HTMLElement, scrollHeight: number, clientHeight: number): void {
    Object.defineProperty(viewport, "scrollHeight", { configurable: true, value: scrollHeight });
    Object.defineProperty(viewport, "clientHeight", { configurable: true, value: clientHeight });
  }

  /** Mount 10 rows and let the mount restore drive to the end and consume (locked at bottom). */
  async function mountSettledAtBottom() {
    const utils = render(
      <ConversationTimeline items={feedOf(10)} hasOlder={false} busy={false} onLoadOlder={() => {}} />,
    );
    const viewport = screen.getByTestId("conversation-viewport");
    pinGeometry(viewport, 6000, 600);
    await act(async () => { await vi.advanceTimersByTimeAsync(250); });
    expect(viewport.scrollTop).toBe(5400);
    return { viewport, rerender: utils.rerender };
  }

  it("deliberate scroll-up during streaming holds the EXACT position (deltas + new rows); content-driven events never re-engage; bottom arrival re-engages and clears the pill", async () => {
    vi.useFakeTimers();
    try {
      const onScrollMemory = vi.fn();
      const { rerender } = render(
        <ConversationTimeline items={feedOf(10)} hasOlder={false} busy={false} onLoadOlder={() => {}} onScrollMemory={onScrollMemory} />,
      );
      const viewport = screen.getByTestId("conversation-viewport");
      pinGeometry(viewport, 6000, 600);
      await act(async () => { await vi.advanceTimersByTimeAsync(250); });
      expect(viewport.scrollTop).toBe(5400); // mount drive landed at the end

      // The operator deliberately scrolls up mid-stream (wheel = genuine input).
      fireEvent.wheel(viewport);
      viewport.scrollTop = 3000;
      fireEvent.scroll(viewport);
      expect(onScrollMemory).toHaveBeenLastCalledWith({ scrollTop: 3000, atBottom: false });

      // A stream delta grows the last row IN PLACE (same keys, longer content): exact hold.
      pinGeometry(viewport, 6300, 600);
      const streamed = feedOf(10);
      streamed[9] = msg({
        itemId: "m-10",
        globalOrdinal: 10,
        blocks: [{ blockId: "m-10-b", type: "markdown", markdown: "hi\nstreamed more\nand more" }],
      });
      rerender(
        <ConversationTimeline items={streamed} hasOlder={false} busy={false} onLoadOlder={() => {}} onScrollMemory={onScrollMemory} />,
      );
      expect(viewport.scrollTop).toBe(3000);

      // A NEW ROW arrives: still an exact hold; the pill counts it.
      pinGeometry(viewport, 6600, 600);
      rerender(
        <ConversationTimeline items={feedOf(11)} hasOlder={false} busy={false} onLoadOlder={() => {}} onScrollMemory={onScrollMemory} />,
      );
      expect(viewport.scrollTop).toBe(3000);
      expect(screen.getByTestId("conversation-new-updates").textContent).toContain("1 new");

      // A CONTENT-DRIVEN scroll event landing AT the bottom (the interaction window has decayed,
      // no genuine input): never re-engages — the deliberate scroll-up STAYS disengaged.
      await act(async () => { await vi.advanceTimersByTimeAsync(600); });
      viewport.scrollTop = 6000; // 6600 - 600, geometrically the end
      fireEvent.scroll(viewport);
      expect(onScrollMemory).toHaveBeenLastCalledWith({ scrollTop: 6000, atBottom: false });

      // Scrolling all the way down WITH genuine input re-engages fully: pill clears, follow owns.
      fireEvent.wheel(viewport);
      fireEvent.scroll(viewport);
      expect(onScrollMemory).toHaveBeenLastCalledWith({ scrollTop: 6000, atBottom: true });
      expect(screen.queryByTestId("conversation-new-updates")).toBeNull();

      // …and growth is followed again, to the new end.
      pinGeometry(viewport, 7200, 600);
      rerender(
        <ConversationTimeline items={feedOf(12)} hasOlder={false} busy={false} onLoadOlder={() => {}} onScrollMemory={onScrollMemory} />,
      );
      expect(viewport.scrollTop).toBe(7200 - 600);
    } finally {
      vi.useRealTimers();
    }
  });

  it("follow-on-growth: locked mid-stream, a total-height change with NO key change re-pins the window to the end", async () => {
    vi.useFakeTimers();
    try {
      const { viewport, rerender } = await mountSettledAtBottom();

      // A stream delta arrives: the last row grows in place — total height changes, the row key
      // does NOT. The direct pixel write re-pins (no scrollToIndex involved).
      pinGeometry(viewport, 6450, 600);
      const streamed = feedOf(10);
      streamed[9] = msg({
        itemId: "m-10",
        globalOrdinal: 10,
        blocks: [{ blockId: "m-10-b", type: "markdown", markdown: "hi\nstreamed more\nand more\nand more" }],
      });
      rerender(
        <ConversationTimeline items={streamed} hasOlder={false} busy={false} onLoadOlder={() => {}} />,
      );
      expect(viewport.scrollTop).toBe(6450 - 600);
    } finally {
      vi.useRealTimers();
    }
  });

  it("pointerdown stands an armed restore drive down (scrollbar drag) — the restore never applies", async () => {
    vi.useFakeTimers();
    try {
      const items = feedOf(10);
      const { rerender } = render(
        <ConversationTimeline items={items} hasOlder={false} busy={false} onLoadOlder={() => {}} visible={false} restoreScroll={{ scrollTop: 611, atBottom: false }} />,
      );
      const viewport = screen.getByTestId("conversation-viewport");
      pinGeometry(viewport, 0, 0);
      rerender(
        <ConversationTimeline items={items} hasOlder={false} busy={false} onLoadOlder={() => {}} visible restoreScroll={{ scrollTop: 611, atBottom: false }} />,
      );
      // The drag starts inside the re-measure window: pointerdown is trusted input (a drag never
      // wheels/touches/keys), so the armed restore stands down for good…
      fireEvent.pointerDown(viewport);
      pinGeometry(viewport, 1962, 600);
      await act(async () => { await vi.advanceTimersByTimeAsync(250); });
      expect(viewport.scrollTop).toBe(0); // …never applied — the operator's position owns.
    } finally {
      vi.useRealTimers();
    }
  });

  it("shows one latest chip only after the feed moves away from the live edge", () => {
    render(<ConversationTimeline items={feedOf(10)} hasOlder={false} busy={false} onLoadOlder={() => {}} />);
    const viewport = screen.getByTestId("conversation-viewport");
    expect(screen.queryByTestId("conversation-scroll-latest")).toBeNull();

    fireEvent.wheel(viewport);
    viewport.scrollTop = 3000;
    fireEvent.scroll(viewport);
    expect(screen.getByTestId("conversation-scroll-latest").getAttribute("aria-label")).toBe("Jump to latest");
    expect(screen.queryByTestId("conversation-scroll-top")).toBeNull();
  });

  it("keeps the latest chip hidden when the content fits the viewport", () => {
    // Content fits: scrollHeight == clientHeight, so there is no route away from the live edge.
    Object.defineProperty(HTMLElement.prototype, "scrollHeight", { configurable: true, value: 600 });
    render(<ConversationTimeline items={feedOf(2)} hasOlder={false} busy={false} onLoadOlder={() => {}} />);
    expect(screen.queryByTestId("conversation-scroll-latest")).toBeNull();
  });

  it("the latest chip re-engages the lock, clears its update count, and pins the end", async () => {
    vi.useFakeTimers();
    try {
      const { rerender } = render(
        <ConversationTimeline items={feedOf(10)} hasOlder={false} busy={false} onLoadOlder={() => {}} />,
      );
      const viewport = screen.getByTestId("conversation-viewport");
      pinGeometry(viewport, 6000, 600);
      await act(async () => { await vi.advanceTimersByTimeAsync(250); });

      // The operator scrolls up; an arrival pills.
      fireEvent.wheel(viewport);
      viewport.scrollTop = 3000;
      fireEvent.scroll(viewport);
      pinGeometry(viewport, 6600, 600);
      rerender(
        <ConversationTimeline items={feedOf(11)} hasOlder={false} busy={false} onLoadOlder={() => {}} />,
      );
      expect(screen.getByTestId("conversation-new-updates").textContent).toContain("1 new");
      expect(screen.getByTestId("conversation-scroll-latest").getAttribute("aria-label")).toBe(
        "Jump to latest, 1 new update",
      );

      // The explicit action: clear the override + pill, pin the honest end, re-engage.
      fireEvent.click(screen.getByTestId("conversation-scroll-latest"));
      expect(screen.queryByTestId("conversation-new-updates")).toBeNull();
      expect(screen.queryByTestId("conversation-scroll-latest")).toBeNull();
      expect(viewport.scrollTop).toBe(6600 - 600);

      // The lock is re-engaged: the next arrival is followed, not pilled.
      pinGeometry(viewport, 7200, 600);
      rerender(
        <ConversationTimeline items={feedOf(12)} hasOlder={false} busy={false} onLoadOlder={() => {}} />,
      );
      expect(screen.queryByTestId("conversation-new-updates")).toBeNull();
      expect(viewport.scrollTop).toBe(7200 - 600);
    } finally {
      vi.useRealTimers();
    }
  });

  it("a wheel-up INSIDE the band holds through the next growth mid-gesture (the scroll-up trap); continuing past the band disengages and the deltas accumulate", async () => {
    // A 50px wheel-up inside the 120px band leaves the lock engaged by
    // design, so the follow-on-growth effect used to undo it on the very next delta — the
    // operator could not scroll up while following. The effect now bails while the genuine-input
    // window is open AND the landing is displaced from the end.
    vi.useFakeTimers();
    try {
      const { viewport, rerender } = await mountSettledAtBottom(); // 5400, locked at the end

      // One 50px wheel-up inside the band (distance 50 ≤ 120: the lock stays engaged)…
      fireEvent.wheel(viewport);
      viewport.scrollTop = 5350;
      fireEvent.scroll(viewport);

      // …then a stream delta grows the last row in place (same keys, longer content): before the
      // fix this snapped the offset to 5700; mid-gesture the landing now HOLDS.
      pinGeometry(viewport, 6300, 600);
      const streamed = feedOf(10);
      streamed[9] = msg({
        itemId: "m-10",
        globalOrdinal: 10,
        blocks: [{ blockId: "m-10-b", type: "markdown", markdown: "hi\nstreamed more\nand more" }],
      });
      rerender(
        <ConversationTimeline items={streamed} hasOlder={false} busy={false} onLoadOlder={() => {}} />,
      );
      expect(viewport.scrollTop).toBe(5350);

      // The gesture continues past the band (distance 390 > 120): the lock disengages and stays
      // disengaged — the next arrival holds the accumulated position and counts into the pill.
      fireEvent.wheel(viewport);
      viewport.scrollTop = 5310;
      fireEvent.scroll(viewport);
      pinGeometry(viewport, 6600, 600);
      rerender(
        <ConversationTimeline items={feedOf(11)} hasOlder={false} busy={false} onLoadOlder={() => {}} />,
      );
      expect(viewport.scrollTop).toBe(5310);
      expect(screen.getByTestId("conversation-new-updates").textContent).toContain("1 new");

      // After the gesture window decays the position still holds exactly (the operator left the
      // band — an engaged lock would only resume following had they stayed within it).
      await act(async () => { await vi.advanceTimersByTimeAsync(600); });
      pinGeometry(viewport, 7200, 600);
      rerender(
        <ConversationTimeline items={feedOf(12)} hasOlder={false} busy={false} onLoadOlder={() => {}} />,
      );
      expect(viewport.scrollTop).toBe(5310);
    } finally {
      vi.useRealTimers();
    }
  });

  it("an in-band wheel-up SURVIVES the 500ms interaction decay — a later stream delta does not yank it back to the end (M10)", async () => {
    // The gap: the follow-on-growth guard keyed ONLY on userInteractionRef (500ms decay). A
    // 50px wheel-up leaves the lock engaged (distance ≤ 120 = in-band = following), so nothing but
    // that transient window held the offset — a delta arriving after the window snapped the
    // operator back to the end. The durable, input-only bandHoldRef now owns the hold. This test is
    // mutation-sensitive: revert the guard to `userInteractionRef.current && …` (or drop the
    // bandHoldRef arm in handleScroll) and, after the decay below, the delta re-pins to 5700 —
    // the assertion fails.
    vi.useFakeTimers();
    try {
      const { viewport, rerender } = await mountSettledAtBottom(); // 5400, locked at the end

      // One 50px wheel-up, parked INSIDE the 120px band (distance 50): the lock stays engaged.
      fireEvent.wheel(viewport);
      viewport.scrollTop = 5350;
      fireEvent.scroll(viewport);

      // The genuine-input window fully decays BEFORE the next delta (this is the whole point — the
      // old guard had nothing left to hold with once userInteractionRef cleared).
      await act(async () => { await vi.advanceTimersByTimeAsync(600); });

      // An in-place stream delta (last row grows, NO key change → the follow-on-growth effect).
      pinGeometry(viewport, 6300, 600);
      const streamed = feedOf(10);
      streamed[9] = msg({
        itemId: "m-10",
        globalOrdinal: 10,
        blocks: [{ blockId: "m-10-b", type: "markdown", markdown: "hi\nstreamed more\nand more" }],
      });
      rerender(
        <ConversationTimeline items={streamed} hasOlder={false} busy={false} onLoadOlder={() => {}} />,
      );
      // Held EXACTLY at the parked offset — NOT snapped to the end (6300 − 600 = 5700).
      expect(viewport.scrollTop).toBe(5350);
    } finally {
      vi.useRealTimers();
    }
  });

  it("an in-band parked operator is not yanked by a NEW ROW after the decay either — the arrival counts into the pill (M10)", async () => {
    // Same durable in-band hold across the sibling code path: a new item (key change) routes
    // through the row-key follow effect, not the follow-on-growth effect. bandHoldRef gates that
    // scrollToIndex too, so the parked offset holds and the arrival pills. Mutation-sensitive:
    // revert the row-key branch to `else if (nearBottomRef.current)` and the new row follows to the
    // end (no pill) — the pill assertion below fails (queryByTestId returns null).
    vi.useFakeTimers();
    try {
      const { viewport, rerender } = await mountSettledAtBottom(); // 5400, locked at the end

      fireEvent.wheel(viewport);
      viewport.scrollTop = 5350; // distance 50, in-band
      fireEvent.scroll(viewport);
      await act(async () => { await vi.advanceTimersByTimeAsync(600); }); // decay the input window

      // A genuinely new row (key change) arrives.
      pinGeometry(viewport, 6600, 600);
      rerender(
        <ConversationTimeline items={feedOf(11)} hasOlder={false} busy={false} onLoadOlder={() => {}} />,
      );
      // The parked offset holds, and the new row is reported through the pill instead of a jump.
      expect(viewport.scrollTop).toBe(5350);
      expect(screen.getByTestId("conversation-new-updates").textContent).toContain("1 new");
    } finally {
      vi.useRealTimers();
    }
  });

  it("the bottom-follow contract is intact: parked AT the exact end, a delta after the decay still re-pins to the new end (F-ac, the M10 over-fix guard)", async () => {
    // The counterweight to the two tests above: the in-band hold must NOT swallow the ordinary
    // bottom-follow. At the true end (distance 0) bandHoldRef is clear, so growth re-pins. A naive
    // "hold whenever displaced" fix (guard on bare `distance > 0`) would freeze at 5400 here — this
    // asserts 5700, catching that over-fix.
    vi.useFakeTimers();
    try {
      const { viewport, rerender } = await mountSettledAtBottom(); // 5400, at the exact end
      await act(async () => { await vi.advanceTimersByTimeAsync(600); });

      pinGeometry(viewport, 6300, 600);
      const streamed = feedOf(10);
      streamed[9] = msg({
        itemId: "m-10",
        globalOrdinal: 10,
        blocks: [{ blockId: "m-10-b", type: "markdown", markdown: "hi\nstreamed more\nand more" }],
      });
      rerender(
        <ConversationTimeline items={streamed} hasOlder={false} busy={false} onLoadOlder={() => {}} />,
      );
      expect(viewport.scrollTop).toBe(5700); // 6300 − 600: followed to the new end
    } finally {
      vi.useRealTimers();
    }
  });
});

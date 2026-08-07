import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ConversationTimeline } from "./ConversationTimeline";
import { msg } from "./test-utils";
import type { ConversationItem } from "../../../../data/conversation/types";

describe("ConversationTimeline — upscroll anchor preservation (B3)", () => {
  // Same jsdom geometry shim as the scroll-memory and intent-lock describes, plus a controllable ResizeObserver: jsdom
  // ships none, so without it the virtualizer's only measurement path is the ref attach. The mock
  // re-measures a row through the PRODUCTION path (RO entry → resizeItem → measurement commit)
  // after the test pins a new offsetHeight on the row element.
  const alignedTops: number[] = [];
  const roCallbacks: Array<{ targets: Set<Element>; cb: ResizeObserverCallback }> = [];

  class MockResizeObserver {
    readonly targets = new Set<Element>();
    constructor(readonly cb: ResizeObserverCallback) {
      roCallbacks.push({ targets: this.targets, cb });
    }
    observe(target: Element): void {
      this.targets.add(target);
    }
    unobserve(target: Element): void {
      this.targets.delete(target);
    }
    disconnect(): void {
      this.targets.clear();
    }
  }

  function triggerRowResize(element: Element): void {
    for (const registered of roCallbacks) {
      if (registered.targets.has(element)) {
        registered.cb([{ target: element } as unknown as ResizeObserverEntry], {} as ResizeObserver);
      }
    }
  }

  const originalResizeObserver = window.ResizeObserver;
  beforeEach(() => {
    alignedTops.length = 0;
    roCallbacks.length = 0;
    (window as unknown as { ResizeObserver: unknown }).ResizeObserver = MockResizeObserver;
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
    (window as unknown as { ResizeObserver: unknown }).ResizeObserver = originalResizeObserver;
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

  function rowElement(container: HTMLElement, key: string): HTMLElement {
    const el = container.querySelector(`[data-row-key="${key}"]`);
    expect(el).not.toBeNull();
    return el as HTMLElement;
  }

  it("detaches TanStack observers and manual scroll work while hidden without losing DOM identity or scroll state", async () => {
    vi.useFakeTimers();
    try {
      const onScrollMemory = vi.fn();
      const items = feedOf(10);
      const { container, rerender } = render(
        <ConversationTimeline
          items={items}
          hasOlder={false}
          busy={false}
          onLoadOlder={() => {}}
          onScrollMemory={onScrollMemory}
        />,
      );
      const viewport = screen.getByTestId("conversation-viewport");
      pinGeometry(viewport, 6000, 600);
      await act(async () => { await vi.advanceTimersByTimeAsync(250); });
      const lastRow = rowElement(container, "m-10");
      expect(roCallbacks.some((registered) => registered.targets.size > 0)).toBe(true);

      fireEvent.wheel(viewport);
      viewport.scrollTop = 321;
      fireEvent.scroll(viewport);
      onScrollMemory.mockClear();

      rerender(
        <ConversationTimeline
          items={items}
          hasOlder={false}
          busy={false}
          onLoadOlder={() => {}}
          onScrollMemory={onScrollMemory}
          visible={false}
        />,
      );
      expect(roCallbacks.every((registered) => registered.targets.size === 0)).toBe(true);
      fireEvent.scroll(viewport);
      expect(onScrollMemory).not.toHaveBeenCalled();
      expect(screen.getByTestId("conversation-viewport")).toBe(viewport);
      expect(rowElement(container, "m-10")).toBe(lastRow);
      expect(viewport.scrollTop).toBe(321);

      rerender(
        <ConversationTimeline
          items={items}
          hasOlder={false}
          busy={false}
          onLoadOlder={() => {}}
          onScrollMemory={onScrollMemory}
          visible
        />,
      );
      expect(roCallbacks.some((registered) => registered.targets.size > 0)).toBe(true);
      expect(screen.getByTestId("conversation-viewport")).toBe(viewport);
      expect(rowElement(container, "m-10")).toBe(lastRow);
      expect(viewport.scrollTop).toBe(321);
    } finally {
      vi.useRealTimers();
    }
  });

  it("a row growing above the scrolled-up viewport compensates scrollTop by the delta — and the write never flips the intent lock", async () => {
    vi.useFakeTimers();
    try {
      const onScrollMemory = vi.fn();
      const { container, rerender } = render(
        <ConversationTimeline items={feedOf(30)} hasOlder={false} busy={false} onLoadOlder={() => {}} onScrollMemory={onScrollMemory} />,
      );
      const viewport = screen.getByTestId("conversation-viewport");
      pinGeometry(viewport, 18000, 600); // 30 rows × 600 (jsdom rows all measure 600)
      await act(async () => { await vi.advanceTimersByTimeAsync(250); });
      expect(viewport.scrollTop).toBe(17400); // mount drive landed at the end

      // The operator deliberately scrolls up to m-11's top. Two steps: the second, upward step
      // registers the BACKWARD scroll direction, under which the virtualizer's own measurement
      // adjustment skips re-measurements — exactly the production gap the anchor covers.
      fireEvent.wheel(viewport);
      viewport.scrollTop = 9000;
      fireEvent.scroll(viewport);
      fireEvent.wheel(viewport);
      viewport.scrollTop = 6000;
      fireEvent.scroll(viewport);
      expect(onScrollMemory).toHaveBeenLastCalledWith({ scrollTop: 6000, atBottom: false });

      // m-5 (above the viewport) re-measures taller: every start below it shifts by +300, so the
      // anchor must compensate scrollTop by exactly the growth, in the same commit.
      const grown = rowElement(container, "m-5");
      Object.defineProperty(grown, "offsetHeight", { configurable: true, value: 900 });
      act(() => { triggerRowResize(grown); });
      expect(viewport.scrollTop).toBe(6300);

      // The anchor write is content-preserving: its (jsdom-silent) scroll echo reports the
      // compensated position but never re-engages the lock — a later arrival still pills and the
      // position holds exactly (growth below the viewport: no-op).
      fireEvent.scroll(viewport);
      expect(onScrollMemory).toHaveBeenLastCalledWith({ scrollTop: 6300, atBottom: false });
      pinGeometry(viewport, 18600, 600);
      rerender(
        <ConversationTimeline items={feedOf(31)} hasOlder={false} busy={false} onLoadOlder={() => {}} onScrollMemory={onScrollMemory} />,
      );
      expect(viewport.scrollTop).toBe(6300);
      expect(screen.getByTestId("conversation-new-updates").textContent).toContain("1 new");
    } finally {
      vi.useRealTimers();
    }
  });

  it("never fires while the intent lock is engaged — the follow re-pin owns the end", async () => {
    vi.useFakeTimers();
    try {
      const { container } = render(
        <ConversationTimeline items={feedOf(10)} hasOlder={false} busy={false} onLoadOlder={() => {}} />,
      );
      const viewport = screen.getByTestId("conversation-viewport");
      pinGeometry(viewport, 6000, 600);
      await act(async () => { await vi.advanceTimersByTimeAsync(250); });
      expect(viewport.scrollTop).toBe(5400);

      // Record an anchor mid-feed (disengaged), then genuinely re-engage at the bottom.
      fireEvent.wheel(viewport);
      viewport.scrollTop = 3000;
      fireEvent.scroll(viewport);
      fireEvent.wheel(viewport);
      viewport.scrollTop = 5400;
      fireEvent.scroll(viewport);

      // A row above re-measures taller while LOCKED: the follow re-pins the (pinned) end 5400; the
      // anchor must not add its +300 delta on top (that would land at 5700).
      const grown = rowElement(container, "m-3");
      Object.defineProperty(grown, "offsetHeight", { configurable: true, value: 900 });
      act(() => { triggerRowResize(grown); });
      expect(viewport.scrollTop).toBe(5400);
    } finally {
      vi.useRealTimers();
    }
  });

  it("never fires while a restore is armed — the restore owns the offset", async () => {
    vi.useFakeTimers();
    try {
      const items = feedOf(10);
      const { container, rerender } = render(
        <ConversationTimeline items={items} hasOlder={false} busy={false} onLoadOlder={() => {}} visible={false} restoreScroll={{ scrollTop: 611, atBottom: false }} />,
      );
      const viewport = screen.getByTestId("conversation-viewport");
      pinGeometry(viewport, 0, 0);
      rerender(
        <ConversationTimeline items={items} hasOlder={false} busy={false} onLoadOlder={() => {}} visible restoreScroll={{ scrollTop: 611, atBottom: false }} />,
      );
      pinGeometry(viewport, 876, 600); // partial: maxScroll 276 < 611 — the restore stays armed

      // A re-measurement while armed must not move the offset: the restore owns it.
      const grown = rowElement(container, "m-2");
      Object.defineProperty(grown, "offsetHeight", { configurable: true, value: 900 });
      act(() => { triggerRowResize(grown); });
      expect(viewport.scrollTop).toBe(0);

      // Measurements recover: the restore applies exactly the remembered offset, never anchor-adjusted.
      pinGeometry(viewport, 6000, 600);
      await act(async () => { await vi.advanceTimersByTimeAsync(250); });
      expect(viewport.scrollTop).toBe(611);
    } finally {
      vi.useRealTimers();
    }
  });

  it("the older-prepend anchor still wins over the measurement anchor", async () => {
    vi.useFakeTimers();
    try {
      const { rerender } = render(
        <ConversationTimeline items={feedOf(30)} hasOlder busy={false} onLoadOlder={() => {}} />,
      );
      const viewport = screen.getByTestId("conversation-viewport");
      pinGeometry(viewport, 18000, 600);
      await act(async () => { await vi.advanceTimersByTimeAsync(250); });

      // Operator scrolled up: the prepend anchor (first mounted row, m-3) and the measurement
      // anchor (first visible row, 150px into m-11) are both recorded.
      fireEvent.wheel(viewport);
      viewport.scrollTop = 6150;
      fireEvent.scroll(viewport);
      // Let the isScrolling debounce lapse so the prepended rows measure on ref attach (in
      // production the RO measures them immediately; jsdom defers while isScrolling is true).
      await act(async () => { await vi.advanceTimersByTimeAsync(200); });

      const prepended = [
        ...Array.from({ length: 5 }, (_, index) => msg({ itemId: `old-${index + 1}`, globalOrdinal: 100 + index })),
        ...feedOf(30),
      ];
      pinGeometry(viewport, 21000, 600); // 35 × 600
      rerender(
        <ConversationTimeline items={prepended} hasOlder busy={false} onLoadOlder={() => {}} />,
      );

      // The prepend path owns the landing: its scrollToIndex(recorded first-mounted row, align:
      // start) is the last programmatic write. (The 1600 landing is estimate-degenerate: jsdom's
      // prepended rows never get their RO measurement, so the virtualizer cannot converge to the
      // honest 7 × 600 = 4200 here — in production the RO measures them and the reconcile
      // re-drives. What this test pins is the invariant:) the measurement anchor's exact-offset
      // correction (m-11's new start + 150 = 9150) must NOT fire on the prepend commit — a direct
      // anchor write would not appear in alignedTops, so scrollTop must equal the prepend landing.
      await act(async () => { await vi.advanceTimersByTimeAsync(250); });
      expect(viewport.scrollTop).toBe(1600);
      expect(viewport.scrollTop).toBe(alignedTops[alignedTops.length - 1]);
      expect(viewport.scrollTop).not.toBe(9150);
    } finally {
      vi.useRealTimers();
    }
  });
});

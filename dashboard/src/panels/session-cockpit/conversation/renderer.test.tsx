import axe from "axe-core";
import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

// jsdom has no layout engine, so @tanstack/react-virtual measures a 0-height viewport and renders no
// rows. Give elements a fixed geometry for this file so the virtualizer produces the feed rows the
// semantics tests assert against (a standard jsdom + tanstack-virtual shim).
beforeAll(() => {
  // tanstack-virtual reads offsetWidth/offsetHeight (getRect) synchronously on mount.
  Object.defineProperty(HTMLElement.prototype, "offsetHeight", { configurable: true, value: 600 });
  Object.defineProperty(HTMLElement.prototype, "offsetWidth", { configurable: true, value: 800 });
});

import type { ConversationItem } from "../../../data/conversation/types";
import { ConversationTimeline } from "./ConversationTimeline";
import { MessageItem } from "./MessageItem";
import { TerminalDiagnosticsDrawer } from "./TerminalDiagnosticsDrawer";

function msg(overrides: Partial<ConversationItem> & { itemId: string; globalOrdinal: number }): ConversationItem {
  return {
    revision: 1,
    lane: "harness",
    source: "harness-live",
    provenance: { strength: "exact", origin: "codex" },
    role: "assistant",
    kind: "message",
    phase: "completed",
    blocks: [{ blockId: `${overrides.itemId}-b`, type: "markdown", markdown: "hi" }],
    ...overrides,
  };
}

describe("ConversationTimeline — one navigable role=feed (R5, §14.2)", () => {
  it("exposes a role=feed and articles keyed to the server globalOrdinal, with aria-setsize when total is known", () => {
    render(
      <ConversationTimeline
        items={[msg({ itemId: "a", globalOrdinal: 7 }), msg({ itemId: "b", globalOrdinal: 8 })]}
        totalItems={2}
        hasOlder={false}
        busy={false}
        onLoadOlder={() => {}}
      />,
    );
    const feed = screen.getByRole("feed");
    expect(feed).not.toBeNull();
    const articles = within(feed).getAllByRole("article");
    expect(articles.length).toBeGreaterThan(0);
    // aria-posinset comes from the server ordinal, never the array index.
    expect(articles[0].getAttribute("aria-posinset")).toBe("7");
    expect(articles[0].getAttribute("aria-setsize")).toBe("2");
    expect(articles[0].getAttribute("aria-live")).toBe("off");
  });

  it("omits aria-setsize and says 'total unknown' on the pager when the total is not honestly known", () => {
    render(
      <ConversationTimeline
        items={[msg({ itemId: "a", globalOrdinal: 1 })]}
        totalItems={undefined}
        hasOlder
        busy={false}
        onLoadOlder={() => {}}
      />,
    );
    const article = screen.getAllByRole("article")[0];
    expect(article.getAttribute("aria-setsize")).toBeNull();
    expect(screen.getByTestId("conversation-load-older").textContent).toContain("total unknown");
  });
});

describe("ConversationTimeline — scroll memory (F-ac)", () => {
  // A cockpit VIEW switch hides the Chats layer with
  // display:none, which destroys the scroll offset — the chat reopened at the START. The position
  // is remembered continuously and restored pre-paint on re-show: middle stays middle, top stays
  // top; at-bottom lands at the CURRENT end (items arrived while away included) and re-arms the
  // follow. jsdom's Element.scrollTo is undefined, so a prototype shim makes the virtualizer's
  // alignments observable (scoped to this describe). tanstack's end-alignment also reads
  // scrollHeight/clientHeight off the scroll element (both 0 under jsdom), so honest geometry
  // defaults are pinned on the prototype — per-test per-element pins still shadow them.
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

  it("reports the position on every scroll — exact offset, and the bottom band as atBottom", () => {
    const onScrollMemory = vi.fn();
    render(
      <ConversationTimeline
        items={feedOf(10)}
        hasOlder={false}
        busy={false}
        onLoadOlder={() => {}}
        onScrollMemory={onScrollMemory}
      />,
    );
    const viewport = screen.getByTestId("conversation-viewport");
    pinGeometry(viewport, 6000, 600);

    // Disengagement is intent-based — a bare scroll event is content-driven and never flips
    // the lock. The operator's scroll is preceded by trusted input (wheel), as in production.
    fireEvent.wheel(viewport);
    viewport.scrollTop = 456;
    fireEvent.scroll(viewport);
    expect(onScrollMemory).toHaveBeenLastCalledWith({ scrollTop: 456, atBottom: false });

    fireEvent.wheel(viewport);
    viewport.scrollTop = 5400; // 6000 - 600: exactly at the end
    fireEvent.scroll(viewport);
    expect(onScrollMemory).toHaveBeenLastCalledWith({ scrollTop: 5400, atBottom: true });
  });

  it("a box-less (display:none collapse) scroll event never overwrites the remembered position", () => {
    // The live failure: the teardown's clamp event (st=0/sh=0/ch=0) clobbered the memory, so the
    // re-show restored 0 and the chat stayed at the START.
    const onScrollMemory = vi.fn();
    const items = feedOf(10);
    const { rerender } = render(
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
    fireEvent.wheel(viewport); // the operator's scroll-up arrives with trusted input
    viewport.scrollTop = 400;
    fireEvent.scroll(viewport);
    expect(onScrollMemory).toHaveBeenLastCalledWith({ scrollTop: 400, atBottom: false });
    const calls = onScrollMemory.mock.calls.length;

    // Hidden + box-less: the collapse event is ignored entirely.
    rerender(
      <ConversationTimeline
        items={items}
        hasOlder={false}
        busy={false}
        onLoadOlder={() => {}}
        visible={false}
        onScrollMemory={onScrollMemory}
      />,
    );
    pinGeometry(viewport, 0, 0);
    viewport.scrollTop = 0;
    fireEvent.scroll(viewport);
    expect(onScrollMemory).toHaveBeenCalledTimes(calls);

    // Hidden but geometry reading non-zero (a mid-teardown quirk): still ignored.
    pinGeometry(viewport, 6000, 600);
    viewport.scrollTop = 0;
    fireEvent.scroll(viewport);
    expect(onScrollMemory).toHaveBeenCalledTimes(calls);
  });

  it("restores the remembered middle offset on re-show, then never yanks on new arrivals (pill covers them)", () => {
    const items = feedOf(10);
    const { rerender } = render(
      <ConversationTimeline
        items={items}
        hasOlder={false}
        busy={false}
        onLoadOlder={() => {}}
      />,
    );
    const viewport = screen.getByTestId("conversation-viewport");
    pinGeometry(viewport, 2000, 600);
    viewport.scrollTop = 456;
    fireEvent.scroll(viewport);

    // The hide (display:none resets the offset silently) then the re-show carrying the memory.
    rerender(
      <ConversationTimeline
        items={items}
        hasOlder={false}
        busy={false}
        onLoadOlder={() => {}}
        visible={false}
        restoreScroll={{ scrollTop: 456, atBottom: false }}
      />,
    );
    viewport.scrollTop = 0;
    rerender(
      <ConversationTimeline
        items={items}
        hasOlder={false}
        busy={false}
        onLoadOlder={() => {}}
        visible
        restoreScroll={{ scrollTop: 456, atBottom: false }}
      />,
    );
    expect(screen.getByTestId("conversation-viewport")).toBe(viewport); // never remounted
    expect(viewport.scrollTop).toBe(456); // restored, not the top

    // Not at-bottom: an arrival after the return counts into the "N new updates" pill.
    rerender(
      <ConversationTimeline
        items={feedOf(11)}
        hasOlder={false}
        busy={false}
        onLoadOlder={() => {}}
        visible
        restoreScroll={{ scrollTop: 456, atBottom: false }}
      />,
    );
    expect(viewport.scrollTop).toBe(456);
    expect(screen.getByTestId("conversation-new-updates").textContent).toContain("1 new");
  });

  it("a chat left at the bottom lands at the CURRENT end on re-show — the end moved by items ingested while away", async () => {
    vi.useFakeTimers();
    try {
      const endAlignmentFor = async (count: number): Promise<number> => {
        const items = feedOf(count);
        const { rerender, unmount } = render(
          <ConversationTimeline
            items={items}
            hasOlder={false}
            busy={false}
            onLoadOlder={() => {}}
            visible={false}
            restoreScroll={{ scrollTop: 1400, atBottom: true }}
          />,
        );
        const viewport = screen.getByTestId("conversation-viewport");
        // The content's honest scrollable height at this item count (rows measure 600 here).
        pinGeometry(viewport, count * 600, 600);
        rerender(
          <ConversationTimeline
            items={items}
            hasOlder={false}
            busy={false}
            onLoadOlder={() => {}}
            visible
            restoreScroll={{ scrollTop: 1400, atBottom: true }}
          />,
        );
        await act(async () => { await vi.advanceTimersByTimeAsync(250); });
        const top = viewport.scrollTop;
        unmount();
        return top;
      };
      // The restore drives to the CURRENT DOM end: a history that grew while away lands strictly
      // beyond the end a shorter history would produce.
      expect(await endAlignmentFor(13)).toBeGreaterThan(await endAlignmentFor(10));
    } finally {
      vi.useRealTimers();
    }
  });

  it("a middle restore stays armed through the re-measure window and applies exactly once, when the content can contain it", () => {
    // The failure: on re-show the virtualizer re-measures across a multi-frame window
    // (feed height 0px at +2ms, true scrollHeight ~54ms later) — a one-shot apply in the re-show
    // commit was clamped to 0 by the browser. The restore must wait for honest geometry. (jsdom
    // note: pinGeometry plays the window's frames; the atBottom analog — scrollToIndex plus the
    // virtualizer's own frame-by-frame scroll reconcile converging to the recovering end — cannot
    // be reproduced here because jsdom never loses the virtualizer's measurements.)
    const items = feedOf(10);
    const { rerender } = render(
      <ConversationTimeline items={items} hasOlder={false} busy={false} onLoadOlder={() => {}} />,
    );
    const viewport = screen.getByTestId("conversation-viewport");
    pinGeometry(viewport, 2000, 600);
    viewport.scrollTop = 611;
    fireEvent.scroll(viewport);

    // Hide: the offset is destroyed with the box.
    rerender(
      <ConversationTimeline items={items} hasOlder={false} busy={false} onLoadOlder={() => {}} visible={false} restoreScroll={{ scrollTop: 611, atBottom: false }} />,
    );
    pinGeometry(viewport, 0, 0);
    viewport.scrollTop = 0;

    // Re-show commit lands INSIDE the re-measure window: still box-less, then partial.
    rerender(
      <ConversationTimeline items={items} hasOlder={false} busy={false} onLoadOlder={() => {}} visible restoreScroll={{ scrollTop: 611, atBottom: false }} />,
    );
    expect(viewport.scrollTop).toBe(0); // not applied against a collapsed box
    pinGeometry(viewport, 876, 600); // partial measurements (maxScroll 276 < 611)
    rerender(
      <ConversationTimeline items={items} hasOlder={false} busy={false} onLoadOlder={() => {}} visible restoreScroll={{ scrollTop: 611, atBottom: false }} />,
    );
    expect(viewport.scrollTop).toBe(0); // still armed — partial geometry cannot contain 611

    // Measurements recover: the restore applies once, exactly, and is consumed.
    pinGeometry(viewport, 1962, 600);
    rerender(
      <ConversationTimeline items={items} hasOlder={false} busy={false} onLoadOlder={() => {}} visible restoreScroll={{ scrollTop: 611, atBottom: false }} />,
    );
    expect(viewport.scrollTop).toBe(611);
    pinGeometry(viewport, 2200, 600); // a later append arrives
    rerender(
      <ConversationTimeline items={feedOf(11)} hasOlder={false} busy={false} onLoadOlder={() => {}} visible restoreScroll={{ scrollTop: 611, atBottom: false }} />,
    );
    expect(viewport.scrollTop).toBe(611); // consumed — never re-applied, no yank
  });

  it("the operator always wins: a scroll while the restore is still armed cancels it", () => {
    const items = feedOf(10);
    const { rerender } = render(
      <ConversationTimeline items={items} hasOlder={false} busy={false} onLoadOlder={() => {}} />,
    );
    const viewport = screen.getByTestId("conversation-viewport");
    pinGeometry(viewport, 2000, 600);
    viewport.scrollTop = 611;
    fireEvent.scroll(viewport);

    rerender(
      <ConversationTimeline items={items} hasOlder={false} busy={false} onLoadOlder={() => {}} visible={false} restoreScroll={{ scrollTop: 611, atBottom: false }} />,
    );
    pinGeometry(viewport, 0, 0);
    viewport.scrollTop = 0;
    // Re-show inside the window: restore armed, not yet applied (partial geometry — the apply
    // gate cannot pass at 876, so nothing can fire before the operator's event).
    rerender(
      <ConversationTimeline items={items} hasOlder={false} busy={false} onLoadOlder={() => {}} visible restoreScroll={{ scrollTop: 611, atBottom: false }} />,
    );
    pinGeometry(viewport, 876, 600);
    rerender(
      <ConversationTimeline items={items} hasOlder={false} busy={false} onLoadOlder={() => {}} visible restoreScroll={{ scrollTop: 611, atBottom: false }} />,
    );

    // The operator scrolls before the measurements recover — TRUSTED input (a wheel event, never
    // present on a programmatic clamp) stands the restore down for good. The scroll event itself
    // arrives at partial geometry and is echo-gated: it can neither report nor re-arm.
    viewport.scrollTop = 100;
    fireEvent.wheel(viewport);
    fireEvent.scroll(viewport);
    pinGeometry(viewport, 1962, 600);
    rerender(
      <ConversationTimeline items={items} hasOlder={false} busy={false} onLoadOlder={() => {}} visible restoreScroll={{ scrollTop: 611, atBottom: false }} />,
    );
    expect(viewport.scrollTop).toBe(100);
  });

  it("the late re-show clamp echo neither clobbers memory nor cancels the restore — rAF applies after recovery with zero renders", async () => {
    // ~0.5s after re-show the browser dispatches the collapse's clamp
    // event LATE — box already restored (clientHeight>0, passing the clientHeight guard) but the feed
    // transiently collapsed (st=0, sh≈ch; the next frame reads the true sh). It must be ignored
    // for BOTH memory and cancel. And the apply must fire from the rAF driver: TanStack's sizes
    // survive display:none, so no render re-runs the effect in the window.
    vi.useFakeTimers();
    try {
      const onScrollMemory = vi.fn();
      const items = feedOf(10);
      const { rerender } = render(
        <ConversationTimeline items={items} hasOlder={false} busy={false} onLoadOlder={() => {}} onScrollMemory={onScrollMemory} />,
      );
      const viewport = screen.getByTestId("conversation-viewport");
      pinGeometry(viewport, 6000, 741);
      fireEvent.wheel(viewport); // the operator's scroll-up arrives with trusted input
      viewport.scrollTop = 611;
      fireEvent.scroll(viewport);
      expect(onScrollMemory).toHaveBeenLastCalledWith({ scrollTop: 611, atBottom: false });

      // Hide (offset destroyed), then re-show inside the window (feed still collapsed).
      rerender(
        <ConversationTimeline items={items} hasOlder={false} busy={false} onLoadOlder={() => {}} visible={false} restoreScroll={{ scrollTop: 611, atBottom: false }} onScrollMemory={onScrollMemory} />,
      );
      pinGeometry(viewport, 0, 0);
      viewport.scrollTop = 0;
      rerender(
        <ConversationTimeline items={items} hasOlder={false} busy={false} onLoadOlder={() => {}} visible restoreScroll={{ scrollTop: 611, atBottom: false }} onScrollMemory={onScrollMemory} />,
      );

      // The late clamp echo: clientHeight restored, feed collapsed to one viewport, offset 0.
      pinGeometry(viewport, 741, 741);
      viewport.scrollTop = 0;
      fireEvent.scroll(viewport);
      // Neither reported (memory intact) nor treated as the operator (restore still armed).
      expect(onScrollMemory).toHaveBeenLastCalledWith({ scrollTop: 611, atBottom: false });

      // Geometry recovers WITHOUT any render — the rAF driver applies, once.
      pinGeometry(viewport, 6000, 741);
      await act(async () => {
        await vi.advanceTimersByTimeAsync(20);
      });
      expect(viewport.scrollTop).toBe(611);
      await act(async () => {
        await vi.advanceTimersByTimeAsync(100);
      });
      expect(viewport.scrollTop).toBe(611); // consumed — never re-applied
    } finally {
      vi.useRealTimers();
    }
  });

  it("a chat left at the top is not inverted to the bottom by the clamp echo", () => {
    // The echo (st=0, sh collapsed to one viewport ⇒ distance 0) reads as AT-BOTTOM — reported,
    // it would clobber {0,false} into {0,true} and the next re-show would fire scrollToIndex(end)
    // (the live top→bottom inversion).
    const onScrollMemory = vi.fn();
    const items = feedOf(10);
    const { rerender } = render(
      <ConversationTimeline items={items} hasOlder={false} busy={false} onLoadOlder={() => {}} onScrollMemory={onScrollMemory} />,
    );
    const viewport = screen.getByTestId("conversation-viewport");
    pinGeometry(viewport, 6000, 741);
    fireEvent.wheel(viewport); // the operator's scroll to the top arrives with trusted input
    viewport.scrollTop = 0; // left at the very top
    fireEvent.scroll(viewport);
    expect(onScrollMemory).toHaveBeenLastCalledWith({ scrollTop: 0, atBottom: false });

    rerender(
      <ConversationTimeline items={items} hasOlder={false} busy={false} onLoadOlder={() => {}} visible={false} restoreScroll={{ scrollTop: 0, atBottom: false }} onScrollMemory={onScrollMemory} />,
    );
    rerender(
      <ConversationTimeline items={items} hasOlder={false} busy={false} onLoadOlder={() => {}} visible restoreScroll={{ scrollTop: 0, atBottom: false }} onScrollMemory={onScrollMemory} />,
    );
    // The echo arrives: collapsed feed reads as bottom — ignored; the memory stays {0, false}.
    pinGeometry(viewport, 741, 741);
    viewport.scrollTop = 0;
    fireEvent.scroll(viewport);
    expect(onScrollMemory).toHaveBeenLastCalledWith({ scrollTop: 0, atBottom: false });
  });

  it("a viewport settle that lowers maxScroll never dead-locks the memory (F1)", () => {
    // The last streaming report was (1260,true) at ch=722 (maxScroll 1260); the viewport
    // then settled to ch=741 (maxScroll 1241) and the old gate 1 (maxScroll+1 < reference) rejected
    // EVERY later event — the memory froze at bottom, so a later middle/top scroll never recorded
    // and every re-show landed at the END. Gate 1 is now scoped to the ARMED restore only.
    const onScrollMemory = vi.fn();
    render(
      <ConversationTimeline items={feedOf(10)} hasOlder={false} busy={false} onLoadOlder={() => {}} onScrollMemory={onScrollMemory} />,
    );
    const viewport = screen.getByTestId("conversation-viewport");
    // Bottom-most report at the building viewport height (maxScroll = 6000-722 = 5278).
    pinGeometry(viewport, 6000, 722);
    viewport.scrollTop = 5278;
    fireEvent.scroll(viewport);
    expect(onScrollMemory).toHaveBeenLastCalledWith({ scrollTop: 5278, atBottom: true });

    // The viewport settles taller (maxScroll 5259 < 5278) — the browser's clamp event AND any
    // later operator scroll must still record (never rejected as a stale-reference echo).
    pinGeometry(viewport, 6000, 741);
    viewport.scrollTop = 5259;
    fireEvent.scroll(viewport);
    expect(onScrollMemory).toHaveBeenLastCalledWith({ scrollTop: 5259, atBottom: true });

    fireEvent.wheel(viewport); // the operator's scroll away from the bottom carries input
    viewport.scrollTop = 621; // the operator leaves the bottom for the middle
    fireEvent.scroll(viewport);
    expect(onScrollMemory).toHaveBeenLastCalledWith({ scrollTop: 621, atBottom: false });
  });

  it("the atBottom restore re-drives the CURRENT end until the feed settles — never consuming against degenerate geometry (F2)", async () => {
    // scrollToIndex(end) fired at the re-show commit against estimate-degenerate
    // measurements, computed target 0, consumed, and tanstack's reconcile cleared after ONE stable
    // frame — the chat settled at 0 while the feed recovered 40ms later. The restore now re-drives
    // the pixel end every frame while armed and consumes only once the feed's height settles.
    vi.useFakeTimers();
    try {
      const items = feedOf(10);
      const { rerender } = render(
        <ConversationTimeline items={items} hasOlder={false} busy={false} onLoadOlder={() => {}} visible={false} restoreScroll={{ scrollTop: 1241, atBottom: true }} />,
      );
      const viewport = screen.getByTestId("conversation-viewport");
      pinGeometry(viewport, 0, 0); // still hidden/collapsed
      rerender(
        <ConversationTimeline items={items} hasOlder={false} busy={false} onLoadOlder={() => {}} visible restoreScroll={{ scrollTop: 1241, atBottom: true }} />,
      );

      // The feed recovers in steps across frames; the drive follows the CURRENT end each frame.
      pinGeometry(viewport, 741, 741); // collapsed (estimates-degenerate)
      await act(async () => { await vi.advanceTimersByTimeAsync(20); });
      expect(viewport.scrollTop).toBe(0);
      pinGeometry(viewport, 1500, 741); // partial
      await act(async () => { await vi.advanceTimersByTimeAsync(20); });
      expect(viewport.scrollTop).toBe(759);
      pinGeometry(viewport, 1982, 741); // recovered
      await act(async () => { await vi.advanceTimersByTimeAsync(20); });
      expect(viewport.scrollTop).toBe(1241);

      // The feed settles (10 stable frames) — the restore consumes; nothing re-drives afterwards.
      await act(async () => { await vi.advanceTimersByTimeAsync(300); });
      expect(viewport.scrollTop).toBe(1241);

      // …and from here the ordinary bottom-follow (re-armed by the drive) owns new growth:
      // items arrived while away/after, the follow lands at the NEW end.
      pinGeometry(viewport, 2415, 741);
      rerender(
        <ConversationTimeline items={feedOf(13)} hasOlder={false} busy={false} onLoadOlder={() => {}} visible restoreScroll={{ scrollTop: 1241, atBottom: true }} />,
      );
      expect(viewport.scrollTop).toBe(2415 - 741);
    } finally {
      vi.useRealTimers();
    }
  });

  it("the bottom-follow never fires while the feed is box-less (F3) — the re-show restore owns the landing", async () => {
    // Inflow while display:none fired scrollToIndex four times with degenerate
    // targets, each arming a 5s reconcile that could race the re-show restore.
    vi.useFakeTimers();
    try {
      const items = feedOf(10);
      const { rerender } = render(
        <ConversationTimeline items={items} hasOlder={false} busy={false} onLoadOlder={() => {}} />,
      );
      const viewport = screen.getByTestId("conversation-viewport");
      pinGeometry(viewport, 1982, 741);
      viewport.scrollTop = 1241; // at the bottom
      fireEvent.scroll(viewport);
      alignedTops.length = 0;

      // Hidden + box-less; three items flow in — the follow must NOT fire (no degenerate targets).
      rerender(
        <ConversationTimeline items={items} hasOlder={false} busy={false} onLoadOlder={() => {}} visible={false} restoreScroll={{ scrollTop: 1241, atBottom: true }} />,
      );
      pinGeometry(viewport, 0, 0);
      viewport.scrollTop = 0;
      rerender(
        <ConversationTimeline items={feedOf(13)} hasOlder={false} busy={false} onLoadOlder={() => {}} visible={false} restoreScroll={{ scrollTop: 1241, atBottom: true }} />,
      );
      expect(alignedTops).toEqual([]);

      // Re-show: the restore (not the follow) drives to the CURRENT end, inflow included.
      pinGeometry(viewport, 2415, 741);
      rerender(
        <ConversationTimeline items={feedOf(13)} hasOlder={false} busy={false} onLoadOlder={() => {}} visible restoreScroll={{ scrollTop: 1241, atBottom: true }} />,
      );
      await act(async () => { await vi.advanceTimersByTimeAsync(250); });
      expect(viewport.scrollTop).toBe(2415 - 741);
    } finally {
      vi.useRealTimers();
    }
  });

  it("first-ever open arms the atBottom restore and lands at the latest once geometry is honest (B2); a memory-less re-show never re-aligns", async () => {
    vi.useFakeTimers();
    try {
      const items = feedOf(10);
      const { rerender } = render(
        <ConversationTimeline
          items={items}
          hasOlder={false}
          busy={false}
          onLoadOlder={() => {}}
        />,
      );
      const viewport = screen.getByTestId("conversation-viewport");
      pinGeometry(viewport, 6000, 600);
      // NO one-shot scrollToIndex end-jump — the armed atBottom restore drives to the CURRENT
      // end via the rAF driver and consumes once the feed settles. (alignedTops may carry a 0 from
      // tanstack's mount-time offset reconcile — never the 5400 end-alignment.)
      expect(alignedTops).not.toContain(5400);
      await act(async () => { await vi.advanceTimersByTimeAsync(250); });
      expect(viewport.scrollTop).toBe(5400); // 6000 - 600: landed at the latest, never at 0

      // A hide→show round-trip with no memory (never scrolled, the drive already consumed): the
      // restore stays null — nothing re-aligns or jumps; the live follow alone owns the offset.
      const alignments = alignedTops.length;
      rerender(
        <ConversationTimeline items={items} hasOlder={false} busy={false} onLoadOlder={() => {}} visible={false} />,
      );
      rerender(
        <ConversationTimeline items={items} hasOlder={false} busy={false} onLoadOlder={() => {}} visible />,
      );
      await act(async () => { await vi.advanceTimersByTimeAsync(250); });
      expect(alignedTops.length).toBe(alignments);
      expect(viewport.scrollTop).toBe(5400);
    } finally {
      vi.useRealTimers();
    }
  });

  it("B2: a fresh mount hidden since boot (display:none) stays armed box-less and lands at the bottom once geometry is honest — never at 0", async () => {
    // The restart root cause: the cockpit boots on Operations, so the chats layer is display:none
    // at mount and the old one-shot scrollToIndex(end) no-oped against the collapsed box forever.
    // The armed atBottom restore must wait out the box-less phase and land at the CURRENT end.
    vi.useFakeTimers();
    try {
      const items = feedOf(10);
      const { rerender } = render(
        <ConversationTimeline items={items} hasOlder={false} busy={false} onLoadOlder={() => {}} visible={false} />,
      );
      const viewport = screen.getByTestId("conversation-viewport");
      pinGeometry(viewport, 0, 0); // display:none: no box at all
      await act(async () => { await vi.advanceTimersByTimeAsync(250); });
      expect(viewport.scrollTop).toBe(0); // nothing fired against the hidden box

      // The operator switches to Chats: still inside the re-measure window (box-less)…
      rerender(
        <ConversationTimeline items={items} hasOlder={false} busy={false} onLoadOlder={() => {}} visible />,
      );
      await act(async () => { await vi.advanceTimersByTimeAsync(60); });
      expect(viewport.scrollTop).toBe(0); // stays armed — no degenerate landing

      // …geometry becomes honest: the drive lands at the end and consumes.
      pinGeometry(viewport, 6000, 600);
      await act(async () => { await vi.advanceTimersByTimeAsync(250); });
      expect(viewport.scrollTop).toBe(5400);
    } finally {
      vi.useRealTimers();
    }
  });

  it("a mount carrying a remembered position restores it instead of jumping to the latest", () => {
    render(
      <ConversationTimeline
        items={feedOf(10)}
        hasOlder={false}
        busy={false}
        onLoadOlder={() => {}}
        restoreScroll={{ scrollTop: 456, atBottom: false }}
      />,
    );
    // No open-at-latest jump (the end-alignment to 5400 is gated when a memory is pending)…
    expect(alignedTops).not.toContain(5400);
    // …the remembered offset owns the initial position (pre-paint).
    expect(screen.getByTestId("conversation-viewport").scrollTop).toBe(456);
  });

  it("the restore driver STANDS DOWN on the 2.5s budget under sustained churn — a pixel restore that only becomes appliable AFTER the budget is never applied (L5I Race 1, mechanism)", async () => {
    // Race 1 restated as a MECHANISM, not a pixel (reverting the fix leaves all 1380
    // tests passing because the resulting scrollTop is identical either way). The driver's run
    // state (startedAt) now lives in a ref reset only on (re-)arm, so the 2.5s budget accrues ACROSS
    // the live-frame churn and trips. The pre-fix closure-keyed driver reset startedAt on every rows
    // change (deps [visible, rows.length, tryApplyPendingRestore]), so under churn the budget never
    // tripped and the restore rode the whole turn armed. We prove the stand-down WITHOUT the
    // trusted-input confound the old test relied on (a wheel nulls the restore whether or not the
    // budget tripped): a NON-atBottom restore is held un-appliable by short geometry through the
    // budget window, then geometry is revealed — only a still-armed driver applies the offset.
    vi.useFakeTimers();
    // Short geometry from the first layout effect (maxScroll 2400 < 5000): the render-driven pass
    // cannot apply the remembered offset, so the rAF budget alone owns the stand-down. Pinned on the
    // prototype so it governs before any per-element pin.
    Object.defineProperty(HTMLElement.prototype, "scrollHeight", { configurable: true, value: 3000 });
    try {
      const remembered = { scrollTop: 5000, atBottom: false as const };
      let count = 10;
      const { rerender } = render(
        <ConversationTimeline items={feedOf(count)} hasOlder={false} busy={false} onLoadOlder={() => {}} visible restoreScroll={remembered} />,
      );
      const viewport = screen.getByTestId("conversation-viewport");
      // Sustained inflow for 3.4s (> the 2.5s budget): a rows change every 100ms is exactly the live
      // frame that made the pre-fix driver re-subscribe and zero its budget; the geometry stays
      // short so the restore can never apply during the window.
      for (let elapsed = 0; elapsed < 3400; elapsed += 100) {
        await act(async () => { await vi.advanceTimersByTimeAsync(100); });
        count += 1;
        rerender(
          <ConversationTimeline items={feedOf(count)} hasOlder={false} busy={false} onLoadOlder={() => {}} visible restoreScroll={remembered} />,
        );
      }
      // Reveal honest geometry (maxScroll 5400 ≥ 5000 now permits the pixel restore) and commit once.
      pinGeometry(viewport, 6000, 600);
      count += 1;
      rerender(
        <ConversationTimeline items={feedOf(count)} hasOlder={false} busy={false} onLoadOlder={() => {}} visible restoreScroll={remembered} />,
      );
      await act(async () => { await vi.advanceTimersByTimeAsync(400); });
      // 5000 is the UNIQUE fingerprint of the restore applying (`el.scrollTop = pending.scrollTop`):
      // the follow writes maxScroll and scrollToIndex writes tanstack ends, never exactly 5000. A
      // stood-down restore never teleports there — the ordinary follow owns the honest end (5400).
      expect(viewport.scrollTop).not.toBe(5000);
      expect(viewport.scrollTop).toBe(5400);
    } finally {
      vi.useRealTimers();
    }
  });

  it("the restore driver holds ONE continuous rAF loop across a streaming turn — the ref-keyed run state means its effect never re-subscribes per delta (L5I Race 1, mechanism)", async () => {
    // The direct "count driver effect entries" assertion. The driver effect keys on [visible,
    // rowsEmpty]; a streaming turn changes neither, so the effect's cleanup — the ONLY
    // cancelAnimationFrame caller in the whole component — never runs mid-stream. The pre-fix driver
    // keyed on [visible, rows.length, tryApplyPendingRestore], tearing down and re-subscribing its
    // rAF loop on every delta. Instrumented, that is the 27-re-entries-vs-1 difference,
    // which no pixel assertion can see. Counting cancelAnimationFrame makes it visible.
    vi.useFakeTimers();
    const cancelSpy = vi.spyOn(globalThis, "cancelAnimationFrame");
    try {
      let count = 10;
      const { rerender } = render(
        <ConversationTimeline items={feedOf(count)} hasOlder={false} busy={false} onLoadOlder={() => {}} />,
      );
      const viewport = screen.getByTestId("conversation-viewport");
      pinGeometry(viewport, count * 600, 600);
      // Let the mount driver subscribe and start its single rAF loop (the atBottom mount arm).
      await act(async () => { await vi.advanceTimersByTimeAsync(30); });
      const cancelsAtStart = cancelSpy.mock.calls.length;
      // Eight streamed deltas, each a rows change (a live frame). The moving height keeps the atBottom
      // restore from settling, so the driver stays armed throughout: the ref-keyed effect holds its
      // ONE loop, while a closure-keyed effect re-subscribes (one cleanup → one cancel) per delta.
      for (let i = 0; i < 8; i += 1) {
        await act(async () => { await vi.advanceTimersByTimeAsync(50); });
        count += 1;
        pinGeometry(viewport, count * 600, 600);
        rerender(
          <ConversationTimeline items={feedOf(count)} hasOlder={false} busy={false} onLoadOlder={() => {}} />,
        );
      }
      expect(cancelSpy.mock.calls.length - cancelsAtStart).toBe(0);
    } finally {
      cancelSpy.mockRestore();
      vi.useRealTimers();
    }
  });
});

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

describe("MessageItem — grammar, images, clamp (R3, §12.2)", () => {
  it("renders an image reference with a non-empty accessible alt + provenance, and NO fabricated fetch URL (F11)", () => {
    const { container } = render(
      <MessageItem
        item={msg({
          itemId: "img",
          globalOrdinal: 1,
          blocks: [
            {
              blockId: "img-b",
              type: "image-ref",
              assetId: "asset-1",
              alt: "a bar chart of usage",
              altProvenance: "supplied-description",
              mimeType: "image/png",
            },
          ],
        })}
      />,
    );
    expect(screen.getByTestId("image-alt-provenance").textContent).toContain("a bar chart of usage");
    // No <img> is rendered (no asset-read route exists) — never an invented /api/assets URL.
    expect(container.querySelector("img")).toBeNull();
  });

  it("clamps a long completed assistant message behind a real button with an exact +N lines count", () => {
    const longText = Array.from({ length: 80 }, (_, i) => `line ${i}`).join("\n");
    render(
      <MessageItem
        item={msg({ itemId: "long", globalOrdinal: 1, blocks: [{ blockId: "l-b", type: "markdown", markdown: longText }] })}
      />,
    );
    const clamp = screen.getByTestId("conversation-clamp");
    expect(clamp.tagName).toBe("BUTTON");
    expect(clamp.getAttribute("aria-expanded")).toBe("false");
    expect(clamp.textContent).toMatch(/\+\d+ lines/);
  });

  it("badges an agent-bus delivery (origin changes interpretation) but not an ordinary operator message", () => {
    const { rerender } = render(
      <MessageItem item={msg({ itemId: "bus", globalOrdinal: 1, role: "user", lane: "agent-bus", source: "durable-inbox" })} />,
    );
    expect(screen.getByTestId("conversation-source-badge").textContent).toBe("agent bus");
    rerender(
      <MessageItem item={msg({ itemId: "op", globalOrdinal: 1, role: "user", lane: "operator", source: "cockpit-composer" })} />,
    );
    expect(screen.queryByTestId("conversation-source-badge")).toBeNull();
  });

  it("shows the streaming phase cue (accent dot + wire word) ONLY on a streamed message (FB7.4)", () => {
    const { rerender } = render(
      <MessageItem item={msg({ itemId: "stream", globalOrdinal: 1, phase: "streaming" })} />,
    );
    expect(screen.getByTestId("message-phase").textContent).toBe("streaming");
    rerender(
      <MessageItem item={msg({ itemId: "done", globalOrdinal: 2, phase: "completed" })} />,
    );
    expect(screen.queryByTestId("message-phase")).toBeNull();
  });
});

describe("TerminalDiagnosticsDrawer — default off (R2/R7, §12.6)", () => {
  it("is closed by default: inert, hidden from the a11y tree, and renders no PTY frame", () => {
    render(<TerminalDiagnosticsDrawer focused={undefined} open={false} onClose={() => {}} />);
    const drawer = screen.getByTestId("terminal-diagnostics-drawer");
    expect(drawer.getAttribute("data-open")).toBe("false");
    expect(drawer.getAttribute("aria-hidden")).toBe("true");
    expect(drawer.hasAttribute("inert")).toBe(true);
    // The negative proof: no diagnostic content is mounted when closed.
    expect(screen.queryByTestId("terminal-diagnostics-frame")).toBeNull();
  });
});

// The checked-in DOM + interaction baseline at 10,000 tool-heavy items — a standing regression
// tripwire. The invariant under proof: the mounted DOM is virtualized by stable item and stays
// BOUNDED regardless of history depth, so the feed cannot degrade into a 10k-node tree.
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

describe("axe — no structural accessibility violations on the rendered grammar", () => {
  it("passes axe on a small feed + a closed diagnostics drawer", async () => {
    const { container } = render(
      <div>
        <ConversationTimeline
          items={[msg({ itemId: "a", globalOrdinal: 1 }), msg({ itemId: "b", globalOrdinal: 2, role: "user", lane: "operator", source: "cockpit-composer" })]}
          totalItems={2}
          hasOlder={false}
          busy={false}
          onLoadOlder={() => {}}
        />
        <TerminalDiagnosticsDrawer focused={undefined} open={false} onClose={() => {}} />
      </div>,
    );
    const results = await axe.run(container, {
      // jsdom has no layout engine, so skip the rules that require rendered geometry.
      rules: { "color-contrast": { enabled: false }, region: { enabled: false } },
    });
    expect(results.violations).toEqual([]);
  });
});

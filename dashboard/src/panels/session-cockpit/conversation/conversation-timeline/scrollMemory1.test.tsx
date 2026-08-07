import { act, fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ConversationTimeline } from "./ConversationTimeline";
import {
  installScrollMemoryGeometry,
  alignedTops,
  feedOf,
  pinGeometry,
} from "./scrollMemory.test-utils";

describe("ConversationTimeline — scroll memory (F-ac)", () => {
  installScrollMemoryGeometry();
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

});

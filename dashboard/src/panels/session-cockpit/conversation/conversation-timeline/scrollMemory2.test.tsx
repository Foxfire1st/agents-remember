import { act, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ConversationTimeline } from "./ConversationTimeline";
import "./test-utils";
import {
  installScrollMemoryGeometry,
  feedOf,
  pinGeometry,
} from "./scrollMemory.test-utils";

describe("ConversationTimeline — scroll memory (F-ac)", () => {
  installScrollMemoryGeometry();
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

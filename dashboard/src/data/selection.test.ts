import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { isIgnoredAnchor, readSelection, useSelectionCapture } from "./selection";

afterEach(() => {
  document.body.innerHTML = "";
});

function nodeInside(html: string): Node {
  const host = document.createElement("div");
  host.innerHTML = html;
  document.body.appendChild(host);
  // the deepest text node — what an anchorNode usually is
  const walker = document.createTreeWalker(host, NodeFilter.SHOW_TEXT);
  return walker.nextNode() ?? host;
}

function fakeSelection(text: string, anchor: Node, collapsed = false): Selection {
  return {
    isCollapsed: collapsed,
    rangeCount: collapsed ? 0 : 1,
    anchorNode: anchor,
    toString: () => text,
    getRangeAt: () =>
      ({ getBoundingClientRect: () => ({ left: 1, top: 2, width: 3, height: 4 }) }) as Range,
    removeAllRanges: () => {},
  } as unknown as Selection;
}

describe("isIgnoredAnchor (6f selection rules)", () => {
  it("ignores anchors inside the terminal host and editable fields", () => {
    expect(isIgnoredAnchor(nodeInside('<div data-testid="terminal-host"><span>out</span></div>'))).toBe(
      true,
    );
    expect(isIgnoredAnchor(nodeInside("<textarea>typed</textarea>"))).toBe(true);
    expect(isIgnoredAnchor(nodeInside('<div data-highlight-composer><span>x</span></div>'))).toBe(true);
  });

  it("allows anchors in ordinary cockpit content, and is null-safe", () => {
    expect(isIgnoredAnchor(nodeInside('<p class="detail">a finding</p>'))).toBe(false);
    expect(isIgnoredAnchor(null)).toBe(false);
  });
});

describe("readSelection", () => {
  it("returns the trimmed text + rect for a real selection", () => {
    const ctx = readSelection(fakeSelection("  a finding  ", nodeInside("<p>a finding</p>")));
    expect(ctx).toEqual({ text: "a finding", rect: { left: 1, top: 2, width: 3, height: 4 } });
  });

  it("returns null for a collapsed, empty, ignored, or absent selection", () => {
    expect(readSelection(null)).toBeNull();
    expect(readSelection(fakeSelection("x", nodeInside("<p>x</p>"), true))).toBeNull();
    expect(readSelection(fakeSelection("   ", nodeInside("<p>x</p>")))).toBeNull();
    expect(readSelection(fakeSelection("x", nodeInside("<textarea>x</textarea>")))).toBeNull();
  });
});

describe("useSelectionCapture", () => {
  it("captures the selection on mouse-up (deferred), and clears on demand", () => {
    vi.useFakeTimers();
    const selection = fakeSelection("a finding", nodeInside("<p>a finding</p>"));
    const getSelection = vi.spyOn(window, "getSelection").mockReturnValue(selection);
    const { result } = renderHook(() => useSelectionCapture());

    expect(result.current.selection).toBeNull();
    act(() => {
      document.dispatchEvent(new MouseEvent("mouseup", { bubbles: true }));
      vi.runAllTimers();
    });
    expect(result.current.selection?.text).toBe("a finding");

    act(() => result.current.clear());
    expect(result.current.selection).toBeNull();

    getSelection.mockRestore();
    vi.useRealTimers();
  });

  it("dismisses on a mouse-up with no live selection (click elsewhere clears it)", () => {
    vi.useFakeTimers();
    const live = fakeSelection("a finding", nodeInside("<p>a finding</p>"));
    const getSelection = vi.spyOn(window, "getSelection").mockReturnValue(live);
    const { result } = renderHook(() => useSelectionCapture());
    act(() => {
      document.dispatchEvent(new MouseEvent("mouseup", { bubbles: true }));
      vi.runAllTimers();
    });
    expect(result.current.selection?.text).toBe("a finding");

    // The user clicks elsewhere → the live selection collapses → the next mouse-up must clear it
    // (the old "only raise, never clear" handler left this to the popover and re-captured the range).
    getSelection.mockReturnValue(fakeSelection("", nodeInside("<p>x</p>"), true));
    act(() => {
      document.dispatchEvent(new MouseEvent("mouseup", { bubbles: true }));
      vi.runAllTimers();
    });
    expect(result.current.selection).toBeNull();

    getSelection.mockRestore();
    vi.useRealTimers();
  });

  it("collapses the live DOM selection on clear (so the trailing mouse-up can't re-capture)", () => {
    const removeAllRanges = vi.fn();
    const live = {
      ...fakeSelection("a finding", nodeInside("<p>a finding</p>")),
      removeAllRanges,
    } as unknown as Selection;
    const getSelection = vi.spyOn(window, "getSelection").mockReturnValue(live);
    const { result } = renderHook(() => useSelectionCapture());
    act(() => result.current.clear());
    expect(removeAllRanges).toHaveBeenCalledTimes(1);
    getSelection.mockRestore();
  });
});

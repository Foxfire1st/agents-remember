import { useCallback, useEffect, useState } from "react";

// Slice 6f: the cockpit text selection the highlight composer attaches to. A selection only ever
// *raises* the composer — nothing is sent until the operator explicitly Sends (the no-silent-action
// invariant). Captured on **mouse-up** (not mid-drag) and held as a **snapshot** so clicking into the
// composer — which collapses the live DOM selection — never dismisses it. Pure where it can be, so the
// rules unit-test without a DOM.

export interface SelectionContext {
  /** The trimmed selected text — the body of the context package. */
  text: string;
  /** The selection's viewport rect — the anchor the composer popover positions against. */
  rect: DOMRect;
}

// A selection is ignored when its anchor sits inside the terminal host (xterm owns its own selection),
// the composer itself (its content must not re-raise it), or an editable field.
const IGNORE_SELECTOR =
  '[data-testid="terminal-host"], [data-highlight-composer], input, textarea, [contenteditable="true"]';

/** True when `node` sits inside an ignored region (terminal host / composer / editable field). */
export function isIgnoredAnchor(node: Node | null | undefined): boolean {
  if (!node) return false;
  const el = node instanceof Element ? node : node.parentElement;
  return el?.closest(IGNORE_SELECTOR) != null;
}

/** Derive the composer's context from a `Selection`, or `null` when there is nothing to send. */
export function readSelection(selection: Selection | null): SelectionContext | null {
  if (!selection || selection.isCollapsed || selection.rangeCount === 0) return null;
  const text = selection.toString().trim();
  if (text.length === 0 || isIgnoredAnchor(selection.anchorNode)) return null;
  return { text, rect: selection.getRangeAt(0).getBoundingClientRect() };
}

/**
 * Capture the cockpit selection on **mouse-up** (so it never appears mid-drag) and hold it as a
 * snapshot the composer lives on — clicking into the composer collapses the live selection, but the
 * snapshot persists, so the composer stays open. Cleared explicitly via the returned `clear` (Send /
 * outside-click dismissal). A release inside the composer is interaction, not a new capture.
 */
export function useSelectionCapture(): { selection: SelectionContext | null; clear: () => void } {
  const [selection, setSelection] = useState<SelectionContext | null>(null);
  useEffect(() => {
    const onMouseUp = (event: MouseEvent) => {
      const target = event.target as Element | null;
      if (target?.closest?.("[data-highlight-composer]")) return;
      // Defer one tick so the browser finalizes (or collapses) the selection after the mouse-up
      // settles, then *mirror* it: a real selection raises the composer, an empty one (a click
      // elsewhere) dismisses it. Capturing only-when-present would let this same handler re-raise a
      // selection a dismiss just cleared — the "click-outside takes several tries" bug.
      window.setTimeout(() => setSelection(readSelection(window.getSelection())), 0);
    };
    document.addEventListener("mouseup", onMouseUp);
    return () => document.removeEventListener("mouseup", onMouseUp);
  }, []);
  const clear = useCallback(() => {
    setSelection(null);
    // Collapse the live DOM selection too: otherwise the trailing mouse-up re-reads the still-present
    // range and re-captures, undoing the dismiss (the multi-click / fast-click symptom).
    window.getSelection()?.removeAllRanges();
  }, []);
  return { selection, clear };
}

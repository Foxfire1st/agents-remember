// Keeps a part of the tray that is placed in the window (position: fixed) on the element it belongs
// to while it is in use: the target popup on its "to:" button, a chip's quick intents on the chip.
// Placed in the window, the part escapes every column, dock and list that clips its content; it
// stays where it is in the document, so the keyboard's order and the chip's hover hold.
import { useLayoutEffect } from 'react';

// `measure` places the part again. It runs on each render while `active` (the dock re-renders after
// the frame, the rail or the message box moved), when an `observed` element changes size, when the
// window resizes, and on a scroll anywhere on the page. `measure` and `observed` keep their identity
// between renders (useCallback).
export function useFollowAnchor(active: boolean, measure: () => void, observed: () => readonly (Element | null | undefined)[]): void {
  useLayoutEffect(() => {
    if (active) measure();
  });
  useLayoutEffect(() => {
    if (!active) return undefined;
    const observer = new ResizeObserver(() => measure());
    for (const node of observed()) if (node) observer.observe(node);
    window.addEventListener('resize', measure);
    window.addEventListener('scroll', measure, true);
    return () => {
      observer.disconnect();
      window.removeEventListener('resize', measure);
      window.removeEventListener('scroll', measure, true);
    };
  }, [active, measure, observed]);
}

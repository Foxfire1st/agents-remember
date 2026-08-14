// The hidden-layer visibility gate (Engine Room CPU fix). The cockpit keeps its heavy views
// mounted across tab switches (hidden via display:none, never unmounted — see Cockpit `engineLayer`
// and siblings), so the effects gate alone (useShouldAnimate) is not enough: GSAP fx loops, Motion
// pulses, and backdrop videos would keep burning frames on a view nobody is looking at. An element
// inside a display:none layer reports not-intersecting to an IntersectionObserver, so one observer
// per gated element gives each animation substrate the pause/resume contract the browser already
// gives a background tab — a kept-mounted hidden view then costs ~0.
//
// Defaults to VISIBLE when IntersectionObserver is unavailable (jsdom unit tests, where the gate is
// a no-op unless a test drives it) so existing render tests are unaffected.

import { useEffect, useState } from "react";

/** Reactive on-screen gate for one element: re-renders when the element starts/stops intersecting. */
export function useElementVisible(ref: React.RefObject<Element | null>): boolean {
  const [visible, setVisible] = useState(true);
  useEffect(() => {
    const element = ref.current;
    if (!element || typeof IntersectionObserver !== "function") return;
    const observer = new IntersectionObserver(([entry]) => {
      setVisible(entry?.isIntersecting ?? true);
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, [ref]);
  return visible;
}

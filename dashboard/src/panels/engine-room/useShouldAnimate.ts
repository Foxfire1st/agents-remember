// Honest-motion gate for the Engine Room (5f §2, §8.4). JS-driven GSAP/Motion are NOT frozen by
// the CSS `html[data-effects="off"]` switch, so every JS animation must consult this gate and skip
// straight to the transition's "After" state when EITHER the determinism flag (data-effects=off,
// set in main.tsx via ?effects=off / calm-cockpit) OR the OS `prefers-reduced-motion` is active.
// Generalizes the inline check the topology canvas already uses (topology/constel.ts).

import { useEffect, useState } from "react";

const REDUCE_QUERY = "(prefers-reduced-motion: reduce)";

/** Imperative gate for GSAP timelines / one-off tweens. False ⇒ render the After state, no tween. */
export function shouldAnimate(): boolean {
  if (typeof document === "undefined" || typeof window === "undefined") return false;
  if (document.documentElement.dataset.effects === "off") return false;
  return !window.matchMedia(REDUCE_QUERY).matches;
}

/** Reactive gate for components: re-renders when the OS setting or the data-effects flag flips. */
export function useShouldAnimate(): boolean {
  const [animate, setAnimate] = useState(shouldAnimate);
  useEffect(() => {
    const media = window.matchMedia(REDUCE_QUERY);
    const sync = () => setAnimate(shouldAnimate());
    media.addEventListener("change", sync);
    const observer = new MutationObserver(sync);
    observer.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["data-effects"],
    });
    sync();
    return () => {
      media.removeEventListener("change", sync);
      observer.disconnect();
    };
  }, []);
  return animate;
}

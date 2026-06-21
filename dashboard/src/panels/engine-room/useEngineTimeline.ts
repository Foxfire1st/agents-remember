// 05k — the engine-room canvas motion substrate (05f §8). GSAP owns the orchestrated, GSAP-native parts:
// the one-shot `strokeDashoffset` draw-ons (conduit clone arcs + landing flows), staged per phase, and the
// repeating fx that used to be CSS @keyframes (engine fault flicker, reindex pulse, warp-core surge,
// attention breath, terminal-STOP flash, the travelling flow packet). Motion owns opacity/transform/charge
// + enter/exit (in EnclosureCanvas); CSS is static. One `gsap.context` per enclosure, scoped to the SVG
// root, selecting elements by `data-draw` / `data-fx` attributes — so the component renders the structure
// and this hook animates it. Everything is gated by `useShouldAnimate`: under `data-effects=off` /
// `prefers-reduced-motion` no context is built, no ticker runs, and the elements rest at the end-state the
// render already set (so the Playwright/vitest snapshots stay deterministic).

import { useLayoutEffect } from "react";
import gsap from "gsap";

import { useShouldAnimate } from "./useShouldAnimate";
import type { EngineProcessNode } from "../../types/projection";

export type PhaseStage = "power-up" | "closeout" | "integrate" | "teardown" | "idle";

// Map the projection phase to the choreography stage the timeline branches on (the build-up B0→B5 power-up,
// the D1 closeout, the D2/D3/D4 landing, the D5/abandon teardown). `idle` (completed/nominal/unknown) plays
// no timeline — the constellation is at rest.
export function phaseStage(phase: string): PhaseStage {
  switch (phase) {
    case "worktree-started":
    case "code-worktree":
    case "contract-written":
    case "provider-setup":
      return "power-up";
    case "closeout-pending":
      return "closeout";
    case "integration-pending":
      return "integrate";
    case "cleanup-pending":
    case "abandoned":
      return "teardown";
    default:
      return "idle";
  }
}

// A signature of everything the GSAP layer keys on: which lanes are drawing (running edges + resolved
// landing flows) and which engines/couplers are in a repeating-fx state. The effect re-runs (revert →
// rebuild) whenever this changes, so a planned→running lane re-draws and a cleared fault stops flickering.
function fxSignature(node: EngineProcessNode): string {
  const draws = node.edges
    .filter((edge) => edge.state === "running")
    .map((edge) => edge.kind)
    .sort()
    .join(",");
  const landing = (node.landing ?? [])
    .map((ref) => `${ref.kind}:${ref.state}:${ref.factState}`)
    .sort()
    .join(",");
  const engines = node.providers
    .map((provider) => `${provider.role}:${provider.runtimeState}`)
    .sort()
    .join(",");
  return [node.phase, draws, landing, engines, node.seedFallback ? "reindex" : "", node.memoryMode].join("|");
}

const DRAW = { duration: 0.6, ease: "power2.out" } as const;

// Build the repeating fx loops on whatever data-fx elements the render produced. Each was a CSS keyframe;
// driven here by GSAP so CSS stays static. Alarm flickers stay ≤3 flashes/s (WCAG 2.3.1 — the master
// invariant). `gsap.context` (the caller's) reverts every tween + restores inline state on teardown.
function buildFx(q: gsap.utils.SelectorFunc): void {
  const fault = q("[data-fx='fault']"); // engine down → red flicker (≤3/s), isolated to that engine
  if (fault.length) gsap.fromTo(fault, { opacity: 1 }, { opacity: 0.3, duration: 0.34, repeat: -1, yoyo: true, ease: "steps(1)" });

  const reindex = q("[data-fx='reindex']"); // seedFallback → amber center-out pulse (a fallback, not a fault)
  if (reindex.length)
    gsap.fromTo(
      reindex,
      { scaleY: 0.25, transformOrigin: "center", opacity: 0.5 },
      { scaleY: 1, opacity: 0.9, duration: 1.5, repeat: -1, ease: "power1.out" },
    );

  const surge = q("[data-fx='surge']"); // warp-core surge: two hot bands born at the link, splitting out
  surge.forEach((band, i) => {
    const dir = band.getAttribute("data-dir") === "down" ? 1 : -1;
    gsap.fromTo(
      band,
      { attr: { y1: 342, y2: 342 }, opacity: 0.9 },
      { attr: { y1: 342 + dir * 26, y2: 342 + dir * 4 }, opacity: 0, duration: 1.6, repeat: -1, ease: "power2.out", delay: i * 0.1 },
    );
  });

  const breath = q("[data-fx='breath']"); // attention badge — gentle alarm-parity breathing (not the flicker)
  if (breath.length) gsap.fromTo(breath, { opacity: 1 }, { opacity: 0.45, duration: 0.65, repeat: -1, yoyo: true, ease: "sine.inOut" });

  const stop = q("[data-fx='stop']"); // terminal STOP — a brief flash ×3, then steady (repeat:5 = 3 on-beats)
  if (stop.length) gsap.fromTo(stop, { opacity: 0.35 }, { opacity: 1, duration: 0.25, repeat: 5, yoyo: true, ease: "steps(1)" });

  const packet = q("[data-fx='packet']"); // travelling flow packet — runs the offset-path of its conduit
  if (packet.length) gsap.fromTo(packet, { attr: { offsetDistance: "0%" } }, { attr: { offsetDistance: "100%" }, duration: 1.4, repeat: -1, ease: "none" });
}

// The orchestrated draw-on timeline + the fx loops, as one gsap.context per enclosure. The draw-ons sweep
// the active lanes (strokeDashoffset 100 → 0) staged in DOM order — GSAP owns stroke-dashoffset alone, so
// it never fights Motion (which owns opacity/transform). Re-runs (revert → rebuild) when the phase, the
// worktree group, or the active draw/fx set changes. Under !animate: nothing runs; the rendered end-state
// stands (paths rest fully drawn at offset 0, fx elements at their CSS end-state).
export function useEngineTimeline(
  rootRef: React.RefObject<SVGSVGElement | null>,
  node: EngineProcessNode,
): void {
  const animate = useShouldAnimate();
  const signature = fxSignature(node);
  useLayoutEffect(() => {
    const root = rootRef.current;
    if (!root || !animate) return;
    const q = gsap.utils.selector(root);
    const ctx = gsap.context(() => {
      const drawn = q("[data-draw='on']");
      if (drawn.length) gsap.fromTo(drawn, { strokeDashoffset: 100 }, { strokeDashoffset: 0, ...DRAW, stagger: 0.1 });
      buildFx(q);
    }, root);
    return () => ctx.revert();
    // signature folds in node.phase + worktreeGroup + the draw/fx state; listing it alone keeps the
    // dependency set honest (the effect re-runs exactly when the choreography inputs change).
  }, [rootRef, animate, signature, node.worktreeGroup]);
}

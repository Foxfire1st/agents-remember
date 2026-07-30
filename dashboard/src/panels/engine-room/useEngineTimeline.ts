// The engine-room canvas motion substrate. GSAP owns the orchestrated, GSAP-native
// parts: the DrawSVG draw-ons (conduit clone arcs + landing flows), drawn once per lane (a
// `data-drawn` guard stops them re-sweeping on each beat step), the MotionPath travelling flow packet
// (rides its conduit's `data-path`, replacing CSS offset-path), and the repeating fx that used to be
// CSS @keyframes (engine fault flicker, reindex pulse, warp-core surge, attention breath, terminal-STOP
// flash). Motion owns opacity/transform/charge
// + enter/exit (in EnclosureCanvas); CSS is static. One `gsap.context` per enclosure, scoped to the SVG
// root, selecting elements by `data-draw` / `data-fx` attributes — so the component renders the structure
// and this hook animates it. Everything is gated by `useShouldAnimate`: under `data-effects=off` /
// `prefers-reduced-motion` no context is built, no ticker runs, and the elements rest at the end-state the
// render already set (so the Playwright/vitest snapshots stay deterministic). A second, orthogonal gate
// pauses the built context while the canvas is off-screen (useElementVisible — the cockpit keeps the
// room mounted but display:none across tab switches, CPU fix): hidden ⇒ every repeating tween
// sleeps, re-shown ⇒ it resumes mid-beat, with no context rebuild either way.

import { useLayoutEffect, useRef } from "react";
import gsap from "gsap";
import { DrawSVGPlugin } from "gsap/DrawSVGPlugin";
import { MotionPathPlugin } from "gsap/MotionPathPlugin";

import { useElementVisible } from "./useElementVisible";
import { useShouldAnimate } from "./useShouldAnimate";
import type { EngineProcessNode } from "../../types/projection";

gsap.registerPlugin(DrawSVGPlugin, MotionPathPlugin);

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
  // A refused/failed/stale seed-or-integration lane drives the one-shot refused-conduit flash. It is
  // NOT a `running` lane (so `draws` misses it); fold it in so the flash re-arms when the refuse beat lands.
  const refused = node.edges
    .filter((edge) => edge.state === "refused" || edge.state === "failed" || edge.state === "stale")
    .map((edge) => `${edge.kind}:${edge.state}`)
    .sort()
    .join(",");
  return [node.phase, draws, landing, engines, refused, node.seedFallback ? "reindex" : "", node.memoryMode].join("|");
}

const DRAW = { duration: 0.6, ease: "power2.out" } as const;

// Build the repeating fx loops on whatever data-fx elements the render produced. Each was a CSS keyframe;
// driven here by GSAP so CSS stays static. Alarm flickers stay ≤3 flashes/s (WCAG 2.3.1 — the master
// invariant). `gsap.context` (the caller's) reverts every tween + restores inline state on teardown.
function buildFx(q: gsap.utils.SelectorFunc): void {
  const fault = q("[data-fx='fault']"); // a down engine breathes its red frame GENTLY (~1.7s sine), never a strobe
  if (fault.length) gsap.fromTo(fault, { opacity: 0.5 }, { opacity: 0.95, duration: 1.7, repeat: -1, yoyo: true, ease: "sine.inOut" });

  const reindex = q("[data-fx='reindex']"); // seedFallback → amber center-out pulse (a fallback, not a fault)
  if (reindex.length)
    gsap.fromTo(
      reindex,
      { scaleY: 0.25, transformOrigin: "center", opacity: 0.5 },
      { scaleY: 1, opacity: 0.9, duration: 1.5, repeat: -1, ease: "power1.out" },
    );

  const scan = q("[data-fx='scan']"); // pre-block verify sweep: a cyan ring expands + fades on the checked lane (transient)
  if (scan.length) {
    // The ring is a STROKED circle (scanRing: 2u cyan + a glow) and nothing in the tree declares a
    // vector-effect, so a uniform scale would scale the STROKE too — 2u → 2·52/6 ≈ 17.3u, i.e. a
    // thickening blob, not an expanding ring. Declaring the stroke non-scaling is what makes "a circle
    // scaled about its centre IS a radius tween" actually true here: the transform then moves the
    // geometry only. Set once (not per frame, so the perf win stands) and from here rather than the JSX
    // because it is an invariant OF THIS TWEEN — inert on the untransformed, effects-off ring.
    scan.forEach((ring) => ring.setAttribute("vector-effect", "non-scaling-stroke"));
    // scale about the ring's own centre, NOT attr r writes: r is rendered at 6 and the old tween took it
    // to 52, so scale 1 → 52/6 is the identical expansion — but transforms composite while per-frame
    // attribute writes force an SVG re-raster.
    gsap.fromTo(
      scan,
      { scale: 1, transformOrigin: "center", opacity: 0.9 },
      { scale: 52 / 6, opacity: 0, duration: 1.2, repeat: -1, ease: "power1.out" },
    );
  }

  const surge = q("[data-fx='surge']"); // warp-core surge: two hot bands born at the link, splitting out
  surge.forEach((band, i) => {
    // scaleY about the link point (svgOrigin x·342), NOT y1/y2 attribute writes — the top mutation
    // source in the CPU measurement (≈300 attr writes/s, forcing a per-frame re-raster). The
    // bands render at FULL geometry (EnclosureCanvas), so scaleY 0→1 replays the old trajectory
    // exactly: each end's y(t) interpolates linearly from the link point in both versions. At rest
    // (effects off) the bands stay invisible via the warpSurge class (opacity 0), unchanged.
    gsap.fromTo(
      band,
      {
        scaleY: 0,
        svgOrigin: `${band.getAttribute("x1") ?? 0} 342`,
        opacity: 0.9,
      },
      { scaleY: 1, opacity: 0, duration: 1.6, repeat: -1, ease: "power2.out", delay: i * 0.1 },
    );
  });

  const breath = q("[data-fx='breath']"); // attention badge — gentle alarm-parity breathing (not the flicker)
  if (breath.length) gsap.fromTo(breath, { opacity: 1 }, { opacity: 0.45, duration: 0.65, repeat: -1, yoyo: true, ease: "sine.inOut" });

  const stop = q("[data-fx='stop']"); // terminal STOP — a brief flash ×3, then steady (repeat:5 = 3 on-beats)
  if (stop.length) gsap.fromTo(stop, { opacity: 0.35 }, { opacity: 1, duration: 0.25, repeat: 5, yoyo: true, ease: "steps(1)" });

  // Refused-conduit flash (T9B red / T9C amber / T14C red) — a ONE-SHOT colour flash (repeat:0, NOT a
  // loop): cyan → white spark → the polarity colour → fade to 0 (the lane is refused/gone). Mirrors podstage
  // @keyframes `refused`/`refusedred` (.9s ease forwards; 12% spark / 26% recolour / 70% hold / 100% fade).
  // A single ~0.9s flash is well under WCAG 2.3.1's 3/s. Polarity is read off the element's data-polarity so
  // one tween serves both red (fault/conflict) and amber (reroute).
  q("[data-fx='refuse']").forEach((lane) => {
    const red = lane.getAttribute("data-polarity") === "red";
    const hot = red ? "oklch(0.66 0.2 25)" : "oklch(0.8 0.14 85)"; // alarm vs amber (oklch — GSAP-safe)
    gsap
      .timeline()
      .fromTo(lane, { opacity: 0.9, stroke: "oklch(0.85 0.13 200)" }, { opacity: 1, stroke: "oklch(0.98 0.04 200)", duration: 0.11, ease: "none" })
      .to(lane, { stroke: hot, duration: 0.13, ease: "none" }) // recolour to the polarity
      .to(lane, { opacity: 1, duration: 0.4 }) // hold the recoloured flash
      .to(lane, { opacity: 0, duration: 0.27, ease: "power1.in" }); // fade out — the STOP/gate carries on
  });

  // travelling flow packet — rides its conduit via MotionPath (the path string is on data-path;
  // replacing CSS offset-path). GSAP owns the packet transform; the dot only exists while animate.
  q("[data-fx='packet']").forEach((dot) => {
    const path = dot.getAttribute("data-path");
    if (path) gsap.to(dot, { duration: 1.4, repeat: -1, ease: "none", motionPath: { path, alignOrigin: [0.5, 0.5] } });
  });
}

// The orchestrated draw-on timeline + the fx loops, as one gsap.context per enclosure. DrawSVG draws each
// active lane once (the `data-drawn` guard skips lanes already drawn, so a beat step never re-sweeps
// a drawn arc); MotionPath rides the packet. GSAP owns the stroke geometry + the packet transform; Motion
// owns node opacity/transform. Re-runs (revert → rebuild) when the phase, the worktree group, or the
// active draw/fx set changes. Under !animate: nothing runs; the rendered end-state stands (running
// conduits rest solid = drawn, the packet is not rendered, fx elements at their CSS end-state).
export function useEngineTimeline(
  rootRef: React.RefObject<SVGSVGElement | null>,
  node: EngineProcessNode,
  fxRootRef?: React.RefObject<SVGSVGElement | null>,
): void {
  const animate = useShouldAnimate();
  const visible = useElementVisible(rootRef);
  const signature = fxSignature(node);
  const ctxRef = useRef<gsap.Context | null>(null);
  useLayoutEffect(() => {
    const root = rootRef.current;
    if (!root || !animate) return;
    const baseSelector = gsap.utils.selector(root);
    const fxSelector = fxRootRef?.current
      ? gsap.utils.selector(fxRootRef.current)
      : null;
    const q = ((selector: string) => [
      ...baseSelector(selector),
      ...(fxSelector?.(selector) ?? []),
    ]) as gsap.utils.SelectorFunc;

    // RETRACT — tail-to-tip erase on departing lanes. ctx.revert() from the PREVIOUS cycle
    // already ran (it's the cleanup) and stripped DrawSVG's inline dash, so departing lanes are back
    // at their CSS solid rest. Animate them to drawSVG "100% 100%" (visible segment = nothing) then
    // clearProps so the lane settles back at its CSS end-state. Stamp cleared immediately so a fast
    // re-activation picks them up as `fresh` and draws correctly (no stale stamp blocking the re-draw).
    const toRetract = q("[data-drawn]").filter((el) => el.getAttribute("data-draw") !== "on");
    if (toRetract.length) {
      toRetract.forEach((el) => el.removeAttribute("data-drawn"));
      // Lock the stroke at cyan before the retract tween so it stays cyan throughout the erase —
      // the CSS class has already changed (running→complete = amber) by the time this runs, so without
      // this set the retract would play in amber instead of the expected cyan.
      gsap.set(toRetract, { stroke: "oklch(0.85 0.13 200)", filter: "drop-shadow(0 0 3px oklch(0.85 0.13 200))" });
      gsap.to(toRetract, {
        drawSVG: "100% 100%",
        duration: 0.45,
        ease: "power2.in",
        overwrite: true,
        onComplete: () =>
          toRetract.forEach((el) => gsap.set(el, { clearProps: "strokeDashoffset,strokeDasharray,stroke,filter" })),
      });
    }

    const ctx = gsap.context(() => {
      // Draw each lane ONCE per activation. Stamps survive ctx.revert() (DOM attribute, not inline
      // style), so a still-`on` lane is skipped across beat steps and never re-sweeps. Retract (above)
      // cleared stamps on departing lanes before this context runs.
      const fresh = q("[data-draw='on']").filter((el) => !el.getAttribute("data-drawn"));
      if (fresh.length) {
        // Stamp on COMPLETE, not immediately. StrictMode double-invokes this effect (run → revert →
        // run); stamping eagerly made run-1 stamp, the revert kill the draw, and run-2 skip (already
        // stamped) — draw-on never animated. Stamping on complete lets the surviving mount actually draw.
        gsap.from(fresh, {
          drawSVG: 0,
          ...DRAW,
          stagger: 0.1,
          overwrite: true,
          onComplete: () => fresh.forEach((el) => el.setAttribute("data-drawn", "1")),
        });
      }
      buildFx(q);
    }, root);
    ctxRef.current = ctx;
    return () => {
      ctxRef.current = null;
      ctx.revert();
    };
    // signature folds in node.phase + worktreeGroup + the draw/fx state; listing it alone keeps the
    // dependency set honest (the effect re-runs exactly when the choreography inputs change).
  }, [rootRef, fxRootRef, animate, signature, node.worktreeGroup]);
  // Off-screen pause: while the room's cockpit layer is display:none the canvas doesn't
  // intersect — pause every tween the context recorded (the gsap-idiomatic scoped ticker sleep; gsap
  // 3.15's Context has no paused() of its own) and resume on re-show, WITHOUT a revert/rebuild so the
  // choreography picks up mid-beat. Declared after the build effect with a superset of its deps, so a
  // context rebuilt while hidden is (re)paused in the same commit.
  useLayoutEffect(() => {
    if (!ctxRef.current) return;
    for (const tween of ctxRef.current.getTweens() as gsap.core.Tween[]) tween.paused(!visible);
  }, [rootRef, fxRootRef, animate, signature, node.worktreeGroup, visible]);
}

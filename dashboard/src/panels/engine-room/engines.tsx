// Branch nodes and podracer engine gauges: the two world-building elements that carry the
// materialise/de-materialise + boot-charge animation contracts. Motion owns opacity/transform;
// CSS owns fills; GSAP owns the draw-ons.
import { useEffect, useRef, useState } from "react";
import { motion } from "motion/react";

import { useShouldAnimate } from "./useShouldAnimate";
import {
  engineCharge,
  engineDiv,
  engineGaugeLabel,
  engineGaugeOut,
  enginePetal,
  engineReindexCharge,
  engineReindexOut,
  engineSpine,
  prunedNode,
  svgNodeBox,
  svgNodeLabel,
  svgNodeMeta,
  svgNodeTitle,
} from "./styles";
import {
  ENGINE,
  NODE_H,
  branchEnter,
  chargeMotion,
  truncate,
} from "./geometry";
import type { RuntimeState } from "./geometry";
import type { CommitRefNode } from "../../types/projection";

export function BranchNode({ pos, label, refNode, landingIn = false, detaching = false, pruned = false }: {
  pos: { x: number; y: number; w: number };
  label: string;
  refNode: CommitRefNode;
  landingIn?: boolean;
  detaching?: boolean;
  pruned?: boolean;
}) {
  const animate = useShouldAnimate();
  const enter = branchEnter(refNode.factState);
  // a DETACHING worktree (cleanup de-materialise) drifts OUT to the right as it fades — not in from main
  // (which is the build-up branch-copy direction). Same fade; only the slide direction flips.
  const dx = detaching && enter.opacity === 0 ? 64 : enter.dx;
  const cx = pos.x + pos.w / 2;
  const branch = refNode.branch ?? "—";
  // truncate to the box width (~7.4px/char at 14px); the full string is in the <title> (hover).
  const maxChars = Math.max(8, Math.floor((pos.w - 20) / 7.4));
  const flags = `${refNode.dirty ? " · dirty" : ""}${refNode.behindSource ? ` · ${refNode.behindSource} behind` : ""}`;
  const full = `${label}: ${branch}${refNode.commit ? ` @ ${refNode.commit}` : ""}${flags}`;
  // Motion owns the materialise/de-materialise (opacity + slide) + the landing-tier mount fade+lift;
  // GSAP/CSS never touch this group. A detaching worktree node drifts out on a slight delay so the
  // de-materialise reads engines → nodes → border. Under !animate it mounts at the end-state (initial=false),
  // so the count/presence tests stay synchronous; `landingIn` enters from above; `exit` lets a feat-tier
  // node leave (inside AnimatePresence) instead of blinking.
  const detachDelay = animate && detaching && enter.opacity === 0 ? 0.4 : 0;
  return (
    <motion.g
      data-testid="branch-node"
      data-fact={refNode.factState}
      initial={animate ? { opacity: landingIn ? 0 : enter.opacity, x: dx, y: landingIn ? -7 : 0 } : false}
      animate={{ opacity: enter.opacity, x: dx, y: 0 }}
      exit={animate ? { opacity: 0, y: -7 } : { opacity: 0 }}
      transition={{ duration: animate ? 0.5 : 0, ease: [0.2, 0.7, 0.2, 1], delay: detachDelay }}
    >
      <title>{full}</title>
      {/* T1B — a stale base node (local main behind upstream) reads DORMANT/pruned over its fact-state box.
          NB: the local `cx` here is the node centre-x (a number) — it shadows Panda's `cx`, so combine by hand. */}
      <rect
        className={pruned ? `${svgNodeBox({ factState: refNode.factState })} ${prunedNode}` : svgNodeBox({ factState: refNode.factState })}
        data-pruned={pruned || undefined}
        x={pos.x}
        y={pos.y}
        width={pos.w}
        height={NODE_H}
        rx={8}
      />
      <text className={svgNodeLabel} x={cx} y={pos.y + 17} textAnchor="middle">{label}</text>
      <text className={svgNodeTitle} x={cx} y={pos.y + 36} textAnchor="middle">{truncate(branch, maxChars)}</text>
      {refNode.commit ? (
        <text className={svgNodeMeta} x={cx} y={pos.y + 52} textAnchor="middle">
          @{refNode.commit.slice(0, 8)}
          {flags}
        </text>
      ) : null}
    </motion.g>
  );
}

// Spec colour values (panda.config.ts tokens, mirrored from the design spec HTML).
// The charge rect's animated end-state. Motion owns ONLY scaleY + opacity — fill is intentionally
// absent so Motion never writes a fill inline style. CSS class (engineCharge CVA) owns fill color for
// every state. This prevents Motion's oklch inline fill from blocking CSS class color changes on
// subsequent scenario cycles (Motion 12 cannot interpolate oklch; a stale inline fill overrides CSS).

export function EngineGauge({ at, label, runtime, reindex, present = true }: {
  at: { x: number; y: number };
  label: string;
  runtime: RuntimeState;
  reindex?: boolean;
  present?: boolean;
}) {
  const animate = useShouldAnimate();
  const state = reindex ? "reindex" : runtime;
  // Boot flash: one-shot, fires when this engine crosses indexing→nominal. It only flags the Motion
  // opacity pulse on the charge rect (below) — fill is owned by the CSS class, never by the flash —
  // so the flash can never strand the fill colour. `booting` persists across re-renders (a parent
  // re-render must not cut it short); it is cleared by the rect's Motion onAnimationComplete, with the
  // timer below as a guaranteed backstop.
  const prevRuntime = useRef(runtime);
  const [booting, setBooting] = useState(false);
  useEffect(() => {
    const prev = prevRuntime.current;
    prevRuntime.current = runtime;
    if (prev !== "nominal" && runtime === "nominal" && animate) {
      setBooting(true);
    }
  }, [runtime, animate]);
  // Backstop teardown: drop booting shortly after the pulse's run even if onAnimationComplete is missed.
  // Keyed on `booting` alone — frame advances never clear this timer, so the flash always ends, on every
  // cycle. Belt-and-suspenders: a stuck flag is already harmless (fill stays class-owned), this keeps
  // the opacity honest too.
  useEffect(() => {
    if (!booting) return;
    const timer = window.setTimeout(() => setBooting(false), 1000);
    return () => window.clearTimeout(timer);
  }, [booting]);
  // `present` is the build-up gate: a worktree engine only materialises once the provider runtime deploys
  // (B3); until then it is faded out (the left-world engines are always present). Motion owns this opacity
  // (the SVG `transform` position attribute is untouched); on power-down the same gate eases it back out.
  // A `down` engine raises the GSAP fault flicker via data-fx='fault' (≤3/s), isolated to this engine; a
  // reindexing engine pulses amber via data-fx='reindex'. Under !animate both rest at the rendered state.
  return (
    <motion.g
      transform={`translate(${at.x},${at.y})`}
      data-testid="engine-gauge"
      data-runtime={state}
      role="img"
      aria-label={`${label} engine ${state}`}
      initial={animate ? { opacity: present ? 1 : 0 } : false}
      animate={{ opacity: present ? 1 : 0 }}
      transition={{ duration: animate ? 0.45 : 0 }}
    >
      <rect
        className={reindex ? engineReindexOut : engineGaugeOut({ runtimeState: runtime })}
        data-fx={!reindex && runtime === "down" ? "fault" : undefined}
        x={0}
        y={0}
        width={ENGINE.w}
        height={ENGINE.h}
        rx={5}
      />
      {reindex ? (
        <rect
          className={engineReindexCharge}
          visibility={animate ? "hidden" : undefined}
          x={2}
          y={2}
          width={ENGINE.w - 4}
          height={ENGINE.h - 4}
          rx={3}
        />
      ) : (
        <motion.rect
          className={engineCharge({ runtimeState: runtime })}
          x={2}
          y={2}
          width={ENGINE.w - 4}
          height={ENGINE.h - 4}
          rx={3}
          initial={animate ? { scaleY: 0, opacity: 0 } : false}
          // Fill is ALWAYS owned by the engineCharge class (cyan `indexing` → mint `nominal`), so it is
          // correct on every scenario cycle and can never get stuck. Motion owns ONLY scaleY (the
          // center-out boot-fill) + opacity. The indexing→nominal "powerup" is a one-shot Motion opacity
          // pulse (0.85 → 1 → 0.55): a brightness surge as the engine goes green, NOT a fill animation —
          // the instant cyan→mint comes from the class flip (Motion 12 cannot interpolate oklch fill). A
          // CSS @keyframes can't live here: Motion's per-frame scaleY style writes restart it every frame
          // so it never completes, and its `forwards` fill-lock then permanently overrode the class —
          // that was the "second cycle stays green / never goes cyan" bug. booting only shapes the pulse;
          // if it ever fails to reset, the rect just holds the pulse's final 0.55 (already the nominal
          // rest opacity) with class-correct fill, so nothing breaks.
          animate={booting ? { scaleY: 1, opacity: [0.85, 1, 0.55] } : chargeMotion(runtime)}
          transition={
            booting
              ? { duration: animate ? 0.7 : 0, times: [0, 0.35, 1], ease: "easeOut" }
              : { duration: animate ? 0.6 : 0, ease: [0.4, 0, 0.2, 1] }
          }
          // Motion's own lifecycle callback (reliable, unlike the native animationend) ends the flash.
          onAnimationComplete={() => setBooting(false)}
        />
      )}
      {[14, 26, 38, 50, 62, 74, 86].map((y) => (
        <line className={engineDiv} key={y} x1={0} y1={y} x2={ENGINE.w} y2={y} />
      ))}
      {/* podstage .e-spine + .e-petal: a faint centre spine + fanned flank petals (runtime-coloured). */}
      <line className={engineSpine} x1={ENGINE.w / 2} y1={4} x2={ENGINE.w / 2} y2={ENGINE.h - 4} />
      {[
        // left flank + right flank mirror each other across the gauge centre (both fan toward the gauge)
        [-8, 26, -2, 22], [-8, 48, -2, 48], [-8, 70, -2, 74],
        [ENGINE.w + 2, 22, ENGINE.w + 8, 26], [ENGINE.w + 2, 48, ENGINE.w + 8, 48], [ENGINE.w + 2, 74, ENGINE.w + 8, 70],
      ].map(([x1, y1, x2, y2], i) => (
        <line className={enginePetal({ runtimeState: runtime })} key={i} x1={x1} y1={y1} x2={x2} y2={y2} />
      ))}
      <text className={engineGaugeLabel} x={ENGINE.w / 2} y={ENGINE.h + 18} textAnchor="middle">{label}</text>
    </motion.g>
  );
}

// A commit short-sha for the ledger-coupler label (the two linked hashes it stands for).

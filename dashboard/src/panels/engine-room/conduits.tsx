// Flow conduits and the directional landing flows: the moving lanes of the canvas. GSAP owns the
// draw-on strokes; Motion owns group opacity; CSS carries state colours.
import { motion } from "motion/react";

import { useShouldAnimate } from "./useShouldAnimate";
import { cx } from "../../../styled-system/css";
import {
  flowConduit,
  flowPacket,
  ghostedLane,
} from "./styles";
import {
  COL_FEAT_CX,
  COL_MAIN_CX,
  EDGE_GEOM,
  conduitPathD,
  conduitState,
} from "./geometry";
import type { FlowState } from "./geometry";
import type { EngineProcessEdge, LandingRefNode } from "../../types/projection";

function isReplayLane(edge: EngineProcessEdge, strategy: string | undefined): boolean {
  return (edge.kind === "integration" || edge.kind === "integration-mem") && strategy === "replay";
}

function isCloneArc(edge: EngineProcessEdge): boolean {
  return edge.kind === "cgc-seed" || edge.kind === "grepai-clone";
}

function conduitOpacity(edge: EngineProcessEdge, retiring: boolean, cloneArc: boolean): number {
  return retiring
    ? 0
    : cloneArc
      ? (edge.state === "running" ? 1 : 0)
      : edge.state === "planned"
        ? 0
        : 1;
}

function conduitTitle(edge: EngineProcessEdge, replay: boolean): string {
  return `${edge.label}${edge.detail ? ` — ${edge.detail}` : ""}${
    replay ? " — replay (rebased onto moved main)" : ""
  }`;
}

function conduitTransitionDelay(animate: boolean, cloneArc: boolean, opacity: number): number {
  return animate && cloneArc && opacity === 0 ? 0.45 : 0;
}

function conduitDraw(edge: EngineProcessEdge): "on" | undefined {
  return edge.state === "running" ? "on" : undefined;
}

function conduitMarker(edge: EngineProcessEdge): string | undefined {
  return edge.state === "running" ? "url(#er-chev)" : undefined;
}

function conduitPacketVisible(edge: EngineProcessEdge, animate: boolean): boolean {
  return edge.state === "running" && animate;
}

function ghostLaneClass(ghosted: boolean): string {
  return ghosted ? ghostedLane : "";
}

function initialConduitOpacity(animate: boolean, opacity: number): boolean | { opacity: number } {
  return animate ? { opacity } : false;
}

export function Conduit({ edge, strategy, retiring = false, ghosted = false }: { edge: EngineProcessEdge; strategy?: string; retiring?: boolean; ghosted?: boolean }) {
  // The conduit draw-on (strokeDashoffset 100 → 0) is owned by the GSAP timeline (useEngineTimeline),
  // which selects every running lane via [data-draw='on'] and staggers them. Motion owns this
  // group's opacity; CSS is static. A planned → running cycle re-runs the hook (its signature folds in the
  // running edges) so the lane re-draws, while Motion fades it in. Under !animate nothing runs and the path
  // rests fully drawn (offset 0, the rendered end-state) — the presence tests stay synchronous.
  const animate = useShouldAnimate();
  const geom = EDGE_GEOM[edge.kind];
  if (!geom) return null;
  const [x1, y1, x2, y2] = geom;
  // The integration return lane (worktree closeout commits → feat/source branch in the gap) is a
  // plain straight connection like every other settled lane. `replay` (commits rebased onto a moved main)
  // vs a clean `ff-only` is NOT encodable as a line shape: a bent/bowed return lane read as an unexplained
  // triangle, never "around parallel work". The replay fact is recorded as data-strategy (for a future text
  // chip / glyph in the panel) but the path itself stays straight.
  const isReplay = isReplayLane(edge, strategy);
  // The provider-clone arrows sweep across the whole stage from the official engine to the worktree
  // engine — CGC bows OVER the top, GrepAI UNDER the bottom (the "copies + rewrites index" / "clones
  // vector DB" beat). Transient: shown only while running, gone at idle (see the opacity below).
  const cloneArc = isCloneArc(edge);
  const d = conduitPathD(edge) ?? `M${x1} ${y1} L ${x2} ${y2}`; // straight lane, or the clone BOW (shared helper)
  // At cleanup the worktree side de-materialises; fade every worktree conduit to 0 so the yellow
  // connector lines retract with the enclosure instead of dangling to the disposed nodes (the official line
  // keeps its own `officialWire` conduits, which are not in `node.edges`).
  const opacity = conduitOpacity(edge, retiring, cloneArc);
  return (
    <motion.g
      data-testid="conduit"
      data-kind={edge.kind}
      data-state={edge.state}
      data-strategy={isReplay ? "replay" : undefined}
      data-ghosted={ghosted || undefined}
      // a `planned` lane is hidden during the main-only B0; the transient clone arrows show ONLY while the
      // clone is running (gone at idle); every other lane fades in as it activates. Motion eases the opacity
      // (instant under !animate, where it mounts at the end-state).
      // RETRACT VISIBILITY — clone arcs fade their GROUP to 0 when done; delay that fade so the GSAP
      // tail-to-tip retract (0.45s) completes before the group turns transparent (mirrors spec's 0.32s
      // opacity delay on .flow-g.off: retract runs first, then opacity clears).
      initial={initialConduitOpacity(animate, opacity)}
      animate={{ opacity }}
      transition={{ duration: animate ? 0.45 : 0, delay: conduitTransitionDelay(animate, cloneArc, opacity) }}
    >
      <path
        // A gated memory lane is GHOSTED (dim + desaturate) on the inner <path>, NOT the motion.g, so
        // the ghost never fights Motion's group opacity (a className opacity loses on a static frame).
        className={cx(flowConduit({ state: conduitState(edge.state) }), ghostLaneClass(ghosted))}
        d={d}
        // GSAP DrawSVG draws this on when it goes running (data-draw='on'); the running conduit has no
        // CSS dash (solid), so DrawSVG owns the stroke reveal. No pathLength: DrawSVG measures real length.
        data-draw={conduitDraw(edge)}
        // arrow tip only on an ACTION (running flow); a nominal/static line is just a connection
        markerEnd={conduitMarker(edge)}
      >
        <title>{conduitTitle(edge, isReplay)}</title>
      </path>
      {conduitPacketVisible(edge, animate) ? (
        <circle
          className={flowPacket}
          r={4}
          data-testid="conduit-packet"
          data-fx="packet"
          data-path={d}
        />
      ) : null}
    </motion.g>
  );
}


function LandingFlow({ d, state, kind }: { d: string; state: FlowState; kind: string }) {
  const animate = useShouldAnimate();
  const active = state === "active";
  const visible = state !== "hidden";
  // Motion owns the opacity (visible vs hidden). The active flow is cyan `running` (GSAP DrawSVG draws it on
  // via [data-draw='on'] + a travelling [data-fx='packet'] dot); a settled flow is a plain amber `nominal`
  // line — no chevron, no dot. Under !animate it rests at this end-state.
  return (
    <motion.g
      data-testid="landing-flow"
      data-kind={kind}
      data-flow-state={state}
      initial={animate ? { opacity: visible ? 1 : 0 } : false}
      animate={{ opacity: visible ? 1 : 0 }}
      transition={{ duration: animate ? 0.45 : 0 }}
    >
      <path
        className={flowConduit({ state: active ? "running" : "nominal" })}
        d={d}
        data-draw={active ? "on" : undefined}
        markerEnd={active ? "url(#er-chev)" : undefined}
      />
      {active ? <circle className={flowPacket} r={4} data-testid="landing-packet" data-fx="packet" data-path={d} /> : null}
    </motion.g>
  );
}

// Which single flow is the ACTIVE transaction, by the landing[] ref progression: push (feat→origin/feat) is
// active while pushing / PR-open; once the PR merges it settles and pull (origin/main→main) is active; once
// memory carries over (origin-mem-main pushed) pull settles and the carryover flows are active.
function landingRefResolved(refs: LandingRefNode[], kind: string): boolean {
  const ref = refs.find((r) => r.kind === kind);
  return ref !== undefined && ref.factState === "observed" && ref.state !== "planned";
}

function landingPrMerged(refs: LandingRefNode[]): boolean {
  const pr = refs.find((r) => r.kind === "pr");
  return pr !== undefined && pr.factState === "observed" && pr.state === "merged";
}

function landingMemPushed(refs: LandingRefNode[]): boolean {
  const memory = refs.find((r) => r.kind === "origin-mem-main");
  return memory !== undefined && memory.factState === "observed" && memory.state === "pushed";
}

function landingFlowState(refs: LandingRefNode[], kind: string): FlowState {
  if (kind === "push") {
    return !landingRefResolved(refs, "origin-feat") ? "hidden" : landingPrMerged(refs) ? "settled" : "active";
  }
  if (kind === "pull") return !landingPrMerged(refs) ? "hidden" : landingMemPushed(refs) ? "settled" : "active";
  return landingMemPushed(refs) ? "active" : "hidden"; // carry + push-mem: the carryover frontier
}

export function LandingFlows({ refs }: { refs: LandingRefNode[] }) {
  return (
    <g data-testid="landing-flows" aria-hidden="true">
      {/* push: the feat/fix source pushes UP to origin/feat (both on the feat column centre → vertical) */}
      <LandingFlow kind="push" state={landingFlowState(refs, "push")} d={`M${COL_FEAT_CX} 250 L ${COL_FEAT_CX} 100`} />
      {/* pull: merged origin/main pulls DOWN onto local main (both on the main column centre → vertical) */}
      <LandingFlow kind="pull" state={landingFlowState(refs, "pull")} d={`M${COL_MAIN_CX} 100 L ${COL_MAIN_CX} 250`} />
      {/* carry: the feat memory carries over LEFT into local main memory (T16): feat left edge → main right edge */}
      <LandingFlow kind="carry" state={landingFlowState(refs, "carry")} d={`M${COL_FEAT_CX - 68} 403 L ${COL_MAIN_CX + 90} 403`} />
      {/* push-mem: local main memory pushes DOWN to origin/mem-main (both on the main centre → vertical) */}
      <LandingFlow kind="push-mem" state={landingFlowState(refs, "push-mem")} d={`M${COL_MAIN_CX} 434 L ${COL_MAIN_CX} 524`} />
    </g>
  );
}

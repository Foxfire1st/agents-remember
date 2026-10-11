// Branch nodes: the world-building element carrying the materialise/de-materialise contracts.
// Motion owns opacity/transform; CSS owns fills; GSAP owns the draw-ons.
import { motion } from "motion/react";

import { useShouldAnimate } from "./useShouldAnimate";
import {
  prunedNode,
  svgNodeBox,
  svgNodeLabel,
  svgNodeMeta,
  svgNodeTitle,
} from "./styles";
import {
  NODE_H,
  branchEnter,
  truncate,
} from "./geometry";
import type { CommitRefNode } from "../../types/projection";

function branchFlags(refNode: CommitRefNode): string {
  return `${refNode.dirty ? " · dirty" : ""}${
    refNode.behindSource ? ` · ${refNode.behindSource} behind` : ""
  }`;
}

function branchFullLabel(
  label: string,
  branch: string,
  refNode: CommitRefNode,
  flags: string,
): string {
  return `${label}: ${branch}${refNode.commit ? ` @ ${refNode.commit}` : ""}${flags}`;
}

function branchEnterTransform(
  animate: boolean,
  landingIn: boolean,
  enter: ReturnType<typeof branchEnter>,
): boolean | { opacity: number; x: number; y: number } {
  return animate ? { opacity: landingIn ? 0 : enter.opacity, x: enter.dx, y: landingIn ? -7 : 0 } : false;
}

function branchExit(animate: boolean): { opacity: number; y?: number } {
  return animate ? { opacity: 0, y: -7 } : { opacity: 0 };
}

function branchSlide(detaching: boolean, opacity: number, dx: number): number {
  return detaching && opacity === 0 ? 64 : dx;
}

function branchText(branch: string | undefined): string {
  return branch ?? "—";
}

function branchDetachDelay(animate: boolean, detaching: boolean, opacity: number): number {
  return animate && detaching && opacity === 0 ? 0.4 : 0;
}

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
  const dx = branchSlide(detaching, enter.opacity, enter.dx);
  const cx = pos.x + pos.w / 2;
  const branch = branchText(refNode.branch);
  // truncate to the box width (~7.4px/char at 14px); the full string is in the <title> (hover).
  const maxChars = Math.max(8, Math.floor((pos.w - 20) / 7.4));
  const flags = branchFlags(refNode);
  const full = branchFullLabel(label, branch, refNode, flags);
  // Motion owns the materialise/de-materialise (opacity + slide) + the landing-tier mount fade+lift;
  // GSAP/CSS never touch this group. A detaching worktree node drifts out on a slight delay so the
  // de-materialise reads engines → nodes → border. Under !animate it mounts at the end-state (initial=false),
  // so the count/presence tests stay synchronous; `landingIn` enters from above; `exit` lets a feat-tier
  // node leave (inside AnimatePresence) instead of blinking.
  const detachDelay = branchDetachDelay(animate, detaching, enter.opacity);
  return (
    <motion.g
      data-testid="branch-node"
      data-fact={refNode.factState}
      initial={branchEnterTransform(animate, landingIn, enter)}
      animate={{ opacity: enter.opacity, x: dx, y: 0 }}
      exit={branchExit(animate)}
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

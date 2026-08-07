// The remote/landing dock: origin chips, the PR merge arrow, and the strip that orders them as the
// governed landing flow (feat → PR → main, memory after).
import { motion } from "motion/react";

import { useShouldAnimate } from "./useShouldAnimate";
import {
  prBadge,
  prBadgeLabel,
  remoteChip,
  remoteChipLabel,
  remoteChipState,
  remoteStripHeader,
} from "./styles";
import {
  PR_CX,
  RBOX_H,
  RBOX_W,
  REMOTE_POS,
  remoteStateWord,
  remoteTitle,
  remoteTone,
  truncate,
} from "./geometry";
import type { LandingRefNode } from "../../types/projection";

export function RemoteChip({ refNode }: { refNode: LandingRefNode }) {
  const animate = useShouldAnimate();
  const pos = REMOTE_POS[refNode.kind];
  if (!pos) return null;
  const tone = remoteTone(refNode);
  return (
    <motion.g
      data-testid="remote-chip"
      data-kind={refNode.kind}
      data-tone={tone}
      initial={animate ? { opacity: 0, y: -7 } : false}
      animate={{ opacity: 1, y: 0 }}
      exit={animate ? { opacity: 0, y: -7 } : { opacity: 0 }}
      transition={{ duration: animate ? 0.5 : 0, ease: [0.2, 0.7, 0.2, 1] }}
    >
      <title>{remoteTitle(refNode)}</title>
      <rect className={remoteChip({ tone })} x={pos.x} y={pos.y} width={RBOX_W} height={RBOX_H} rx={7} />
      <text className={remoteChipLabel({ tone })} x={pos.x + RBOX_W / 2} y={pos.y + 18} textAnchor="middle">
        {truncate(refNode.label, 18)}
      </text>
      <text className={remoteChipState({ tone })} x={pos.x + RBOX_W / 2} y={pos.y + 33} textAnchor="middle">
        {truncate(remoteStateWord(refNode), 18)}
      </text>
    </motion.g>
  );
}

// PR ▸ merge — the mockup renders this as a leftward merge arrow in the gap between origin/feat and
// origin/main (the merge direction: origin/feat merges into origin/main), with the PR id + state on a
// line beneath the dock. (prBadge's stroke carries the open=amber / merged=mint colour onto the arrow.)
export function PrBadge({ refNode }: { refNode: LandingRefNode }) {
  const animate = useShouldAnimate();
  const state = refNode.factState === "stale" ? "stale" : refNode.state === "merged" ? "merged" : "open";
  const sub = state === "stale" ? "stale" : state === "merged" ? "merged" : refNode.state;
  const top = REMOTE_POS["origin-main"].y;
  const cy = top + RBOX_H / 2;
  return (
    <motion.g
      data-testid="pr-badge"
      data-state={state}
      initial={animate ? { opacity: 0, y: -7 } : false}
      animate={{ opacity: 1, y: 0 }}
      exit={animate ? { opacity: 0, y: -7 } : { opacity: 0 }}
      transition={{ duration: animate ? 0.5 : 0, ease: [0.2, 0.7, 0.2, 1] }}
    >
      <title>{remoteTitle(refNode)}</title>
      <line className={prBadge({ state })} x1={PR_CX + 11} y1={cy} x2={PR_CX - 9} y2={cy} strokeWidth={2.6} markerEnd="url(#er-chev)" />
      <text className={prBadgeLabel({ state })} x={PR_CX} y={top + RBOX_H + 15} textAnchor="middle">
        {truncate(refNode.label, 14)} · {sub}
      </text>
    </motion.g>
  );
}

// The remote/landing dock (copied from podstage.html): code remotes side-by-side at the TOP
// (origin/feat right ▸ PR merge-arrow ▸ origin/main left), the memory remote mirrored to the BOTTOM.
// Each chip is placed by REMOTE_POS, not laid out in a row — so the strip reads as the governed flow,
// wired to the branch nodes by LandingFlows (push ↑ / pull ↓ / push-mem ↓).
export function RemoteStrip({ refs }: { refs: LandingRefNode[] }) {
  const byKind = (kind: string) => refs.find((ref) => ref.kind === kind);
  const main = byKind("origin-main");
  const feat = byKind("origin-feat");
  const pr = byKind("pr");
  const memMain = byKind("origin-mem-main");
  if (!main && !feat && !pr && !memMain) return null;
  return (
    <g data-testid="remote-strip">
      <text className={remoteStripHeader} x={PR_CX} y={REMOTE_POS["origin-main"].y - 12} textAnchor="middle">
        remote ▸ origin
      </text>
      {main ? <RemoteChip refNode={main} /> : null}
      {feat ? <RemoteChip refNode={feat} /> : null}
      {pr ? <PrBadge refNode={pr} /> : null}
      {memMain ? <RemoteChip refNode={memMain} /> : null}
    </g>
  );
}

// The directional landing flows wiring the dock to the branch nodes. These speak the cyan = ACTIVE /
// amber = SETTLED language: at most ONE flow is cyan at a time (the current transaction: push → pull →
// carryover), with the chevron + a travelling MotionPath dot; the moment its step completes the flow drops
// to a plain amber line (no chevron, no dot); steps not yet reached are hidden. The active flow advances by
// the landing[] ref states (origin-feat pushed → pr merged → origin-mem-main pushed) so panel + canvas agree.

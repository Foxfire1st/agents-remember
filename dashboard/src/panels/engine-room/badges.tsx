// Small SVG overlay/badge components for the Engine Room canvas: the HUD canopy, steady gates,
// reason/attention/moved badges, recovery chips, the fleeting enclosure, terminal stop, lane flags,
// and the closeout train. Each carries one responsibility and reads state from props only.
import { motion } from "motion/react";

import { useShouldAnimate } from "./useShouldAnimate";
import {
  attnBadge,
  attnText,
  canopyStroke,
  closeoutBeat,
  closeoutBeatLabel,
  closeoutRail,
  closeoutTrainLabel,
  fleetingBox,
  fleetingBoxReason,
  fleetingBoxTitle,
  gateBar,
  laneFlag,
  laneFlagText,
  movedBadge,
  movedText,
  movedTriangle,
  reasonBadge,
  reasonDot,
  reasonText,
  refusedConduit,
  stopBar,
  svgChip,
  svgChipText,
} from "./styles";
import {
  COL_WT_CX,
  EDGE_GEOM,
  ENGINE,
  conduitPathD,
  truncate,
} from "./geometry";
import type { EngineProcessEdge } from "../../types/projection";

export function CanopyFrame() {
  return (
    <g className={canopyStroke} aria-hidden="true" data-testid="canopy-frame">
      <rect x={14} y={14} width={1172} height={632} rx={22} strokeWidth={1.5} opacity={0.22} />
      <rect x={24} y={24} width={1152} height={612} rx={15} strokeWidth={1} opacity={0.1} />
      <path d="M58 22 L22 22 L22 58" strokeWidth={2} opacity={0.5} />
      <path d="M1142 22 L1178 22 L1178 58" strokeWidth={2} opacity={0.5} />
      <path d="M58 638 L22 638 L22 602" strokeWidth={2} opacity={0.5} />
      <path d="M1142 638 L1178 638 L1178 602" strokeWidth={2} opacity={0.5} />
      <g strokeWidth={1.2} opacity={0.3}>
        <line x1={300} y1={14} x2={300} y2={24} /><line x1={600} y1={14} x2={600} y2={24} /><line x1={900} y1={14} x2={900} y2={24} />
        <line x1={300} y1={646} x2={300} y2={636} /><line x1={600} y1={646} x2={600} y2={636} /><line x1={900} y1={646} x2={900} y2={636} />
        <line x1={14} y1={220} x2={24} y2={220} /><line x1={14} y1={440} x2={24} y2={440} />
        <line x1={1186} y1={220} x2={1176} y2={220} /><line x1={1186} y1={440} x2={1176} y2={440} />
      </g>
    </g>
  );
}


export function Gate({ edge }: { edge: EngineProcessEdge }) {
  const geom = EDGE_GEOM[edge.kind];
  if (!geom) return null;
  const [x1, y1, x2, y2] = geom;
  return (
    <rect
      className={gateBar}
      x={(x1 + x2) / 2 - 26}
      y={(y1 + y2) / 2 - 6}
      width={52}
      height={12}
      rx={3}
      data-testid="gate"
      data-kind={edge.kind}
    />
  );
}

// Alarm parity — a blocked/fault state raises this (breathing, not the fault flicker).
export function Attention({ hidden }: { hidden: boolean }) {
  return (
    <g data-testid="attention" visibility={hidden ? "hidden" : undefined}>
      <rect
        className={attnBadge}
        x={958}
        y={10}
        width={172}
        height={24}
        rx={5}
      />
      <text className={attnText} x={1044} y={26} textAnchor="middle">⚠ ATTENTION</text>
    </g>
  );
}

// A local reason badge (cyan-dot pointer + pill) stating WHY the lane is blocked, beside the gate.
export function ReasonBadge({ reason, cx, cy }: { reason: string; cx: number; cy: number }) {
  const text = truncate(reason, 46);
  const w = Math.max(120, text.length * 6.4 + 40);
  const px = cx - w / 2;
  return (
    <g data-testid="gate-reason">
      <rect className={reasonBadge} x={px} y={cy} width={w} height={22} rx={6} />
      <circle className={reasonDot} cx={px + 15} cy={cy + 11} r={4} />
      <text className={reasonText} x={px + 28} y={cy + 15}>{text}</text>
    </g>
  );
}

// The block indicators anchored ON the checked repository node (not the connector lane): a steady
// gate bar straddling the node's top edge + the reason badge above it, so the gate visibly "points at" the
// repository being blocked (the stale code base / the unmappable memory base). Mirrors the prototype, where
// the gate sits on the Code node, not the wire.
export function NodeBlock({ cx, top, reason }: { cx: number; top: number; reason: string }) {
  return (
    <g data-testid="node-block">
      <rect className={gateBar} x={cx - 64} y={top - 7} width={128} height={12} rx={3} data-testid="gate" />
      <ReasonBadge reason={reason} cx={cx} cy={top - 36} />
    </g>
  );
}

// T12B — the soft (cyan) "moved" remote indicator (podstage .imsg `moved`): a ▲ up-triangle + a pill
// announcing the UPSTREAM memory ref advanced (origin/mem-main moved ahead while the worktree holds local
// commits). Anchors ON the memory worktree NODE (never the connector lane), paints in the TOPMOST overlay.
// Mirrors ReasonBadge geometry but with the ▲ "moved" glyph (soft notification, not the alarm gate).
export function MovedBadge({ cx, cy, text }: { cx: number; cy: number; text: string }) {
  const label = truncate(text, 40);
  const w = Math.max(120, label.length * 6.4 + 44);
  const px = cx - w / 2;
  return (
    <g data-testid="moved-badge">
      <rect className={movedBadge} x={px} y={cy} width={w} height={22} rx={6} />
      {/* the ▲ "moved" pointer (an up-triangle): origin advanced AHEAD of the held worktree */}
      <path className={movedTriangle} d={`M${px + 15} ${cy + 6} L${px + 21} ${cy + 16} L${px + 9} ${cy + 16} Z`} />
      <text className={movedText} x={px + 30} y={cy + 15}>{label}</text>
    </g>
  );
}

// T7B — the provider-plan block (podstage P4). The runtime setup config is missing, so the provider
// engines never light. UNLIKE T1B/T3B this does NOT gate a repository node: the alarm bar sits BESIDE the
// worktree CGC provider engine (podstage `gate(1004,150,w108)` — the barred provider runtime), and the reason
// rides the TOP EDGE of the worktree enclosure as a header alert for the whole containment,
// not a node pointer. The two engine slots stay unlit; the dropout halos (rendered separately) mark them held.
export function ProviderBlock({ reason }: { reason: string }) {
  // enclosure box mirrors the dashed border / FleetingEnclosure (x = COL_WT_CX-126, y 76, right edge 1148).
  const enclosureCx = (COL_WT_CX - 126 + 1148) / 2;
  return (
    <g data-testid="provider-block">
      {/* the alarm bar — a VERTICAL bar attached to the LEFT side of the top provider engine slot,
          not a horizontal bar across it and not on the code node; the provider runtime is barred
          so the engines never light. Right edge meets the engine's left edge (1057); full slot height. */}
      <rect
        className={gateBar}
        x={ENGINE.cgc.x - 12}
        y={ENGINE.cgc.y - 6}
        width={12}
        height={ENGINE.h + 12}
        rx={3}
        data-testid="gate"
      />
      {/* the reason rides the enclosure's TOP edge — a containment header, not a node gate. */}
      <ReasonBadge reason={reason} cx={enclosureCx} cy={65} />
    </g>
  );
}

// Recovery choices (node.nextAction + enabled actions) as chips along the bottom of the stage.
export function RecoveryChips({ labels }: { labels: string[] }) {
  if (!labels.length) return null;
  let x = 690;
  return (
    <g data-testid="recovery-chips">
      {labels.slice(0, 3).map((label) => {
        const w = Math.max(110, label.length * 6.6 + 28);
        const chip = (
          <g key={label}>
            <rect className={svgChip} x={x} y={600} width={w} height={22} rx={4} />
            <text className={svgChipText} x={x + w / 2} y={615} textAnchor="middle">▸ {label}</text>
          </g>
        );
        x += w + 12;
        return chip;
      })}
    </g>
  );
}

// T1B — the FLEETING block enclosure (podstage `.fbox`): a born-blocked enclosure (stale-base /
// pre-contract) renders as the big red provisional box over the worktree footprint — the BLOCKED title +
// reason centred + the recovery chips along the bottom — REPLACING the dashed-amber border. Motion fades it.
export function FleetingEnclosure({ summary, choices }: { summary: string; choices: string[] }) {
  const animate = useShouldAnimate();
  const x = COL_WT_CX - 126;
  const y = 76;
  const w = 1148 - (COL_WT_CX - 126);
  const h = 506;
  const cx = x + w / 2;
  const shown = choices.slice(0, 3);
  const widths = shown.map((label) => Math.max(110, label.length * 6.6 + 28));
  const totalW = widths.reduce((sum, cw) => sum + cw, 0) + 12 * Math.max(0, widths.length - 1);
  let chipX = cx - totalW / 2;
  const chipY = y + h - 54;
  return (
    <motion.g
      data-testid="fleeting-enclosure"
      initial={animate ? { opacity: 0 } : false}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      transition={{ duration: animate ? 0.4 : 0 }}
    >
      <rect className={fleetingBox} x={x} y={y} width={w} height={h} rx={18} />
      <text className={fleetingBoxTitle} x={cx} y={y + h / 2 - 16} textAnchor="middle">⚠ BLOCKED · start gated</text>
      <text className={fleetingBoxReason} x={cx} y={y + h / 2 + 10} textAnchor="middle">{truncate(summary, 64)}</text>
      {shown.map((label, i) => {
        const cw = widths[i];
        const chip = (
          <g key={label}>
            <rect className={svgChip} x={chipX} y={chipY} width={cw} height={22} rx={4} />
            <text className={svgChipText} x={chipX + cw / 2} y={chipY + 15} textAnchor="middle">▸ {label}</text>
          </g>
        );
        chipX += cw + 12;
        return chip;
      })}
    </motion.g>
  );
}

// t14c — terminal integration conflict (podstage C4: a STOP bar on the lane + the reason ABOVE it).
// Heavier than the recoverable Gate; the source line does NOT move (all-or-nothing); no recovery chips.
// Two earlier defects fixed: (1) the conflict words were rendered ON the lane midpoint, so the bright red
// conduit line bisected the glyphs → illegible; they now ride a banner LIFTED clear of the lane line. (2) a
// 128px pill centred on the 72px feat↔worktree gap overran the worktree node → the on-lane bar is now sized
// to the gap. The bar carries NO `data-testid="gate"` (it is terminal, not a recoverable Gate).
export function TerminalStop({ edge }: { edge: EngineProcessEdge }) {
  const geom = EDGE_GEOM[edge.kind];
  if (!geom) return null;
  const [x1, y1, x2, y2] = geom;
  const cx = (x1 + x2) / 2;
  const cy = (y1 + y2) / 2;
  const label = "⛔ conflict · source did NOT move";
  const w = label.length * 7 + 30;
  const px = cx - w / 2;
  const by = cy - 58; // banner top — in the clear band ABOVE the node row, off the lane line
  return (
    <g data-testid="terminal-stop" data-kind={edge.kind}>
      {/* the on-lane STOP bar — fits the feat↔worktree gap, marks the conflict point (no node collision) */}
      <rect className={stopBar} data-fx="stop" x={cx - 33} y={cy - 6} width={66} height={12} rx={3} data-testid="terminal-stop-bar" />
      {/* the conflict banner ABOVE the lane — the SAME legible combo as the recoverable reason badges (a dark
          opaque pill with a RED border + LIGHT text), NOT dark-on-bright-red which washed out. The ⛔ glyph +
          the red border + the on-lane red bar carry the terminal identity. */}
      <rect className={reasonBadge} x={px} y={by} width={w} height={22} rx={6} />
      <text className={reasonText} x={cx} y={by + 15} textAnchor="middle">{label}</text>
    </g>
  );
}

// refused-conduit flash (SHARED — T9B red fault / T9C amber reroute / T14C red conflict): a one-shot
// GSAP flash (data-fx='refuse') along the EXACT lane geometry of the refused seed/return conduit — cyan →
// white spark → its polarity colour → fade out. The polarity is chosen by the caller off the projection
// (refusedPolarityOf), never hardcoded. Rests at opacity 0 (the cva base) so under effects=off it is
// present-but-absent; the steady STOP/gate (a separate element) carries the settled state.
export function RefusedConduit({ edge, polarity }: { edge: EngineProcessEdge; polarity: "amber" | "red" }) {
  const d = conduitPathD(edge);
  if (!d) return null;
  return (
    <path
      className={refusedConduit({ polarity })}
      d={d}
      data-fx="refuse"
      data-testid="refused-conduit"
      data-kind={edge.kind}
      data-polarity={polarity}
      data-refused-polarity={polarity}
    >
      <title>{edge.label} — seed refused ({polarity === "amber" ? "reroute → reindex" : "fault / conflict"})</title>
    </path>
  );
}

// Lane annotation flag (podstage.html #ledger / #hist): a small toned plate labelling a landing lane.
export function LaneFlag({ x, y, w, h, label, tone, testid, visible = true, enter = false }: {
  x: number; y: number; w: number; h: number; label: string;
  tone: "ledger" | "historical"; testid: string; visible?: boolean; enter?: boolean;
}) {
  // truncate to the box (laneFlagText is 11px ≈ 6 units/char) so a long branch name never overflows; the
  // full label is on hover. `visible` lets a lane annotation fade with its enclosure during the build-up (it
  // stays in the DOM for the presence tests; Motion eases the opacity). `enter` adds the landing-tail
  // fade+lift for a flag that only mounts when the enclosure starts to land. Under !animate it mounts at the
  // end-state (initial=false), synchronously.
  const animate = useShouldAnimate();
  return (
    <motion.g
      data-testid={testid}
      initial={animate ? { opacity: enter ? 0 : visible ? 1 : 0, y: enter ? -7 : 0 } : false}
      animate={{ opacity: visible ? 1 : 0, y: 0 }}
      transition={{ duration: animate ? 0.5 : 0, ease: [0.2, 0.7, 0.2, 1] }}
    >
      <title>{label}</title>
      <rect className={laneFlag({ tone })} x={x} y={y} width={w} height={h} rx={3} />
      <text className={laneFlagText({ tone })} x={x + w / 2} y={y + h / 2 + 5} textAnchor="middle">
        {truncate(label, Math.floor((w - 8) / 6.2))}
      </text>
    </motion.g>
  );
}

// T13 — closeout train: the known closeout order plays as a derived left-to-right strip on
// closeout-pending (the order is fixed and known, so it can be derived rather than probed live). Each beat group sweeps in via `closeoutSweep`
// with a per-beat delay; the global effects=off freeze settles it to the all-done strip. aria-hidden —
// the derived order is observability, not live status (which stays in the diagnostics panel).
const CLOSEOUT_BEATS = ["code", "onboard", "quality", "memory", "ledger"] as const;
export function CloseoutTrain({ x, y }: { x: number; y: number }) {
  const animate = useShouldAnimate();
  const bw = 60;
  const gap = 8;
  const railEnd = x + CLOSEOUT_BEATS.length * (bw + gap) - gap;
  return (
    <g data-testid="closeout-train" aria-hidden="true">
      <text className={closeoutTrainLabel} x={x} y={y - 6}>closeout order ▸</text>
      <line className={closeoutRail} x1={x} y1={y + 11} x2={railEnd} y2={y + 11} />
      {CLOSEOUT_BEATS.map((beat, i) => {
        const bx = x + i * (bw + gap);
        // Motion staggers each beat in (was the CSS closeoutSweep + inline animationDelay). Under !animate
        // initial=false → all 5 beats mount at rest synchronously, so the 5-rect presence test stays sync.
        return (
          <motion.g
            key={beat}
            initial={animate ? { opacity: 0 } : false}
            animate={{ opacity: 1 }}
            transition={{ duration: animate ? 0.45 : 0, delay: animate ? i * 0.28 : 0, ease: "easeOut" }}
          >
            <rect className={closeoutBeat} x={bx} y={y} width={bw} height={22} rx={4} />
            <text className={closeoutBeatLabel} x={bx + bw / 2} y={y + 15} textAnchor="middle">{beat}</text>
          </motion.g>
        );
      })}
    </g>
  );
}

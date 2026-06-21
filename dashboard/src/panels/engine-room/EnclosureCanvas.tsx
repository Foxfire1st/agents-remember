// The Engine Room pod-stage bird's-eye (5g G1): the live EngineProcessNode rendered as the
// two-world canvas from the design prototype (dashboard/public/_proto/podstage.html) — official
// line (left) <-> worktree enclosure (right), podracer engine gauges, the warp coupler, and the
// flow conduits. G1 is the STATIC frame (the nominal end-state); the boot/failure choreography
// (draw-on, travelling packets, center-out fill, gates) is G2+. Geometry is ported from the
// prototype's viewBox (0 0 1200 660). State always comes from the model (factState / runtimeState /
// edge.state), never a class name alone — so the truth stays in the projection, not the render.

import { useEffect, useRef, useState } from "react";
import { Dialog, Popover } from "react-aria-components";
import { AnimatePresence, motion } from "motion/react";

import { useEngineTimeline } from "./useEngineTimeline";
import { useShouldAnimate } from "./useShouldAnimate";
import type {
  CommitRefNode,
  EngineProcessEdge,
  EngineProcessNode,
  LandingRefNode,
  LedgerNode,
  LedgerRefNode,
  ProviderNode,
} from "../../types/projection";
import { engineState } from "../../data/selectors";
import {
  attnBadge,
  attnText,
  canopyStroke,
  closeoutBeat,
  closeoutBeatLabel,
  closeoutRail,
  closeoutTrainLabel,
  enclosureBorder,
  engineCharge,
  engineDiv,
  engineGaugeLabel,
  engineGaugeOut,
  enginePetal,
  engineReindexCharge,
  engineReindexOut,
  engineSpine,
  flowConduit,
  flowPacket,
  gateBar,
  laneFlag,
  laneFlagText,
  ledgerButton,
  ledgerButtonLabel,
  ledgerCard,
  ledgerCardHead,
  ledgerDate,
  ledgerHashCode,
  ledgerHashMem,
  ledgerMore,
  ledgerMsg,
  ledgerRowCss,
  ledgerScroll,
  ledgerSeam,
  ledgerShowMore,
  ledgerTable,
  officialWire,
  prBadge,
  prBadgeLabel,
  reasonBadge,
  reasonDot,
  reasonText,
  remoteChip,
  remoteChipLabel,
  remoteChipState,
  remoteStripHeader,
  sceneSvg,
  stopBar,
  stopText,
  svgChip,
  svgChipText,
  svgNodeBox,
  svgNodeLabel,
  svgNodeMeta,
  svgNodeTitle,
  warpCouplerBar,
  warpCouplerLabel,
  warpLinkGlyph,
  warpSurge,
  worktreeWire,
  worldLabel,
} from "./engineRoomStyles";

type ConduitState =
  | "nominal" | "complete" | "running" | "blocked" | "failed" | "stale" | "skipped" | "planned" | "unknown";
type RuntimeState = "nominal" | "configured" | "indexing" | "down" | "unknown";

function conduitState(value: string): ConduitState {
  switch (value) {
    case "nominal": case "complete": case "running": case "blocked":
    case "failed": case "stale": case "skipped": case "planned":
      return value;
    default:
      return "unknown";
  }
}

function runtimeState(value: string | undefined): RuntimeState {
  switch (value) {
    case "nominal": case "configured": case "indexing": case "down":
      return value;
    default:
      return "unknown";
  }
}

// The canopy housing (podstage.html .canopy): a decorative HUD frame — a double bevel rim, the four L
// corner brackets, and the edge ticks. Pure amber line-art at the stage edges; carries no state, so
// it is aria-hidden. Stroke is inherited from the group; per-element strokeWidth/opacity set inline.
function CanopyFrame() {
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

// --- geometry (ported 1:1 from podstage.html) --------------------------------
const NODE_H = 62;
// The three middle columns are anchored on these centres (viewBox 1200 wide). They are evenly spaced so
// the gap zones either side of feat breathe equally (~72px edge-to-edge), and main/worktree are pulled
// apart symmetrically about the stage centre (~600). Every dependent coordinate below — node x, couplers,
// remote chips, conduit/edge geometry, the engine→node wires, the landing-flow paths, and the enclosure
// border — is derived from or aligned to these three centres, so a future re-space is a small, local edit.
// POS.x is the LEFT edge (centre = x + w/2); the remote chips + couplers sit ON these centres.
const COL_MAIN_CX = 365; // official-line · main column (left of the middle three)
const COL_FEAT_CX = 595; // feat ▸ source column (the landing-only middle tier, in the main↔worktree gap)
const COL_WT_CX = 835; // worktree code/memory column (right of the middle three)
const POS = {
  codeSource: { x: COL_MAIN_CX - 90, y: 250, w: 180 }, // = mockup m-code (the OFFICIAL LINE = main, left)
  memorySource: { x: COL_MAIN_CX - 90, y: 372, w: 180 },
  codeWorktree: { x: COL_WT_CX - 100, y: 250, w: 200 }, // = mockup w-code (worktree, right)
  memoryWorktree: { x: COL_WT_CX - 100, y: 372, w: 200 },
  // The feat/fix source branch — the THIRD tier the worktree was actually branched off (5f §7.4). It
  // lives in the GAP between main and the worktree and is shown only during landing (mockup feat-code /
  // feat-mem, w136), so the closeout reads main → feat → worktree, never main = feat.
  featCode: { x: COL_FEAT_CX - 68, y: 250, w: 136 },
  featMemory: { x: COL_FEAT_CX - 68, y: 372, w: 136 },
} as const;
const ENGINE = {
  cgc: { x: 1057, y: 102 }, grepai: { x: 1057, y: 452 }, // worktree (enclosure) engines, right world
  mcgc: { x: 81, y: 102 }, mgrep: { x: 81, y: 452 }, // official-line (workspace) engines, left world
  w: 54, h: 96,
} as const;
const COUPLER_X = COL_WT_CX; // worktree code↔memory coupler — on the worktree column centre
const OFFICIAL_COUPLER_X = COL_MAIN_CX; // official-line code↔memory coupler (podstage cpl-main) — on the main centre

// Flow-conduit endpoints by edge kind, anchored to node/engine edges so a line never crosses a box.
const EDGE_GEOM: Record<string, readonly [number, number, number, number]> = {
  // main right edge (COL_MAIN_CX+90) → worktree left edge (COL_WT_CX-100): the intake lanes spanning the gap.
  "worktree-add": [COL_MAIN_CX + 90, 281, COL_WT_CX - 100, 281],
  "ledger-map": [COL_MAIN_CX + 90, 403, COL_WT_CX - 100, 403],
  // Provider CLONE arrows (5f §7.2 "cloned-from, not re-indexed"): the worktree engines are SEEDED BY
  // CLONING the official-line engines — a fast copy of the index / vector DB — NOT rebuilt from the
  // worktree code. So the seed/clone flow runs official-provider -> worktree-provider, sweeping across
  // the whole stage (CGC arcs over the top, GrepAI under the bottom; see the clone-arc path in Conduit),
  // and it is TRANSIENT — drawn only while the clone is running, gone at idle. The persistent
  // worktree-engine -> branch wiring is a separate static wire (worktree-wire), mirroring the left world.
  "cgc-seed": [135, 150, 1057, 150],
  "grepai-clone": [135, 500, 1057, 500],
  // sync shares the code intake lane's CENTRELINE with worktree-add (same source→worktree channel,
  // a later phase of it) — collinear, not stacked 8px below, so the blocked sync reads as one
  // centred line on the lane rather than a confusing off-centre double.
  sync: [COL_MAIN_CX + 90, 281, COL_WT_CX - 100, 281],
  // integration = the worktree's closeout commits returning to the feat/fix SOURCE branch in the gap
  // (the mockup's D2 int-code: worktree → feat); t14c STOPs it. The push flow then carries feat → origin/feat.
  integration: [COL_WT_CX - 100, 281, COL_FEAT_CX + 68, 281],
  // integration-mem mirrors it on the memory lane (y=403): the memory worktree's commits return to the feat
  // SOURCE (memory) before the carryover (feat → main mem). Same worktree→feat direction as the code lane.
  "integration-mem": [COL_WT_CX - 100, 403, COL_FEAT_CX + 68, 403],
};

// The build-up "branch-copy": a worktree node is born from its official-line node, rising from nothing
// while sliding in from the main side. Driven by the honesty axis — a `planned` worktree ref is not yet
// on disk (hidden, offset toward main), `observed`/`derived` is materialised (in place). Official-line
// nodes are always `observed`, so they stay settled. The sceneSvg transition tweens between these as the
// projection advances (and freezes instant under data-effects=off, so the count/presence tests stand).
function branchEnter(factState: CommitRefNode["factState"]): { opacity: number; dx: number } {
  switch (factState) {
    case "observed":
    case "derived":
      return { opacity: 1, dx: 0 };
    case "planned":
      return { opacity: 0, dx: -90 };
    case "missing":
      return { opacity: 0.22, dx: 0 };
    default:
      return { opacity: 0.45, dx: 0 };
  }
}

// During landing the official-line node reads as the protected branch (main) — the feat/fix SOURCE the
// worktree was branched off is shown as its own tier in the gap. Same on-disk commit; only the branch
// label flips, so the closeout reads main ◂ feat ◂ worktree instead of collapsing main and feat into one.
function mainRef(src: CommitRefNode): CommitRefNode {
  return { ...src, branch: "main" };
}

function BranchNode({ pos, label, refNode, landingIn = false, detaching = false }: {
  pos: { x: number; y: number; w: number };
  label: string;
  refNode: CommitRefNode;
  landingIn?: boolean;
  detaching?: boolean;
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
  // Motion owns the materialise/de-materialise (opacity + slide) + the landing-tier mount fade+lift (05f
  // §8); GSAP/CSS never touch this group. A detaching worktree node drifts out on a slight delay so the
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
      <rect className={svgNodeBox({ factState: refNode.factState })} x={pos.x} y={pos.y} width={pos.w} height={NODE_H} rx={8} />
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
function chargeMotion(runtime: RuntimeState): { scaleY: number; opacity: number } {
  switch (runtime) {
    case "nominal":
    case "indexing":
      return { scaleY: 1, opacity: runtime === "indexing" ? 0.85 : 0.55 };
    case "down":
      return { scaleY: 1, opacity: 0.55 };
    default:
      return { scaleY: 0, opacity: 0 }; // configured/unknown: drained (invisible)
  }
}

function EngineGauge({ at, label, runtime, reindex, present = true }: {
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
        <rect className={engineReindexCharge} data-fx="reindex" x={2} y={2} width={ENGINE.w - 4} height={ENGINE.h - 4} rx={3} />
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
const short = (commit: string | null | undefined): string => (commit ? commit.slice(0, 8) : "—");

// The commit's recorded wall-clock (5h Tier 2): "2026-06-18T18:19:48+02:00" -> "06-18 18:19". A plain
// string slice — no Date/timezone conversion, so it is deterministic + screenshot-stable and shows the
// committer's recorded offset, not the viewer's locale. Absent date -> empty cell (honest hash-only row).
const compactDate = (iso: string | undefined): string =>
  iso && iso.length >= 16 ? iso.slice(5, 16).replace("T", " ") : "";

// The warp coupler = the memory.md LEDGER link: the lookup-table row binding this side's code commit to
// its memory commit across the two physically distinct repos (5h coupler-semantics fix; NOT the task
// contract.md). A drawn chain-link glyph + the two linked short-hashes as the label, and — when bound —
// the warp-core surge (two hot bands born at the link, splitting up + down; ported from podstage.html).
// Default-show the newest LEDGER_PREVIEW rows; "▾ show N more" expands in place to the full served window
// (≤ LEDGER_WINDOW = 25), which scrolls. Older rows stay in the file ("+N more in memory.md"). The
// full-history browser is the post-ship viewer (agents-remember#88).
const LEDGER_PREVIEW = 8;

// The ledger-popover content (5h): the memory.md lookup table the coupler stands for, with THIS enclosure's
// row highlighted. Short SHAs for display (full pair in the row `title`). HTML inside a React-Aria Dialog.
function LedgerTable({ rows, total, currentCode }: {
  rows: LedgerRefNode[];
  total: number;
  currentCode?: string;
}) {
  const [expanded, setExpanded] = useState(false);
  // prefix-tolerant: the ledger holds full 40-char SHAs, the node commit may be a short prefix
  const isCurrent = (code: string): boolean =>
    !!currentCode && (code.startsWith(currentCode) || currentCode.startsWith(code));
  const shown = expanded ? rows : rows.slice(0, LEDGER_PREVIEW);
  const hiddenServed = rows.length - shown.length; // served rows the collapsed view is hiding
  const beyondWindow = total - rows.length; // older rows that live only in the file
  return (
    <div className={ledgerCard} data-testid="ledger-popover">
      <div className={ledgerCardHead}>memory.md ledger · code ⇄ memory</div>
      <div className={ledgerScroll({ expanded })}>
        <table className={ledgerTable}>
          <tbody>
            {shown.map((row) => {
              const current = isCurrent(row.codeCommit);
              return (
                <tr
                  key={`${row.codeCommit}-${row.memoryCommit}`}
                  className={ledgerRowCss({ current })}
                  data-current={current || undefined}
                >
                  {/* 6 columns (Tier 2): date | message | code-hash ⇄ memory-hash | message | date —
                      hashes meet the centre seam, message + date-time fan outward per side. Messages
                      truncate (full text in `title`); an unprobed side shows an empty message/date cell. */}
                  <td className={ledgerDate}>{compactDate(row.codeDate)}</td>
                  <td className={ledgerMsg} title={row.codeSubject}>{row.codeSubject ?? ""}</td>
                  <td className={ledgerHashCode} title={row.codeCommit}>{short(row.codeCommit)}</td>
                  <td className={ledgerSeam} aria-hidden="true">⇄</td>
                  <td className={ledgerHashMem} title={row.memoryCommit}>{short(row.memoryCommit)}</td>
                  <td className={ledgerMsg} title={row.memorySubject}>{row.memorySubject ?? ""}</td>
                  <td className={ledgerDate}>{compactDate(row.memoryDate)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {hiddenServed > 0 ? (
        <button
          type="button"
          className={ledgerShowMore}
          data-testid="ledger-show-more"
          onClick={() => setExpanded(true)}
        >
          ▾ show {hiddenServed} more
        </button>
      ) : beyondWindow > 0 ? (
        <div className={ledgerMore}>+{beyondWindow} more in memory.md</div>
      ) : null}
    </div>
  );
}

function WarpCoupler({ x, bound, label, testid = "warp-coupler", rows, total = 0, currentCode, visible = true }: {
  x: number;
  bound: boolean;
  label?: string;
  testid?: string;
  rows?: LedgerRefNode[];
  total?: number;
  currentCode?: string;
  visible?: boolean;
}) {
  const animate = useShouldAnimate();
  const cy = 342;
  const triggerRef = useRef<SVGRectElement>(null);
  // the popover anchors to this invisible point HIGH in the scene (SVG coords → scales with the canvas),
  // not the coupler button, so it opens in its old upper position and grows DOWNWARD from there (the
  // coupler button stays the click trigger). 5h Tier 2 feedback.
  const anchorRef = useRef<SVGRectElement>(null);
  const [open, setOpen] = useState(false);
  const ledgerRows = rows ?? [];
  const hasLedger = ledgerRows.length > 0; // a ledger-backed coupler opens its memory.md lookup table
  // Motion owns the coupler group's opacity: the bound dim (1 vs 0.3) AND the build-up `visible` gate (the
  // worktree coupler only appears once the memory worktree materialises) — one owner, no double-drive with
  // CSS. The warp-core surge bands are GSAP (data-fx='surge' + data-dir), driven by useEngineTimeline.
  const couplerOpacity = visible ? (bound ? 1 : 0.3) : 0;
  return (
    <>
      <motion.g
        data-testid={testid}
        data-bound={bound}
        initial={animate ? { opacity: couplerOpacity } : false}
        animate={{ opacity: couplerOpacity }}
        transition={{ duration: animate ? 0.45 : 0 }}
      >
        <line className={warpCouplerBar} x1={x} y1={312} x2={x} y2={372} />
        {/* invisible high anchor for the popover (upper position) — see anchorRef note above */}
        <rect ref={anchorRef} x={x + 90} y={58} width={1} height={1} fill="none" pointerEvents="none" aria-hidden="true" />
        {bound ? (
          <>
            <line className={warpSurge} data-fx="surge" data-dir="up" data-testid="warp-surge" x1={x} y1={cy - 4} x2={x} y2={cy + 4} />
            <line className={warpSurge} data-fx="surge" data-dir="down" data-testid="warp-surge" x1={x} y1={cy - 4} x2={x} y2={cy + 4} />
          </>
        ) : null}
        {/* the ledger link icon — a drawn chain-link (two interlocking rings), not the contract node */}
        <g className={warpLinkGlyph} aria-hidden="true" data-testid="warp-link">
          <ellipse cx={x} cy={cy - 3} rx={5} ry={4} />
          <ellipse cx={x} cy={cy + 3} rx={5} ry={4} />
        </g>
        {/* the coupler label: a ledger-backed coupler renders it as a clickable BUTTON (rect + label + a ▾
            "open" caret) beside the link glyph, opening the memory.md popover; otherwise a plain label.
            A <button> can't live in svg, so the rect is the trigger and the label text sits on top. */}
        {hasLedger ? (
          <>
            <rect
              ref={triggerRef}
              className={ledgerButton}
              x={x + 20}
              y={cy - 10}
              width={140}
              height={20}
              rx={4}
              role="button"
              tabIndex={0}
              aria-label={`open memory.md ledger — ${ledgerRows.length} of ${total} rows`}
              data-testid={`${testid}-ledger`}
              onClick={() => setOpen((value) => !value)}
              onKeyDown={(event) => {
                if (event.key === "Enter" || event.key === " ") {
                  event.preventDefault();
                  setOpen((value) => !value);
                }
              }}
            />
            <text className={ledgerButtonLabel} x={x + 27} y={cy + 4}>{label ?? "ledger"} ▾</text>
          </>
        ) : label ? (
          <text className={warpCouplerLabel} x={x + 13} y={cy + 4}>{label}</text>
        ) : null}
      </motion.g>
      {/* anchored to the high anchorRef (not the coupler) so it sits in its old upper position and grows
          DOWNWARD as it expands; shouldFlip=false keeps it from flipping up when the tall window meets the
          viewport edge (the inner scroll covers it) */}
      {hasLedger ? (
        <Popover triggerRef={anchorRef} isOpen={open} onOpenChange={setOpen} placement="bottom" shouldFlip={false}>
          <Dialog aria-label="memory.md ledger lookup table">
            <LedgerTable rows={ledgerRows} total={total} currentCode={currentCode} />
          </Dialog>
        </Popover>
      ) : null}
    </>
  );
}

function Conduit({ edge, strategy, retiring = false }: { edge: EngineProcessEdge; strategy?: string; retiring?: boolean }) {
  // The conduit draw-on (strokeDashoffset 100 → 0) is owned by the GSAP timeline (useEngineTimeline),
  // which selects every running lane via [data-draw='on'] and staggers them (05f §8). Motion owns this
  // group's opacity; CSS is static. A planned → running cycle re-runs the hook (its signature folds in the
  // running edges) so the lane re-draws, while Motion fades it in. Under !animate nothing runs and the path
  // rests fully drawn (offset 0, the rendered end-state) — the presence tests stay synchronous.
  const animate = useShouldAnimate();
  const geom = EDGE_GEOM[edge.kind];
  if (!geom) return null;
  const [x1, y1, x2, y2] = geom;
  // 5k F7 — the integration return lane (worktree closeout commits → feat/source branch in the gap) is a
  // plain straight connection like every other settled lane. `replay` (commits rebased onto a moved main)
  // vs a clean `ff-only` is NOT encodable as a line shape: a bent/bowed return lane read as an unexplained
  // triangle, never "around parallel work". The replay fact is recorded as data-strategy (for a future text
  // chip / glyph in the panel) but the path itself stays straight.
  const isReplay = (edge.kind === "integration" || edge.kind === "integration-mem") && strategy === "replay";
  // The provider-clone arrows sweep across the whole stage from the official engine to the worktree
  // engine — CGC bows OVER the top, GrepAI UNDER the bottom (the "copies + rewrites index" / "clones
  // vector DB" beat). Transient: shown only while running, gone at idle (see the opacity below).
  const cloneArc = edge.kind === "cgc-seed" || edge.kind === "grepai-clone";
  const dip = edge.kind === "grepai-clone" ? 104 : -116; // GrepAI bows down; CGC bows up
  const d = cloneArc
    ? `M${x1} ${y1} C ${x1 + 210} ${y1 + dip}, ${x2 - 210} ${y2 + dip}, ${x2} ${y2}`
    : `M${x1} ${y1} L ${x2} ${y2}`;
  // 5k F5 — at cleanup the worktree side de-materialises; fade every worktree conduit to 0 so the yellow
  // connector lines retract with the enclosure instead of dangling to the disposed nodes (the official line
  // keeps its own `officialWire` conduits, which are not in `node.edges`).
  const opacity = retiring ? 0 : cloneArc ? (edge.state === "running" ? 1 : 0) : edge.state === "planned" ? 0 : 1;
  return (
    <motion.g
      data-testid="conduit"
      data-kind={edge.kind}
      data-state={edge.state}
      data-strategy={isReplay ? "replay" : undefined}
      // a `planned` lane is hidden during the main-only B0; the transient clone arrows show ONLY while the
      // clone is running (gone at idle); every other lane fades in as it activates. Motion eases the opacity
      // (instant under !animate, where it mounts at the end-state).
      // 5o RETRACT VISIBILITY — clone arcs fade their GROUP to 0 when done; delay that fade so the GSAP
      // tail-to-tip retract (0.45s) completes before the group turns transparent (mirrors spec's 0.32s
      // opacity delay on .flow-g.off: retract runs first, then opacity clears).
      initial={animate ? { opacity } : false}
      animate={{ opacity }}
      transition={{ duration: animate ? 0.45 : 0, delay: animate && cloneArc && opacity === 0 ? 0.45 : 0 }}
    >
      <path
        className={flowConduit({ state: conduitState(edge.state) })}
        d={d}
        // GSAP DrawSVG draws this on when it goes running (data-draw='on') — 05n; the running conduit has no
        // CSS dash (solid), so DrawSVG owns the stroke reveal. No pathLength: DrawSVG measures real length.
        data-draw={edge.state === "running" ? "on" : undefined}
        // arrow tip only on an ACTION (running flow); a nominal/static line is just a connection
        markerEnd={edge.state === "running" ? "url(#er-chev)" : undefined}
      >
        <title>{edge.label}{edge.detail ? ` — ${edge.detail}` : ""}{isReplay ? " — replay (rebased onto moved main)" : ""}</title>
      </path>
      {edge.state === "running" && animate ? (
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

// --- failure overlays (5g G3) ------------------------------------------------
function isBlocked(node: EngineProcessNode): boolean {
  return (
    node.health === "blocked" ||
    node.health === "failed" ||
    node.health === "stale" ||
    node.missingFacts.length > 0
  );
}
function truncate(value: string, max: number): string {
  return value.length > max ? `${value.slice(0, max - 1)}…` : value;
}

// Steady red gate over a blocked/failed lane — a human choice required, never the fault flicker (the
// flicker is the engine, G4). Drawn at the blocked edge's midpoint.
function Gate({ edge }: { edge: EngineProcessEdge }) {
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
function Attention() {
  return (
    <g data-testid="attention">
      <rect className={attnBadge} data-fx="breath" x={958} y={10} width={172} height={24} rx={5} />
      <text className={attnText} x={1044} y={26} textAnchor="middle">⚠ ATTENTION</text>
    </g>
  );
}

// A local reason badge (cyan-dot pointer + pill) stating WHY the lane is blocked, beside the gate.
function ReasonBadge({ reason, cx, cy }: { reason: string; cx: number; cy: number }) {
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

// Recovery choices (node.nextAction + enabled actions) as chips along the bottom of the stage.
function RecoveryChips({ labels }: { labels: string[] }) {
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

// t14c — terminal integration conflict: a STOP at the integration lane's midpoint (flash → steady).
// Heavier than the recoverable Gate; the source line does NOT move (all-or-nothing); no recovery chips.
function TerminalStop({ edge }: { edge: EngineProcessEdge }) {
  const geom = EDGE_GEOM[edge.kind];
  if (!geom) return null;
  const [x1, y1, x2, y2] = geom;
  const cx = (x1 + x2) / 2;
  const cy = (y1 + y2) / 2;
  return (
    <g data-testid="terminal-stop" data-kind={edge.kind}>
      <rect className={stopBar} data-fx="stop" x={cx - 64} y={cy - 13} width={128} height={26} rx={4} />
      <text className={stopText} x={cx} y={cy + 4} textAnchor="middle">⛔ STOP · CONFLICT</text>
    </g>
  );
}

// Lane annotation flag (podstage.html #ledger / #hist): a small toned plate labelling a landing lane.
function LaneFlag({ x, y, w, h, label, tone, testid, visible = true, enter = false }: {
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

// T13 — closeout train (5h H2): the known closeout order plays as a derived left-to-right strip on
// closeout-pending (5f §9 allows deriving the fixed order). Each beat group sweeps in via `closeoutSweep`
// with a per-beat delay; the global effects=off freeze settles it to the all-done strip. aria-hidden —
// the derived order is observability, not live status (which stays in the diagnostics panel).
const CLOSEOUT_BEATS = ["code", "onboard", "quality", "memory", "ledger"] as const;
function CloseoutTrain({ x, y }: { x: number; y: number }) {
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

// --- 5h H3: remote/landing dock beyond the official line (T15 code PR+push, T16 carryover) ---------
// Geometry copied from podstage.html (the mockup): the CODE remotes sit side-by-side at the TOP and
// read RIGHT → LEFT — origin/feat (just pushed from the worktree, on the right) ▸ a PR merge-arrow ▸
// origin/main (merged into the official line, on the left). The MEMORY remote (origin/mem-main) is
// mirrored to the BOTTOM, beneath the memory lane. The directional landing flows (push ↑ / pull ↓ /
// push-mem ↓ — see LandingFlows) wire the dock to the branch nodes so it reads as the governed flow.
const RBOX_W = 148; // = mockup r-box width
const RBOX_H = 36;
const REMOTE_POS: Record<string, { x: number; y: number }> = {
  "origin-main": { x: COL_MAIN_CX - RBOX_W / 2, y: 66 }, // top, centred above the main column (merges into it)
  "origin-feat": { x: COL_FEAT_CX - RBOX_W / 2, y: 66 }, // top, centred above the feat column (just pushed)
  "origin-mem-main": { x: COL_MAIN_CX - RBOX_W / 2, y: 522 }, // mirrored to the bottom, under the main column
};
const PR_CX = (COL_MAIN_CX + COL_FEAT_CX) / 2; // centre of the gap between origin/main and origin/feat
// The dock is the SUCCESSFUL-LANDING arc — it shows only while an enclosure is actually retiring to the
// official line (closeout → integration → cleanup), not for every live worktree the probe touched.
const LANDING_PHASES = new Set(["closeout-pending", "integration-pending", "cleanup-pending"]);
type RemoteTone = "planned" | "live" | "done";

function remoteTone(ref: LandingRefNode): RemoteTone {
  if (ref.factState === "planned" || ref.state === "planned") return "planned";
  if (ref.state === "tip" || ref.state === "merged" || ref.state === "pushed") return "done";
  return "live";
}

// One short status word per chip — the colour already carries the tone, so the line stays terse and
// always fits; the full ref + detail lives in the hover <title>.
function remoteStateWord(ref: LandingRefNode): string {
  if (ref.factState === "planned" || ref.state === "planned") return "planned";
  return ref.state || "—";
}

function RemoteChip({ refNode }: { refNode: LandingRefNode }) {
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
      <title>{`${refNode.label} · ${refNode.detail ?? refNode.state}`}</title>
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
function PrBadge({ refNode }: { refNode: LandingRefNode }) {
  const animate = useShouldAnimate();
  const state = refNode.state === "merged" ? "merged" : "open";
  const sub = state === "merged" ? "merged" : refNode.state;
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
      <title>{refNode.detail ? `${refNode.label} · ${sub} · ${refNode.detail}` : `${refNode.label} · ${sub}`}</title>
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
function RemoteStrip({ refs }: { refs: LandingRefNode[] }) {
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

// The directional landing flows wiring the dock to the branch nodes. 5k F4 — these speak the cyan = ACTIVE /
// amber = SETTLED language: at most ONE flow is cyan at a time (the current transaction: push → pull →
// carryover), with the chevron + a travelling MotionPath dot; the moment its step completes the flow drops
// to a plain amber line (no chevron, no dot); steps not yet reached are hidden. The active flow advances by
// the landing[] ref states (origin-feat pushed → pr merged → origin-mem-main pushed) so panel + canvas agree.
type FlowState = "active" | "settled" | "hidden";

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
function landingFlowState(refs: LandingRefNode[], kind: string): FlowState {
  const ref = (k: string) => refs.find((r) => r.kind === k);
  const resolved = (k: string) => {
    const r = ref(k);
    return r ? r.factState !== "planned" && r.state !== "planned" : false;
  };
  const prMerged = ref("pr")?.state === "merged";
  const memPushed = ref("origin-mem-main")?.state === "pushed";
  if (kind === "push") return !resolved("origin-feat") ? "hidden" : prMerged ? "settled" : "active";
  if (kind === "pull") return !prMerged ? "hidden" : memPushed ? "settled" : "active";
  return memPushed ? "active" : "hidden"; // carry + push-mem: the carryover frontier
}

function LandingFlows({ refs }: { refs: LandingRefNode[] }) {
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

export function EnclosureCanvas({ node, workspaceEngines = [], officialLedger }: {
  node: EngineProcessNode;
  workspaceEngines?: ProviderNode[];
  officialLedger?: LedgerNode;
}) {
  const animate = useShouldAnimate();
  // GSAP owns the strokeDashoffset draw-ons ([data-draw='on']) + the repeating fx ([data-fx=…]) as one
  // gsap.context scoped to this <svg> root; Motion (below) owns opacity/transform/scaleY/fill + enter/exit;
  // CSS is static (05f §8). The hook self-gates on useShouldAnimate (no context, no ticker, under effects=off).
  const rootRef = useRef<SVGSVGElement>(null);
  useEngineTimeline(rootRef, node);
  const code = node.providers.find((p) => p.role === "code");
  const memory = node.providers.find((p) => p.role === "memory");
  // 5o PREDICTIVE BOOT — the clone arrow draws toward the worktree engine for ~0.6s; the engine should
  // start filling at the same moment the arrow begins drawing, not after the data says "indexing". When
  // cgc-seed / grepai-clone is running and the engine is still configured (not yet self-reported), treat
  // it as indexing so the fill animates in sync with the arrow and arrives when the arrow does.
  const cgcSeedRunning = node.edges.some((e) => e.kind === "cgc-seed" && e.state === "running");
  const grepaiCloneRunning = node.edges.some((e) => e.kind === "grepai-clone" && e.state === "running");
  const codeRuntimePredicted = runtimeState(
    cgcSeedRunning && (code?.runtimeState === "configured" || !code?.runtimeState) ? "indexing" : code?.runtimeState
  );
  const memoryRuntimePredicted = runtimeState(
    grepaiCloneRunning && (memory?.runtimeState === "configured" || !memory?.runtimeState) ? "indexing" : memory?.runtimeState
  );
  const hasMemory = node.memoryMode === "external" && !!node.memoryWorktree;
  // Build-up materialisation gates (the honesty axis): the enclosure shell + the worktree coupler only
  // appear once the matching worktree ref is observed on disk; the worktree engines materialise when
  // their provider runtime deploys (B3). At main-only B0 these are all `planned`/absent → faded out, so
  // the left world stands alone and the enclosure assembles as the projection advances.
  const codeWtMaterialised =
    node.codeWorktree.factState === "observed" || node.codeWorktree.factState === "derived";
  const memWtMaterialised =
    node.memoryWorktree?.factState === "observed" || node.memoryWorktree?.factState === "derived";
  // Official-line (workspace) engines — the real shared CGC/GrepAI feeding the official line (left
  // world); runtime derived like the OfficialStrip so the two surfaces always agree.
  const officialCode = workspaceEngines.find((engine) => engine.role === "code");
  const officialMemory = workspaceEngines.find((engine) => engine.role === "memory");
  // failure overlays (5g G3)
  const fleeting = node.missingFacts.some((fact) => /contract not yet written/i.test(fact));
  // t14c — a terminal integration conflict draws a STOP (not the recoverable Gate) and no recovery chips.
  const terminal = node.phase === "integration-blocked";
  const terminalEdge = terminal
    ? node.edges.find((e) => e.kind === "integration" && e.state === "blocked")
    : undefined;
  // blocked = STEADY gate (a choice required); failed/down = FAULT → the engine flickers, no gate (G4).
  // The terminal-conflict integration edge is excluded — it renders as a STOP instead of a Gate.
  const gatedEdges = node.edges.filter((e) => e.state === "blocked" && e !== terminalEdge);
  const firstGated = gatedEdges.length ? EDGE_GEOM[gatedEdges[0].kind] : undefined;
  const stopGeom = terminalEdge ? EDGE_GEOM[terminalEdge.kind] : undefined;
  const memoryDown = memory?.runtimeState === "down";
  const codeDown = code?.runtimeState === "down";
  // the reason badge anchors at the STOP / blocked lane, else beside the faulting (down) engine
  const reasonCenter = stopGeom
    ? { cx: (stopGeom[0] + stopGeom[2]) / 2, cy: (stopGeom[1] + stopGeom[3]) / 2 + 16 }
    : firstGated
      ? { cx: (firstGated[0] + firstGated[2]) / 2, cy: (firstGated[1] + firstGated[3]) / 2 + 14 }
      : memoryDown
        ? { cx: 1084, cy: 562 }
        : codeDown
          ? { cx: 1084, cy: 88 }
          : undefined;
  const recovery = [
    ...new Set(
      [
        node.nextAction,
        node.retryArgs ? "retry setup" : undefined,
        ...node.actions.filter((a) => a.enabled).map((a) => a.action),
      ].filter((value): value is string => Boolean(value)),
    ),
  ];
  // 5h H3 — show the landing dock only while the enclosure is actually retiring to the official line,
  // and only the refs the probe could resolve: a `missing` ref (probe couldn't run, e.g. gh absent)
  // carries no signal and is dropped, never rendered as an "unknown" chip.
  const landingRefs = (node.landing ?? []).filter((ref) => ref.factState !== "missing");
  const showLanding =
    landingRefs.length > 0 && (LANDING_PHASES.has(node.phase) || Boolean(node.integrationStrategy));
  // 5h H4 — cleanup teardown: a retiring enclosure (abandon OR a landed cleanup) keeps the historical
  // contract chip; on a successful cleanup the "back into main" seam reads the resolved origin-main tip.
  const retiring = node.phase === "abandoned" || node.phase === "cleanup-pending";
  const cleanupTip =
    node.phase === "cleanup-pending"
      ? node.landing?.find((ref) => ref.kind === "origin-main" && ref.factState !== "missing")
      : undefined;
  return (
    <svg
      ref={rootRef}
      className={sceneSvg}
      viewBox="0 0 1200 660"
      preserveAspectRatio="xMidYMid meet"
      role="img"
      aria-label={`Engine room — ${node.taskName} — ${node.health}`}
      data-testid="enclosure-canvas"
    >
      <defs>
        {/* refX sits at the chevron's VISUAL tip (geom apex 8.5 + the round join's ~1.1) so the
            arrowhead lands ON the line end, never overshooting past it into the target engine/box. */}
        <marker id="er-chev" viewBox="0 0 10 10" refX="9.6" refY="5" markerWidth="9" markerHeight="9" orient="auto">
          <path d="M1.5 1 L8.5 5 L1.5 9" fill="none" stroke="context-stroke" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" />
        </marker>
      </defs>

      <CanopyFrame />
      <text className={worldLabel} x={55} y={40}>Official line · workspace</text>
      <text className={worldLabel} x={930} y={40}>Worktree enclosure</text>
      {hasMemory ? (
        <motion.rect
          className={enclosureBorder}
          // left edge tracks the worktree column (26px of inner padding before the code/memory nodes), so the
          // dashed boundary sits in the main↔worktree gap just right of feat; right edge stays at 1148 (it
          // wraps the right-world engines, which don't move).
          x={COL_WT_CX - 126}
          y={76}
          width={1148 - (COL_WT_CX - 126)}
          height={506}
          rx={18}
          // the enclosure shell only exists once the code worktree materialises (B1); at main-only B0 it is
          // faded out, so the build-up draws the border in FIRST. On teardown it collapses LAST (after the
          // engines power down + nodes drift), via the delay. Motion owns the opacity; under !animate it
          // mounts at the end-state (0.5 = the dashed-amber resting border).
          initial={animate ? { opacity: codeWtMaterialised ? 0.5 : 0 } : false}
          animate={{ opacity: codeWtMaterialised ? 0.5 : 0 }}
          transition={{ duration: animate ? 0.45 : 0, delay: animate ? (codeWtMaterialised ? 0 : 0.3) : 0 }}
        />
      ) : null}

      {node.edges.map((edge) => <Conduit key={edge.id} edge={edge} strategy={node.integrationStrategy} retiring={retiring} />)}

      {/* THREE-TIER (5f §7.4, copied 1-to-1 from the mockup): the OFFICIAL LINE is MAIN (the protected
          branch) — ALWAYS, in the build-up and the landing — and the worktree forks from it on the right.
          The feat/fix SOURCE the worktree was branched off appears in the GAP only during landing (the
          mockup's tear-down reveal), so closeout reads main ◂ feat ◂ worktree. The official-line nodes
          never relabel/remount across the transition; the feat tier fades in (landingIn). */}
      <BranchNode pos={POS.codeSource} label="Official line · main" refNode={mainRef(node.codeSource)} />
      {hasMemory && node.memorySource ? (
        <BranchNode pos={POS.memorySource} label="Official line · main" refNode={mainRef(node.memorySource)} />
      ) : null}
      <AnimatePresence>
        {showLanding ? (
          <motion.g
            key="feat-tier"
            initial={animate ? { opacity: 0 } : false}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: animate ? 0.4 : 0 }}
          >
            <BranchNode pos={POS.featCode} label="feat ▸ source" refNode={node.codeSource} landingIn />
            {hasMemory && node.memorySource ? (
              <BranchNode pos={POS.featMemory} label="feat ▸ source" refNode={node.memorySource} landingIn />
            ) : null}
          </motion.g>
        ) : null}
      </AnimatePresence>
      <BranchNode pos={POS.codeWorktree} label="Code worktree" refNode={node.codeWorktree} detaching={retiring} />
      {hasMemory && node.memoryWorktree ? (
        <BranchNode pos={POS.memoryWorktree} label="Memory worktree" refNode={node.memoryWorktree} detaching={retiring} />
      ) : null}

      {/* Official-line (left world): the workspace engines + their wiring + the official code↔memory
          coupler, ported from podstage.html (m-cgc / m-grep / w-m-* / cpl-main). Real providers. */}
      {officialCode ? <line className={officialWire} x1={135} y1={198} x2={COL_MAIN_CX - 90} y2={281} data-testid="official-wire" /> : null}
      {officialMemory && hasMemory ? <line className={officialWire} x1={135} y1={452} x2={COL_MAIN_CX - 90} y2={403} data-testid="official-wire" /> : null}
      {officialCode ? <EngineGauge at={ENGINE.mcgc} label="CGC" runtime={runtimeState(engineState(officialCode))} /> : null}
      {officialMemory ? <EngineGauge at={ENGINE.mgrep} label="GrepAI" runtime={runtimeState(engineState(officialMemory))} /> : null}
      {hasMemory ? (
        <WarpCoupler
          x={OFFICIAL_COUPLER_X}
          bound={hasMemory}
          testid="warp-coupler-official"
          label={`${short(node.codeSource.commit)} ⇄ ${short(node.memorySource?.commit)}`}
          rows={officialLedger?.rows}
          total={officialLedger?.closeoutCount}
          currentCode={node.codeSource.commit ?? undefined}
        />
      ) : null}

      {/* Worktree engine → branch wiring (mirror of the official-line wires): the cloned engine serves
          its own branch. This is the PERSISTENT structural link — present once the engine materialises,
          and it stays at idle — distinct from the transient clone arrows that copy the index across from
          the official engines (which vanish when the clone completes). */}
      <motion.line
        className={worktreeWire}
        x1={1057}
        y1={198}
        x2={COL_WT_CX + 100}
        y2={281}
        data-testid="worktree-wire"
        initial={animate ? { opacity: code ? 0.8 : 0 } : false}
        animate={{ opacity: code ? 0.8 : 0 }}
        transition={{ duration: animate ? 0.45 : 0, delay: animate ? (code ? 0 : 0.2) : 0 }}
      />
      {hasMemory ? (
        <motion.line
          className={worktreeWire}
          x1={1057}
          y1={452}
          x2={COL_WT_CX + 100}
          y2={403}
          data-testid="worktree-wire"
          initial={animate ? { opacity: memory ? 0.8 : 0 } : false}
          animate={{ opacity: memory ? 0.8 : 0 }}
          transition={{ duration: animate ? 0.45 : 0, delay: animate ? (memory ? 0 : 0.2) : 0 }}
        />
      ) : null}
      <EngineGauge
        at={ENGINE.cgc}
        label="CGC"
        runtime={codeRuntimePredicted}
        reindex={node.seedFallback}
        present={!!code}
      />
      {hasMemory ? (
        <EngineGauge
          at={ENGINE.grepai}
          label="GrepAI"
          runtime={memoryRuntimePredicted}
          present={!!memory}
        />
      ) : null}

      <WarpCoupler
        x={COUPLER_X}
        bound={hasMemory}
        visible={memWtMaterialised}
        label={`${short(node.codeWorktree.commit)} ⇄ ${short(node.memoryWorktree?.commit)}`}
        rows={node.ledgerRows}
        total={node.ledgerRowCount}
        currentCode={node.codeWorktree.commit ?? undefined}
      />

      {/* Lane annotations (podstage.html #ledger / #hist): the worktree landing lane + a historical
          contract marker. Descriptive lane labels; the live status stays in the diagnostics panel. */}
      {hasMemory ? (
        <LaneFlag
          x={730}
          y={476}
          w={140}
          h={24}
          label="ledger ▸ maps merge"
          tone="ledger"
          testid="lane-ledger"
          visible={memWtMaterialised}
        />
      ) : null}
      {retiring ? <LaneFlag x={300} y={560} w={180} h={26} label="contract · historical" tone="historical" testid="lane-historical" /> : null}
      {cleanupTip ? (
        <LaneFlag
          x={300}
          y={188}
          w={196}
          h={20}
          label={`▸ back into ${cleanupTip.label} · ${cleanupTip.state}`}
          tone="ledger"
          testid="lane-back-into-main"
          enter
        />
      ) : null}

      {/* 5h H2 — the landing arc: the closeout train (T13) on closeout-pending, and the official source
          line advancing to its landing tip (T14). */}
      {/* The closeout train (T13) glides in when closeout starts and glides OUT when the phase advances
          to integration (instead of vanishing). Motion owns the enter/exit; `transition: none` opts the
          group out of the sceneSvg transition so the two systems never write opacity at once. Under
          data-effects=off it is an instant mount/unmount (the 5-rect presence test stays synchronous). */}
      <AnimatePresence>
        {node.phase === "closeout-pending" ? (
          <motion.g
            key="closeout-train"
            style={{ transition: "none" }}
            initial={animate ? { opacity: 0, y: 8 } : false}
            animate={{ opacity: 1, y: 0 }}
            exit={animate ? { opacity: 0, y: 8 } : { opacity: 0 }}
            transition={{ duration: animate ? 0.4 : 0 }}
          >
            {/* Bottom-aligned breadcrumb: the beats sit on y=600 — the same baseline as the bottom gate/
                recovery chips (RecoveryChips, also y=600) — so the strip reads as one bottom row rather than
                floating mid-stage. x=260 keeps it left of centre and clear of the left engine (right edge 135)
                and the gate chips (start x=690); the darker lower backdrop keeps the caption legible. */}
            <CloseoutTrain x={260} y={600} />
          </motion.g>
        ) : null}
      </AnimatePresence>
      {/* 5h H3 — the remote/landing dock beyond the official line (copied from the mockup): code remotes
          at the top (origin/feat ▸ PR ▸ origin/main, reading right→left), the memory remote mirrored to
          the bottom, wired to the branch nodes by the directional landing flows (push ↑ / pull ↓ /
          push-mem ↓). Shown only while the enclosure is landing, with `missing` probe refs dropped. */}
      <AnimatePresence>
        {showLanding ? (
          <motion.g
            key="landing-dock"
            initial={animate ? { opacity: 0 } : false}
            // 5k F1 — retract the whole landing tier (origin chips + the push/pull/carry/push-mem flows) as
            // the enclosure de-materialises: when `retiring` (cleanup-pending), fade the dock to 0 in sync
            // with the engines (parent opacity multiplies through the children) so the tier powers down with
            // them, instead of staying lit then hard-unmounting at the next beat.
            animate={{ opacity: retiring ? 0 : 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: animate ? 0.4 : 0 }}
          >
            <LandingFlows refs={landingRefs} />
            <RemoteStrip refs={landingRefs} />
          </motion.g>
        ) : null}
      </AnimatePresence>

      {!hasMemory ? (
        <text className={svgNodeMeta} x={930} y={420} textAnchor="middle" data-testid="memory-lane-absent">
          memory: {node.memoryMode} — no external lane
        </text>
      ) : null}

      {/* failure overlays (5g G3): a steady gate over each blocked lane + a local reason badge, the
          alarm-parity attention badge, and recovery chips. A fleeting (pre-contract) block keeps its
          ghost banner in EnclosureProcessMap, so the scene gate / reason / chips defer to it. */}
      {!fleeting ? gatedEdges.map((edge) => <Gate key={`gate-${edge.id}`} edge={edge} />) : null}
      {!fleeting && terminalEdge ? <TerminalStop edge={terminalEdge} /> : null}
      {!fleeting && reasonCenter ? (
        <ReasonBadge reason={node.summary} cx={reasonCenter.cx} cy={reasonCenter.cy} />
      ) : null}
      {isBlocked(node) ? <Attention /> : null}
      {!fleeting && !terminal ? <RecoveryChips labels={recovery} /> : null}
    </svg>
  );
}

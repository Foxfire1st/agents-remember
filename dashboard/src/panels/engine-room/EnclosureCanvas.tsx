// The Engine Room pod-stage bird's-eye (5g G1): the live EngineProcessNode rendered as the
// two-world canvas from the design prototype (dashboard/public/_proto/podstage.html) — official
// line (left) <-> worktree enclosure (right), podracer engine gauges, the warp coupler, and the
// flow conduits. G1 is the STATIC frame (the nominal end-state); the boot/failure choreography
// (draw-on, travelling packets, center-out fill, gates) is G2+. Geometry is ported from the
// prototype's viewBox (0 0 1200 660). State always comes from the model (factState / runtimeState /
// edge.state), never a class name alone — so the truth stays in the projection, not the render.

import { useRef, useState } from "react";
import { Dialog, Popover } from "react-aria-components";

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
  closeoutBeatG,
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
  prBadgeSub,
  reasonBadge,
  reasonDot,
  reasonText,
  remoteChip,
  remoteChipLabel,
  remoteChipState,
  remoteConnector,
  remoteConnectorCarry,
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
  warpCouplerG,
  warpCouplerLabel,
  warpLinkGlyph,
  warpSurge,
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
const POS = {
  codeSource: { x: 300, y: 250, w: 180 },
  memorySource: { x: 300, y: 372, w: 180 },
  codeWorktree: { x: 700, y: 250, w: 200 },
  memoryWorktree: { x: 700, y: 372, w: 200 },
} as const;
const ENGINE = {
  cgc: { x: 1057, y: 102 }, grepai: { x: 1057, y: 452 }, // worktree (enclosure) engines, right world
  mcgc: { x: 81, y: 102 }, mgrep: { x: 81, y: 452 }, // official-line (workspace) engines, left world
  w: 54, h: 96,
} as const;
const COUPLER_X = 800; // worktree code↔memory coupler
const OFFICIAL_COUPLER_X = 390; // official-line code↔memory coupler (podstage cpl-main)

// Flow-conduit endpoints by edge kind, anchored to node/engine edges so a line never crosses a box.
const EDGE_GEOM: Record<string, readonly [number, number, number, number]> = {
  "worktree-add": [480, 281, 698, 281],
  "ledger-map": [480, 403, 698, 403],
  // Provider conduits run from the box's side-edge MIDDLE to the engine's INNER corner, pointing INTO
  // the engine (it reads + indexes the source); the chevron shows only when running (see Conduit).
  "cgc-seed": [900, 281, 1057, 198],
  "grepai-clone": [900, 403, 1057, 452],
  // sync shares the code intake lane's CENTRELINE with worktree-add (same source→worktree channel,
  // a later phase of it) — collinear, not stacked 8px below, so the blocked sync reads as one
  // centred line on the lane rather than a confusing off-centre double.
  sync: [480, 281, 698, 281],
  // integration = the worktree → official "landing" return lane (above the code lane); t14c STOPs it.
  integration: [690, 234, 490, 234],
};

function BranchNode({ pos, label, refNode }: {
  pos: { x: number; y: number; w: number };
  label: string;
  refNode: CommitRefNode;
}) {
  const cx = pos.x + pos.w / 2;
  const branch = refNode.branch ?? "—";
  // truncate to the box width (~7.4px/char at 14px); the full string is in the <title> (hover).
  const maxChars = Math.max(8, Math.floor((pos.w - 20) / 7.4));
  const flags = `${refNode.dirty ? " · dirty" : ""}${refNode.behindSource ? ` · ${refNode.behindSource} behind` : ""}`;
  const full = `${label}: ${branch}${refNode.commit ? ` @ ${refNode.commit}` : ""}${flags}`;
  return (
    <g data-testid="branch-node" data-fact={refNode.factState}>
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
    </g>
  );
}

function EngineGauge({ at, label, runtime, reindex }: {
  at: { x: number; y: number };
  label: string;
  runtime: RuntimeState;
  reindex?: boolean;
}) {
  const state = reindex ? "reindex" : runtime;
  return (
    <g
      transform={`translate(${at.x},${at.y})`}
      data-testid="engine-gauge"
      data-runtime={state}
      role="img"
      aria-label={`${label} engine ${state}`}
    >
      <rect className={reindex ? engineReindexOut : engineGaugeOut({ runtimeState: runtime })} x={0} y={0} width={ENGINE.w} height={ENGINE.h} rx={5} />
      <rect
        className={reindex ? engineReindexCharge : engineCharge({ runtimeState: runtime })}
        x={2}
        y={2}
        width={ENGINE.w - 4}
        height={ENGINE.h - 4}
        rx={3}
      />
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
    </g>
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

function WarpCoupler({ x, bound, label, testid = "warp-coupler", rows, total = 0, currentCode }: {
  x: number;
  bound: boolean;
  label?: string;
  testid?: string;
  rows?: LedgerRefNode[];
  total?: number;
  currentCode?: string;
}) {
  const cy = 342;
  const triggerRef = useRef<SVGRectElement>(null);
  // the popover anchors to this invisible point HIGH in the scene (SVG coords → scales with the canvas),
  // not the coupler button, so it opens in its old upper position and grows DOWNWARD from there (the
  // coupler button stays the click trigger). 5h Tier 2 feedback.
  const anchorRef = useRef<SVGRectElement>(null);
  const [open, setOpen] = useState(false);
  const ledgerRows = rows ?? [];
  const hasLedger = ledgerRows.length > 0; // a ledger-backed coupler opens its memory.md lookup table
  return (
    <>
      <g className={warpCouplerG({ bound })} data-testid={testid} data-bound={bound}>
        <line className={warpCouplerBar} x1={x} y1={312} x2={x} y2={372} />
        {/* invisible high anchor for the popover (upper position) — see anchorRef note above */}
        <rect ref={anchorRef} x={x + 90} y={58} width={1} height={1} fill="none" pointerEvents="none" aria-hidden="true" />
        {bound ? (
          <>
            <line className={warpSurge({ dir: "up" })} data-testid="warp-surge" x1={x} y1={cy - 4} x2={x} y2={cy + 4} />
            <line className={warpSurge({ dir: "down" })} data-testid="warp-surge" x1={x} y1={cy - 4} x2={x} y2={cy + 4} />
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
      </g>
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

function Conduit({ edge, strategy }: { edge: EngineProcessEdge; strategy?: string }) {
  const geom = EDGE_GEOM[edge.kind];
  if (!geom) return null;
  const [x1, y1, x2, y2] = geom;
  // T14b — a `replay` integration bends the landing return lane around the parallel work that moved the
  // official line (vs the straight `ff-only` fast-forward). Same draw-on/packet idiom, a different path.
  const bent = edge.kind === "integration" && strategy === "replay";
  const d = bent
    ? `M${x1} ${y1} C ${x1 - 60} ${y1 - 54}, ${x2 + 60} ${y1 - 54}, ${x2} ${y2}`
    : `M${x1} ${y1} L ${x2} ${y2}`;
  return (
    <g data-testid="conduit" data-kind={edge.kind} data-state={edge.state} data-strategy={bent ? "replay" : undefined}>
      <path
        className={flowConduit({ state: conduitState(edge.state) })}
        d={d}
        pathLength={100}
        // arrow tip only on an ACTION (running flow); a nominal/static line is just a connection
        markerEnd={edge.state === "running" ? "url(#er-chev)" : undefined}
      >
        <title>{edge.label}{edge.detail ? ` — ${edge.detail}` : ""}{bent ? " — replay (around parallel work)" : ""}</title>
      </path>
      {edge.state === "running" ? (
        <circle
          className={flowPacket}
          r={4}
          data-testid="conduit-packet"
          style={{ offsetPath: `path('${d}')`, animation: "pktRun 1.4s linear infinite" }}
        />
      ) : null}
    </g>
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
      <rect className={attnBadge} x={958} y={10} width={172} height={24} rx={5} />
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
      <rect className={stopBar} x={cx - 64} y={cy - 13} width={128} height={26} rx={4} />
      <text className={stopText} x={cx} y={cy + 4} textAnchor="middle">⛔ STOP · CONFLICT</text>
    </g>
  );
}

// Lane annotation flag (podstage.html #ledger / #hist): a small toned plate labelling a landing lane.
function LaneFlag({ x, y, w, h, label, tone, testid }: {
  x: number; y: number; w: number; h: number; label: string;
  tone: "ledger" | "historical"; testid: string;
}) {
  return (
    <g data-testid={testid}>
      <rect className={laneFlag({ tone })} x={x} y={y} width={w} height={h} rx={3} />
      <text className={laneFlagText({ tone })} x={x + w / 2} y={y + h / 2 + 5} textAnchor="middle">{label}</text>
    </g>
  );
}

// T13 — closeout train (5h H2): the known closeout order plays as a derived left-to-right strip on
// closeout-pending (5f §9 allows deriving the fixed order). Each beat group sweeps in via `closeoutSweep`
// with a per-beat delay; the global effects=off freeze settles it to the all-done strip. aria-hidden —
// the derived order is observability, not live status (which stays in the diagnostics panel).
const CLOSEOUT_BEATS = ["code", "onboard", "quality", "memory", "ledger"] as const;
function CloseoutTrain({ x, y }: { x: number; y: number }) {
  const bw = 60;
  const gap = 8;
  const railEnd = x + CLOSEOUT_BEATS.length * (bw + gap) - gap;
  return (
    <g data-testid="closeout-train" aria-hidden="true">
      <text className={closeoutTrainLabel} x={x} y={y - 6}>closeout order ▸</text>
      <line className={closeoutRail} x1={x} y1={y + 11} x2={railEnd} y2={y + 11} />
      {CLOSEOUT_BEATS.map((beat, i) => {
        const bx = x + i * (bw + gap);
        return (
          <g key={beat} className={closeoutBeatG} style={{ animationDelay: `${i * 0.28}s` }}>
            <rect className={closeoutBeat} x={bx} y={y} width={bw} height={22} rx={4} />
            <text className={closeoutBeatLabel} x={bx + bw / 2} y={y + 15} textAnchor="middle">{beat}</text>
          </g>
        );
      })}
    </g>
  );
}

// --- 5h H3: remote/PR strip beyond the official line (T15 code PR+push, T16 carryover) -----------
// The upstream the official line reports into. Rendered in canonical D3→D4 order (code first: feat →
// PR → main; memory after: mem-main) regardless of feed order, so the governed sequence reads in a
// single frozen frame: mem-main stays dashed/"planned" until the code PR merges, then settles done.
const REMOTE_ORDER = ["origin-feat", "pr", "origin-main", "origin-mem-main"] as const;
const REMOTE_X = 250;
const REMOTE_Y = 56;
const REMOTE_W = 168; // chip width — a peer of the 180-wide branch nodes, so the label reads at the same scale
const REMOTE_H = 46; // two comfortable text lines (label + state), not two cramped ones
const REMOTE_GAP = 30; // breathing room + the connector run between chips
const REMOTE_GROUP_GAP = 36; // extra gap before origin/mem-main — the code→memory carryover handoff (T16)
// The strip is the SUCCESSFUL-LANDING arc — it shows only while an enclosure is actually retiring to the
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

function RemoteChip({ x, refNode }: { x: number; refNode: LandingRefNode }) {
  const tone = remoteTone(refNode);
  return (
    <g data-testid="remote-chip" data-kind={refNode.kind} data-tone={tone}>
      <title>{`${refNode.label} · ${refNode.detail ?? refNode.state}`}</title>
      <rect className={remoteChip({ tone })} x={x} y={REMOTE_Y} width={REMOTE_W} height={REMOTE_H} rx={7} />
      <text className={remoteChipLabel({ tone })} x={x + REMOTE_W / 2} y={REMOTE_Y + 20} textAnchor="middle">
        {truncate(refNode.label, 18)}
      </text>
      <text className={remoteChipState({ tone })} x={x + REMOTE_W / 2} y={REMOTE_Y + 37} textAnchor="middle">
        {truncate(remoteStateWord(refNode), 18)}
      </text>
    </g>
  );
}

function PrBadge({ x, refNode }: { x: number; refNode: LandingRefNode }) {
  const state = refNode.state === "merged" ? "merged" : "open";
  const sub = state === "merged" ? "merged" : refNode.state;
  return (
    <g data-testid="pr-badge" data-state={state}>
      <title>{refNode.detail ? `${refNode.label} · ${sub} · ${refNode.detail}` : `${refNode.label} · ${sub}`}</title>
      <rect className={prBadge({ state })} x={x} y={REMOTE_Y} width={REMOTE_W} height={REMOTE_H} rx={REMOTE_H / 2} />
      <text className={prBadgeLabel({ state })} x={x + REMOTE_W / 2} y={REMOTE_Y + 20} textAnchor="middle">
        {truncate(refNode.label, 16)}
      </text>
      <text className={prBadgeSub({ state })} x={x + REMOTE_W / 2} y={REMOTE_Y + 37} textAnchor="middle">
        {truncate(sub, 18)}
      </text>
    </g>
  );
}

function RemoteStrip({ refs }: { refs: LandingRefNode[] }) {
  const ordered = REMOTE_ORDER.map((kind) => refs.find((ref) => ref.kind === kind)).filter(
    (ref): ref is LandingRefNode => Boolean(ref),
  );
  if (!ordered.length) return null;
  const xOf = (i: number) =>
    REMOTE_X + i * (REMOTE_W + REMOTE_GAP) + (ordered[i].kind === "origin-mem-main" ? REMOTE_GROUP_GAP : 0);
  const midY = REMOTE_Y + REMOTE_H / 2;
  // Centre the header in the clear gap between the OFFICIAL LINE / WORKTREE ENCLOSURE corner labels.
  const headerX = (REMOTE_X + xOf(ordered.length - 1) + REMOTE_W) / 2;
  return (
    <g data-testid="remote-strip">
      <text className={remoteStripHeader} x={headerX} y={REMOTE_Y - 14} textAnchor="middle">
        remote ▸ landing
      </text>
      {/* connectors under the chips: solid amber wires the code chain (feat→PR→main), dashed for the
          code→memory carryover handoff into origin/mem-main */}
      {ordered.slice(1).map((ref, i) => (
        <line
          key={`conn-${ref.kind}`}
          data-testid="remote-connector"
          className={ref.kind === "origin-mem-main" ? remoteConnectorCarry : remoteConnector}
          x1={xOf(i) + REMOTE_W}
          y1={midY}
          x2={xOf(i + 1)}
          y2={midY}
        />
      ))}
      {ordered.map((ref, i) =>
        ref.kind === "pr" ? (
          <PrBadge key={ref.kind} x={xOf(i)} refNode={ref} />
        ) : (
          <RemoteChip key={ref.kind} x={xOf(i)} refNode={ref} />
        ),
      )}
    </g>
  );
}

export function EnclosureCanvas({ node, workspaceEngines = [], officialLedger }: {
  node: EngineProcessNode;
  workspaceEngines?: ProviderNode[];
  officialLedger?: LedgerNode;
}) {
  const code = node.providers.find((p) => p.role === "code");
  const memory = node.providers.find((p) => p.role === "memory");
  const hasMemory = node.memoryMode === "external" && !!node.memoryWorktree;
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
  // T14 — the official source line advances to its landing tip (read from the landing arc's source ref:
  // origin/main if the PR resolved it, else origin/<feat>). Only while a landing strategy is recorded.
  const landingSource =
    node.landing?.find((ref) => ref.kind === "origin-main") ??
    node.landing?.find((ref) => ref.kind === "origin-feat");
  // 5h H3 — show the landing strip only while the enclosure is actually retiring to the official line,
  // and only the refs the probe could resolve: a `missing` ref (probe couldn't run, e.g. gh absent)
  // carries no signal and is dropped, never rendered as an "unknown" chip.
  const landingRefs = (node.landing ?? []).filter((ref) => ref.factState !== "missing");
  const showLanding =
    landingRefs.length > 0 && (LANDING_PHASES.has(node.phase) || Boolean(node.integrationStrategy));
  return (
    <svg
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
      {hasMemory ? <rect className={enclosureBorder} x={674} y={76} width={474} height={506} rx={18} /> : null}

      {node.edges.map((edge) => <Conduit key={edge.id} edge={edge} strategy={node.integrationStrategy} />)}

      <BranchNode pos={POS.codeSource} label="Code source" refNode={node.codeSource} />
      <BranchNode pos={POS.codeWorktree} label="Code worktree" refNode={node.codeWorktree} />
      {hasMemory && node.memorySource ? (
        <BranchNode pos={POS.memorySource} label="Memory source" refNode={node.memorySource} />
      ) : null}
      {hasMemory && node.memoryWorktree ? (
        <BranchNode pos={POS.memoryWorktree} label="Memory worktree" refNode={node.memoryWorktree} />
      ) : null}

      {/* Official-line (left world): the workspace engines + their wiring + the official code↔memory
          coupler, ported from podstage.html (m-cgc / m-grep / w-m-* / cpl-main). Real providers. */}
      {officialCode ? <line className={officialWire} x1={135} y1={198} x2={300} y2={281} data-testid="official-wire" /> : null}
      {officialMemory && hasMemory ? <line className={officialWire} x1={135} y1={452} x2={300} y2={403} data-testid="official-wire" /> : null}
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

      <EngineGauge at={ENGINE.cgc} label="CGC" runtime={runtimeState(code?.runtimeState)} reindex={node.seedFallback} />
      {hasMemory ? <EngineGauge at={ENGINE.grepai} label="GrepAI" runtime={runtimeState(memory?.runtimeState)} /> : null}

      <WarpCoupler
        x={COUPLER_X}
        bound={hasMemory}
        label={`${short(node.codeWorktree.commit)} ⇄ ${short(node.memoryWorktree?.commit)}`}
        rows={node.ledgerRows}
        total={node.ledgerRowCount}
        currentCode={node.codeWorktree.commit ?? undefined}
      />

      {/* Lane annotations (podstage.html #ledger / #hist): the worktree landing lane + a historical
          contract marker. Descriptive lane labels; the live status stays in the diagnostics panel. */}
      {hasMemory ? <LaneFlag x={730} y={476} w={140} h={24} label="ledger ▸ maps merge" tone="ledger" testid="lane-ledger" /> : null}
      {node.phase === "abandoned" ? <LaneFlag x={300} y={560} w={180} h={26} label="contract · historical" tone="historical" testid="lane-historical" /> : null}

      {/* 5h H2 — the landing arc: the closeout train (T13) on closeout-pending, and the official source
          line advancing to its landing tip (T14). */}
      {node.phase === "closeout-pending" ? <CloseoutTrain x={700} y={508} /> : null}
      {node.integrationStrategy && landingSource ? (
        <LaneFlag
          x={300}
          y={216}
          w={180}
          h={20}
          label={`▸ ${landingSource.label} · ${landingSource.state}`}
          tone="ledger"
          testid="lane-landing-source"
        />
      ) : null}

      {/* 5h H3 — the remote/PR strip beyond the official line: T15 code PR+push (origin/feat → PR →
          origin/main) then T16 carryover (origin/mem-main), in the governed code-first order. Shown only
          while the enclosure is landing, with the unresolved (`missing`) probe refs dropped. */}
      {showLanding ? <RemoteStrip refs={landingRefs} /> : null}

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

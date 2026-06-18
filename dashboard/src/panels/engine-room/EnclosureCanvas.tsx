// The Engine Room pod-stage bird's-eye (5g G1): the live EngineProcessNode rendered as the
// two-world canvas from the design prototype (dashboard/public/_proto/podstage.html) — official
// line (left) <-> worktree enclosure (right), podracer engine gauges, the warp coupler, and the
// flow conduits. G1 is the STATIC frame (the nominal end-state); the boot/failure choreography
// (draw-on, travelling packets, center-out fill, gates) is G2+. Geometry is ported from the
// prototype's viewBox (0 0 1200 660). State always comes from the model (factState / runtimeState /
// edge.state), never a class name alone — so the truth stays in the projection, not the render.

import type {
  CommitRefNode,
  EngineProcessEdge,
  EngineProcessNode,
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
  officialWire,
  reasonBadge,
  reasonDot,
  reasonText,
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
  warpCouplerNode,
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
  "cgc-seed": [900, 281, 1055, 150],
  "grepai-clone": [900, 403, 1055, 500],
  sync: [480, 289, 698, 289],
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
        [-8, 26, -2, 22], [-8, 48, -2, 48], [-8, 70, -2, 74],
        [ENGINE.w + 2, 26, ENGINE.w + 8, 22], [ENGINE.w + 2, 48, ENGINE.w + 8, 48], [ENGINE.w + 2, 70, ENGINE.w + 8, 74],
      ].map(([x1, y1, x2, y2], i) => (
        <line className={enginePetal({ runtimeState: runtime })} key={i} x1={x1} y1={y1} x2={x2} y2={y2} />
      ))}
      <text className={engineGaugeLabel} x={ENGINE.w / 2} y={ENGINE.h + 18} textAnchor="middle">{label}</text>
    </g>
  );
}

function WarpCoupler({ x, bound, label, testid = "warp-coupler" }: {
  x: number;
  bound: boolean;
  label?: string;
  testid?: string;
}) {
  return (
    <g className={warpCouplerG({ bound })} data-testid={testid} data-bound={bound}>
      <line className={warpCouplerBar} x1={x} y1={312} x2={x} y2={372} />
      <rect className={warpCouplerNode} x={x - 7} y={335} width={14} height={14} rx={3} />
      {label ? <text className={warpCouplerLabel} x={x + 14} y={346}>{label}</text> : null}
    </g>
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
      <path className={flowConduit({ state: conduitState(edge.state) })} d={d} pathLength={100} markerEnd="url(#er-chev)">
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

export function EnclosureCanvas({ node, workspaceEngines = [] }: {
  node: EngineProcessNode;
  workspaceEngines?: ProviderNode[];
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
    node.landing.find((ref) => ref.kind === "origin-main") ??
    node.landing.find((ref) => ref.kind === "origin-feat");
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
        <marker id="er-chev" viewBox="0 0 10 10" refX="6.5" refY="5" markerWidth="9" markerHeight="9" orient="auto">
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
      {officialCode ? <line className={officialWire} x1={135} y1={185} x2={300} y2={252} data-testid="official-wire" /> : null}
      {officialMemory && hasMemory ? <line className={officialWire} x1={135} y1={466} x2={300} y2={432} data-testid="official-wire" /> : null}
      {officialCode ? <EngineGauge at={ENGINE.mcgc} label="CGC" runtime={runtimeState(engineState(officialCode))} /> : null}
      {officialMemory ? <EngineGauge at={ENGINE.mgrep} label="GrepAI" runtime={runtimeState(engineState(officialMemory))} /> : null}
      {hasMemory ? <WarpCoupler x={OFFICIAL_COUPLER_X} bound={hasMemory} testid="warp-coupler-official" /> : null}

      <EngineGauge at={ENGINE.cgc} label="CGC" runtime={runtimeState(code?.runtimeState)} reindex={node.seedFallback} />
      {hasMemory ? <EngineGauge at={ENGINE.grepai} label="GrepAI" runtime={runtimeState(memory?.runtimeState)} /> : null}

      <WarpCoupler x={COUPLER_X} bound={hasMemory} label={`contract · ${node.taskId}`} />

      {/* Lane annotations (podstage.html #ledger / #hist): the worktree landing lane + a historical
          contract marker. Descriptive lane labels; the live status stays in the diagnostics panel. */}
      {hasMemory ? <LaneFlag x={730} y={476} w={140} h={24} label="ledger ▸ maps merge" tone="ledger" testid="lane-ledger" /> : null}
      {node.phase === "abandoned" ? <LaneFlag x={300} y={560} w={180} h={26} label="contract · historical" tone="historical" testid="lane-historical" /> : null}

      {/* 5h H2 — the landing arc: the closeout train (T13) on closeout-pending, and the official source
          line advancing to its landing tip (T14). The full remote/PR strip + carryover packet is H3. */}
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

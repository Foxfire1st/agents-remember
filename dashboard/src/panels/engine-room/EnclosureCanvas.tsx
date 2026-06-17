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
} from "../../types/projection";
import {
  enclosureBorder,
  engineCharge,
  engineDiv,
  engineGaugeLabel,
  engineGaugeOut,
  flowConduit,
  sceneSvg,
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

// --- geometry (ported 1:1 from podstage.html) --------------------------------
const NODE_H = 62;
const POS = {
  codeSource: { x: 300, y: 250, w: 180 },
  memorySource: { x: 300, y: 372, w: 180 },
  codeWorktree: { x: 700, y: 250, w: 200 },
  memoryWorktree: { x: 700, y: 372, w: 200 },
} as const;
const ENGINE = { cgc: { x: 1057, y: 102 }, grepai: { x: 1057, y: 452 }, w: 54, h: 96 } as const;
const COUPLER_X = 800;

// Flow-conduit endpoints by edge kind, anchored to node/engine edges so a line never crosses a box.
const EDGE_GEOM: Record<string, readonly [number, number, number, number]> = {
  "worktree-add": [480, 281, 698, 281],
  "ledger-map": [480, 403, 698, 403],
  "cgc-seed": [900, 281, 1055, 150],
  "grepai-clone": [900, 403, 1055, 500],
  sync: [480, 289, 698, 289],
};

function BranchNode({ pos, label, refNode }: {
  pos: { x: number; y: number; w: number };
  label: string;
  refNode: CommitRefNode;
}) {
  const cx = pos.x + pos.w / 2;
  return (
    <g data-testid="branch-node" data-fact={refNode.factState}>
      <rect className={svgNodeBox({ factState: refNode.factState })} x={pos.x} y={pos.y} width={pos.w} height={NODE_H} rx={8} />
      <text className={svgNodeLabel} x={cx} y={pos.y + 17} textAnchor="middle">{label}</text>
      <text className={svgNodeTitle} x={cx} y={pos.y + 36} textAnchor="middle">{refNode.branch ?? "—"}</text>
      {refNode.commit ? (
        <text className={svgNodeMeta} x={cx} y={pos.y + 52} textAnchor="middle">
          @{refNode.commit.slice(0, 8)}
          {refNode.dirty ? " · dirty" : ""}
          {refNode.behindSource ? ` · ${refNode.behindSource} behind` : ""}
        </text>
      ) : null}
    </g>
  );
}

function EngineGauge({ at, label, runtime }: {
  at: { x: number; y: number };
  label: string;
  runtime: RuntimeState;
}) {
  return (
    <g
      transform={`translate(${at.x},${at.y})`}
      data-testid="engine-gauge"
      data-runtime={runtime}
      role="img"
      aria-label={`${label} engine ${runtime}`}
    >
      <rect className={engineGaugeOut({ runtimeState: runtime })} x={0} y={0} width={ENGINE.w} height={ENGINE.h} rx={5} />
      <rect className={engineCharge({ runtimeState: runtime })} x={2} y={2} width={ENGINE.w - 4} height={ENGINE.h - 4} rx={3} />
      {[14, 26, 38, 50, 62, 74, 86].map((y) => (
        <line className={engineDiv} key={y} x1={0} y1={y} x2={ENGINE.w} y2={y} />
      ))}
      <text className={engineGaugeLabel} x={ENGINE.w / 2} y={ENGINE.h + 18} textAnchor="middle">{label}</text>
    </g>
  );
}

function WarpCoupler({ bound, label }: { bound: boolean; label: string }) {
  return (
    <g className={warpCouplerG({ bound })} data-testid="warp-coupler" data-bound={bound}>
      <line className={warpCouplerBar} x1={COUPLER_X} y1={312} x2={COUPLER_X} y2={372} />
      <rect className={warpCouplerNode} x={COUPLER_X - 7} y={335} width={14} height={14} rx={3} />
      <text className={warpCouplerLabel} x={COUPLER_X + 14} y={346}>{label}</text>
    </g>
  );
}

function Conduit({ edge }: { edge: EngineProcessEdge }) {
  const geom = EDGE_GEOM[edge.kind];
  if (!geom) return null;
  const [x1, y1, x2, y2] = geom;
  return (
    <path
      className={flowConduit({ state: conduitState(edge.state) })}
      d={`M${x1} ${y1} L ${x2} ${y2}`}
      markerEnd="url(#er-chev)"
      data-testid="conduit"
      data-kind={edge.kind}
      data-state={edge.state}
    >
      <title>{edge.label}{edge.detail ? ` — ${edge.detail}` : ""}</title>
    </path>
  );
}

export function EnclosureCanvas({ node }: { node: EngineProcessNode }) {
  const code = node.providers.find((p) => p.role === "code");
  const memory = node.providers.find((p) => p.role === "memory");
  const hasMemory = node.memoryMode === "external" && !!node.memoryWorktree;
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

      <text className={worldLabel} x={55} y={40}>Official line · workspace</text>
      <text className={worldLabel} x={930} y={40}>Worktree enclosure</text>
      {hasMemory ? <rect className={enclosureBorder} x={674} y={76} width={474} height={506} rx={18} /> : null}

      {node.edges.map((edge) => <Conduit key={edge.id} edge={edge} />)}

      <BranchNode pos={POS.codeSource} label="Code source" refNode={node.codeSource} />
      <BranchNode pos={POS.codeWorktree} label="Code worktree" refNode={node.codeWorktree} />
      {hasMemory && node.memorySource ? (
        <BranchNode pos={POS.memorySource} label="Memory source" refNode={node.memorySource} />
      ) : null}
      {hasMemory && node.memoryWorktree ? (
        <BranchNode pos={POS.memoryWorktree} label="Memory worktree" refNode={node.memoryWorktree} />
      ) : null}

      <EngineGauge at={ENGINE.cgc} label="CGC" runtime={runtimeState(code?.runtimeState)} />
      {hasMemory ? <EngineGauge at={ENGINE.grepai} label="GrepAI" runtime={runtimeState(memory?.runtimeState)} /> : null}

      <WarpCoupler bound={hasMemory} label={`contract · ${node.taskId}`} />

      {!hasMemory ? (
        <text className={svgNodeMeta} x={930} y={420} textAnchor="middle" data-testid="memory-lane-absent">
          memory: {node.memoryMode} — no external lane
        </text>
      ) : null}
    </svg>
  );
}

import type { RefObject } from "react";
import { AnimatePresence, motion } from "motion/react";

import { engineState } from "../../data/selectors";
import type {
  EngineProcessEdge,
  EngineProcessNode,
  LandingRefNode,
  LedgerNode,
  ProviderBootNode,
  ProviderNode,
} from "../../types/projection";
import {
  Attention,
  CanopyFrame,
  CloseoutTrain,
  FleetingEnclosure,
  Gate,
  LaneFlag,
  MovedBadge,
  NodeBlock,
  ProviderBlock,
  ReasonBadge,
  RecoveryChips,
  RefusedConduit,
  TerminalStop,
} from "./badges";
import { Conduit, LandingFlows } from "./conduits";
import { BranchNode, EngineGauge } from "./engines";
import { EngineFxOverlay } from "./EngineFxOverlay";
import {
  COL_MAIN_CX,
  COL_WT_CX,
  COUPLER_X,
  ENGINE,
  OFFICIAL_COUPLER_X,
  POS,
  alertProps,
  runtimeState,
  short,
  type RuntimeState,
} from "./geometry";
import { WarpCoupler } from "./ledger";
import { RemoteStrip } from "./remote";
import type { EnclosureScene } from "./scene";
import {
  enclosureBorder,
  engineDropout,
  officialWire,
  scanRing,
  svgNodeMeta,
  worktreeWire,
  worldLabel,
} from "./styles";

function wireMotion(animate: boolean, visible: boolean) {
  return {
    initial: animate ? { opacity: visible ? 0.8 : 0 } : false,
    animate: { opacity: visible ? 0.8 : 0 },
    transition: {
      duration: animate ? 0.45 : 0,
      delay: animate ? (visible ? 0 : 0.2) : 0,
    },
  };
}

export function SceneHeader() {
  return (
    <>
      <defs>
        {/* refX sits at the chevron's VISUAL tip (geom apex 8.5 + the round join's ~1.1) so the
            arrowhead lands ON the line end, never overshooting past it into the target engine/box. */}
        <marker
          id="er-chev"
          viewBox="0 0 10 10"
          refX="9.6"
          refY="5"
          markerWidth="9"
          markerHeight="9"
          orient="auto"
        >
          <path
            d="M1.5 1 L8.5 5 L1.5 9"
            fill="none"
            stroke="context-stroke"
            strokeWidth="2.2"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </marker>
      </defs>
      <CanopyFrame />
      <text className={worldLabel} x={55} y={40}>Official line · workspace</text>
      <text className={worldLabel} x={930} y={40}>Worktree enclosure</text>
    </>
  );
}

export function EnclosureShell({
  animate,
  hasMemory,
  fleeting,
  codeWtMaterialised,
}: {
  animate: boolean;
  hasMemory: boolean;
  fleeting: boolean;
  codeWtMaterialised: boolean;
}) {
  if (!hasMemory || fleeting) return null;
  return (
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
  );
}

export function ConduitLayer({
  node,
  retiring,
  memGated,
  memSyncMoved,
}: {
  node: EngineProcessNode;
  retiring: boolean;
  memGated: boolean;
  memSyncMoved: boolean;
}) {
  return (
    <>
      {node.edges.map((edge) => (
        <Conduit
          key={edge.id}
          edge={edge}
          strategy={node.integrationStrategy}
          retiring={retiring}
          ghosted={(memGated || memSyncMoved) && edge.kind === "ledger-map"}
        />
      ))}
      {/* NB: the scan ring is NOT drawn here — it is centred ON a repository node, so it must paint in the
          topmost overlay layer (below), after the nodes, or the node's opaque rect would cover it. */}
    </>
  );
}

export function BranchTier({
  node,
  hasMemory,
  showLanding,
  animate,
  retiring,
  baseStale,
}: {
  node: EngineProcessNode;
  hasMemory: boolean;
  showLanding: boolean;
  animate: boolean;
  retiring: boolean;
  baseStale: boolean;
}) {
  return (
    <>
      {/* THREE-TIER: the official line is the resolved integration/source branch from the
          projection. The worktree forks from it on the right; during landing, the source tier appears
          in the gap so the closeout path reads integration ◂ source ◂ worktree. */}
      <BranchNode pos={POS.codeSource} label="Integration line" refNode={node.codeSource} pruned={baseStale} />
      {hasMemory && node.memorySource ? (
        <BranchNode pos={POS.memorySource} label="Integration line" refNode={node.memorySource} />
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
    </>
  );
}

function OfficialWires({
  officialCode,
  officialMemory,
  hasMemory,
}: {
  officialCode: ProviderNode | undefined;
  officialMemory: ProviderNode | undefined;
  hasMemory: boolean;
}) {
  return (
    <>
      {officialCode ? (
        <line className={officialWire} x1={135} y1={198} x2={COL_MAIN_CX - 90} y2={281} data-testid="official-wire" />
      ) : null}
      {officialMemory && hasMemory ? (
        <line className={officialWire} x1={135} y1={452} x2={COL_MAIN_CX - 90} y2={403} data-testid="official-wire" />
      ) : null}
    </>
  );
}

function OfficialGauges({
  officialCode,
  officialMemory,
}: {
  officialCode: ProviderNode | undefined;
  officialMemory: ProviderNode | undefined;
}) {
  return (
    <>
      {officialCode ? (
        <EngineGauge at={ENGINE.mcgc} label="CGC" runtime={runtimeState(engineState(officialCode))} />
      ) : null}
      {officialMemory ? (
        <EngineGauge at={ENGINE.mgrep} label="GrepAI" runtime={runtimeState(engineState(officialMemory))} />
      ) : null}
    </>
  );
}

function OfficialCoupler({
  node,
  hasMemory,
  officialLedger,
}: {
  node: EngineProcessNode;
  hasMemory: boolean;
  officialLedger?: LedgerNode;
}) {
  if (!hasMemory) return null;
  return (
    <WarpCoupler
      x={OFFICIAL_COUPLER_X}
      bound={hasMemory}
      testid="warp-coupler-official"
      label={`${short(node.codeSource.commit)} ⇄ ${short(node.memorySource?.commit)}`}
      rows={officialLedger?.rows}
      total={officialLedger?.closeoutCount}
      currentCode={node.codeSource.commit ?? undefined}
    />
  );
}

export function OfficialLineLayer({
  node,
  officialCode,
  officialMemory,
  hasMemory,
  officialLedger,
}: {
  node: EngineProcessNode;
  officialCode: ProviderNode | undefined;
  officialMemory: ProviderNode | undefined;
  hasMemory: boolean;
  officialLedger?: LedgerNode;
}) {
  return (
    <>
      {/* Official-line (left world): the workspace engines + their wiring + the official code↔memory
          coupler, ported from podstage.html (m-cgc / m-grep / w-m-* / cpl-main). Real providers. */}
      <OfficialWires officialCode={officialCode} officialMemory={officialMemory} hasMemory={hasMemory} />
      <OfficialGauges officialCode={officialCode} officialMemory={officialMemory} />
      <OfficialCoupler node={node} hasMemory={hasMemory} officialLedger={officialLedger} />
    </>
  );
}

function WorktreeWires({
  code,
  memory,
  hasMemory,
  animate,
}: {
  code: ProviderBootNode | undefined;
  memory: ProviderBootNode | undefined;
  hasMemory: boolean;
  animate: boolean;
}) {
  return (
    <>
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
        {...wireMotion(animate, !!code)}
      />
      {hasMemory ? (
        <motion.line
          className={worktreeWire}
          x1={1057}
          y1={452}
          x2={COL_WT_CX + 100}
          y2={403}
          data-testid="worktree-wire"
          {...wireMotion(animate, !!memory)}
        />
      ) : null}
    </>
  );
}

export function WorktreeEngineLayer({
  node,
  code,
  memory,
  hasMemory,
  codeRuntimePredicted,
  memoryRuntimePredicted,
  memWtMaterialised,
}: {
  node: EngineProcessNode;
  code: ProviderBootNode | undefined;
  memory: ProviderBootNode | undefined;
  hasMemory: boolean;
  codeRuntimePredicted: RuntimeState;
  memoryRuntimePredicted: RuntimeState;
  memWtMaterialised: boolean;
}) {
  return (
    <>
      <WorktreeWires code={code} memory={memory} hasMemory={hasMemory} animate />
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
    </>
  );
}

export function LaneFlagLayer({
  hasMemory,
  memWtMaterialised,
  retiring,
  cleanupTip,
}: {
  hasMemory: boolean;
  memWtMaterialised: boolean;
  retiring: boolean;
  cleanupTip: LandingRefNode | undefined;
}) {
  return (
    <>
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
      {retiring ? (
        <LaneFlag
          x={300}
          y={560}
          w={180}
          h={26}
          label="contract · historical"
          tone="historical"
          testid="lane-historical"
        />
      ) : null}
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
    </>
  );
}

export function LandingDockLayer({
  showLanding,
  animate,
  retiring,
  landingRefs,
}: {
  showLanding: boolean;
  animate: boolean;
  retiring: boolean;
  landingRefs: LandingRefNode[];
}) {
  return (
    <AnimatePresence>
      {showLanding ? (
        <motion.g
          key="landing-dock"
          initial={animate ? { opacity: 0 } : false}
          // Retract the whole landing tier (origin chips + the push/pull/carry/push-mem flows) as
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
  );
}

export function CloseoutTrainLayer({
  node,
  animate,
}: {
  node: EngineProcessNode;
  animate: boolean;
}) {
  return (
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
  );
}

export function MemoryLaneAbsent({
  hasMemory,
  node,
}: {
  hasMemory: boolean;
  node: EngineProcessNode;
}) {
  if (hasMemory) return null;
  return (
    <text className={svgNodeMeta} x={930} y={420} textAnchor="middle" data-testid="memory-lane-absent">
      memory: {node.memoryMode} — no external lane
    </text>
  );
}

export function FleetingOverlay({
  fleeting,
  summary,
  recovery,
}: {
  fleeting: boolean;
  summary: string;
  recovery: string[];
}) {
  return (
    <AnimatePresence>
      {fleeting ? (
        <FleetingEnclosure key="fleeting-enclosure" summary={summary} choices={recovery} />
      ) : null}
    </AnimatePresence>
  );
}

export function RefusedOverlay({
  fleeting,
  refusedEdges,
}: {
  fleeting: boolean;
  refusedEdges: EnclosureScene["refusedEdges"];
}) {
  if (fleeting || refusedEdges.length === 0) return null;
  return (
    <g data-testid="refused-flash">
      {refusedEdges.map(({ edge, polarity }) => (
        <RefusedConduit key={`refused-${edge.id}`} edge={edge} polarity={polarity} />
      ))}
    </g>
  );
}

export function StopAttentionOverlay({
  animate,
  fleeting,
  terminalEdge,
  terminal,
  blocked,
  recovery,
}: {
  animate: boolean;
  fleeting: boolean;
  terminalEdge: EngineProcessEdge | undefined;
  terminal: boolean;
  blocked: boolean;
  recovery: string[];
}) {
  return (
    <>
      {/* refused-conduit flash (T9B red seed fault / T9C amber seed reroute / T14C red integrate
          conflict): a one-shot GSAP flash tracing the refused lane. NOT gated on `animate` — it rests at
          opacity 0 (the cva base) so it is present-but-absent under effects=off (the STOP/gate carries the
          settled state); GSAP plays the flash when effects are on. Rendered before the STOP so the steady
          STOP/gate paints over it as the conflict resolves. */}
      <AnimatePresence>
        {!fleeting && terminalEdge ? (
          <motion.g key="terminal" {...alertProps(animate)}>
            <TerminalStop edge={terminalEdge} />
          </motion.g>
        ) : null}
      </AnimatePresence>
      <AnimatePresence>
        {blocked ? (
          <motion.g key="attention" {...alertProps(animate)}>
            <Attention hidden={animate} />
          </motion.g>
        ) : null}
      </AnimatePresence>
      <AnimatePresence>
        {!fleeting && !terminal && recovery.length ? (
          <motion.g key="chips" {...alertProps(animate)}>
            <RecoveryChips labels={recovery} />
          </motion.g>
        ) : null}
      </AnimatePresence>
    </>
  );
}

export function DropoutOverlay({
  animate,
  providerPlanBlocked,
}: {
  animate: boolean;
  providerPlanBlocked: boolean;
}) {
  return (
    <AnimatePresence>
      {providerPlanBlocked ? (
        <motion.g key="engine-dropout" {...alertProps(animate)} data-testid="engine-dropout">
          <rect className={engineDropout} x={ENGINE.cgc.x - 6} y={ENGINE.cgc.y - 6} width={ENGINE.w + 12} height={ENGINE.h + 12} rx={6} />
          <rect className={engineDropout} x={ENGINE.grepai.x - 6} y={ENGINE.grepai.y - 6} width={ENGINE.w + 12} height={ENGINE.h + 12} rx={6} />
        </motion.g>
      ) : null}
    </AnimatePresence>
  );
}

export function PointerOverlays({
  animate,
  scanCenter,
  blockNode,
  providerPlanBlocked,
  fleeting,
  gatedEdges,
  reasonCenter,
  summary,
  movedAt,
}: {
  animate: boolean;
  scanCenter: EnclosureScene["scanCenter"];
  blockNode: EnclosureScene["blockNode"];
  providerPlanBlocked: boolean;
  fleeting: boolean;
  gatedEdges: EngineProcessEdge[];
  reasonCenter: EnclosureScene["reasonCenter"];
  summary: string;
  movedAt: EnclosureScene["movedAt"];
}) {
  return (
    <>
      {/* TOPMOST LAYER — the verify scan ring (centred ON the checked repository node, or AT the worktree
          engine for the T7B provider-plan verify) and the block gate + reason badge (at the node's top edge).
          Rendered dead last so the node's opaque rect can never cover them; centring a pointer on a node and
          painting it BEHIND the node defeats the pointer. The scan group fades via Motion (opacity
          only — its inner circle's r/opacity expand-fade is GSAP). */}
      <AnimatePresence>
        {scanCenter && animate ? (
          <motion.g key="scan" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} transition={{ duration: 0.25 }}>
            <circle className={scanRing} data-fx="scan" data-testid="scan-ring" cx={scanCenter.x} cy={scanCenter.y} r={6} />
          </motion.g>
        ) : null}
      </AnimatePresence>
      <AnimatePresence>
        {blockNode ? (
          <motion.g key="block" {...alertProps(animate)}>
            <NodeBlock cx={blockNode.cx} top={blockNode.top} reason={summary} />
          </motion.g>
        ) : providerPlanBlocked ? (
          <motion.g key="provider-block" {...alertProps(animate)}>
            <ProviderBlock reason={summary} />
          </motion.g>
        ) : !fleeting && gatedEdges.length ? (
          <motion.g key="edge-gate" {...alertProps(animate)}>
            {gatedEdges.map((edge) => <Gate key={`gate-${edge.id}`} edge={edge} />)}
            {reasonCenter ? (
              <ReasonBadge reason={summary} cx={reasonCenter.cx} cy={reasonCenter.cy} />
            ) : null}
          </motion.g>
        ) : null}
      </AnimatePresence>
      {/* T12B — the soft cyan "moved ▲" badge: origin/mem-main advanced while the worktree holds local
          commits (the notification BEFORE the gate). Anchored ON the memory worktree node, painted last. */}
      <AnimatePresence>
        {movedAt ? (
          <motion.g key="moved" {...alertProps(animate)}>
            <MovedBadge cx={movedAt.cx} cy={movedAt.cy} text="origin/mem-main · moved ▲" />
          </motion.g>
        ) : null}
      </AnimatePresence>
    </>
  );
}

export function FxOverlay({
  fxRef,
  animate,
  blocked,
  node,
  code,
  hasMemory,
  memWtMaterialised,
}: {
  fxRef: RefObject<SVGSVGElement | null>;
  animate: boolean;
  blocked: boolean;
  node: EngineProcessNode;
  code: ProviderBootNode | undefined;
  hasMemory: boolean;
  memWtMaterialised: boolean;
}) {
  if (!animate) return null;
  return (
    <EngineFxOverlay
      ref={fxRef}
      attention={blocked}
      engineHeight={ENGINE.h}
      engineWidth={ENGINE.w}
      reindexAt={node.seedFallback && code ? ENGINE.cgc : undefined}
      surgeXs={
        hasMemory
          ? [
              OFFICIAL_COUPLER_X,
              ...(memWtMaterialised ? [COUPLER_X] : []),
            ]
          : []
      }
    />
  );
}

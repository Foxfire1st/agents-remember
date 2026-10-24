// The Engine Room pod-stage bird's-eye: the live EngineProcessNode rendered as the
// two-world canvas from the design prototype (dashboard/public/_proto/podstage.html) — official
// line (left) <-> worktree enclosure (right), the warp couplers, and the flow conduits. This is the
// STATIC frame (the nominal end-state); the boot/failure choreography (draw-on, travelling packets,
// gates) is layered on separately. Geometry is ported from the prototype's viewBox (0 0 1200 660).
// State always comes from the model (factState / edge.state), never a class name alone. The leaf pieces live in
// geometry.ts (pure rules), badges.tsx, engines.tsx, ledger.tsx, conduits.tsx, and remote.tsx;
// derived state lives in scene.ts and the render layers in sceneLayers.tsx.
import { useRef } from "react";

import type { EngineProcessNode, LedgerNode } from "../../types/projection";
import { isBlocked } from "./geometry";
import { resolveScene } from "./scene";
import {
  BranchTier,
  CloseoutTrainLayer,
  ConduitLayer,
  EnclosureShell,
  FleetingOverlay,
  FxOverlay,
  LandingDockLayer,
  LaneFlagLayer,
  MemoryLaneAbsent,
  OfficialLineLayer,
  PointerOverlays,
  RefusedOverlay,
  SceneHeader,
  StopAttentionOverlay,
  WorktreeEngineLayer,
} from "./sceneLayers";
import { sceneSvg } from "./styles";
import { useEngineTimeline } from "./useEngineTimeline";
import { useShouldAnimate } from "./useShouldAnimate";

export function EnclosureCanvas({
  node,
  officialLedger,
}: {
  node: EngineProcessNode;
  officialLedger?: LedgerNode;
}) {
  const animate = useShouldAnimate();
  // GSAP owns the strokeDashoffset draw-ons ([data-draw='on']) + the repeating fx ([data-fx=…]) as one
  // gsap.context scoped to this <svg> root; Motion (below) owns opacity/transform/scaleY/fill + enter/exit;
  // CSS is static. The hook self-gates on useShouldAnimate (no context, no ticker, under effects=off).
  const rootRef = useRef<SVGSVGElement>(null);
  const fxRootRef = useRef<SVGSVGElement>(null);
  useEngineTimeline(rootRef, node, fxRootRef);
  const scene = resolveScene(node);

  return (
    <>
      <svg
        ref={rootRef}
        className={sceneSvg}
        viewBox="0 0 1200 660"
        preserveAspectRatio="xMidYMid meet"
        role="img"
        aria-label={`Engine room — ${node.leafId || node.taskName} — ${node.health}`}
        data-testid="enclosure-canvas"
      >
        <SceneHeader />
        <EnclosureShell animate={animate} hasMemory={scene.hasMemory} fleeting={scene.fleeting} codeWtMaterialised={scene.codeWtMaterialised} />
        <ConduitLayer node={node} retiring={scene.retiring} memGated={scene.memGated} memSyncMoved={scene.memSyncMoved} />
        <BranchTier node={node} hasMemory={scene.hasMemory} showLanding={scene.showLanding} animate={animate} retiring={scene.retiring} baseStale={scene.baseStale} />
        <OfficialLineLayer node={node} hasMemory={scene.hasMemory} officialLedger={officialLedger} />
        <WorktreeEngineLayer node={node} hasMemory={scene.hasMemory} memWtMaterialised={scene.memWtMaterialised} />
        <LaneFlagLayer hasMemory={scene.hasMemory} memWtMaterialised={scene.memWtMaterialised} retiring={scene.retiring} cleanupTip={scene.cleanupTip} />
        <CloseoutTrainLayer node={node} animate={animate} />
        <LandingDockLayer showLanding={scene.showLanding} animate={animate} retiring={scene.retiring} landingRefs={scene.landingRefs} />
        <MemoryLaneAbsent hasMemory={scene.hasMemory} node={node} />
        <FleetingOverlay fleeting={scene.fleeting} summary={node.summary} recovery={scene.recovery} />
        <RefusedOverlay fleeting={scene.fleeting} refusedEdges={scene.refusedEdges} />
        <StopAttentionOverlay animate={animate} fleeting={scene.fleeting} terminalEdge={scene.terminalEdge} terminal={scene.terminal} blocked={isBlocked(node)} recovery={scene.recovery} />
        <PointerOverlays animate={animate} scanCenter={scene.scanCenter} blockNode={scene.blockNode} fleeting={scene.fleeting} gatedEdges={scene.gatedEdges} reasonCenter={scene.reasonCenter} summary={node.summary} movedAt={scene.movedAt} />
      </svg>
      <FxOverlay fxRef={fxRootRef} animate={animate} blocked={isBlocked(node)} hasMemory={scene.hasMemory} memWtMaterialised={scene.memWtMaterialised} />
    </>
  );
}

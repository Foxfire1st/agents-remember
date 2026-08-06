// The Engine Room pod-stage bird's-eye: the live EngineProcessNode rendered as the
// two-world canvas from the design prototype (dashboard/public/_proto/podstage.html) — official
// line (left) <-> worktree enclosure (right), podracer engine gauges, the warp coupler, and the
// flow conduits. This is the STATIC frame (the nominal end-state); the boot/failure choreography
// (draw-on, travelling packets, center-out fill, gates) is layered on separately. Geometry is
// ported from the prototype's viewBox (0 0 1200 660). State always comes from the model
// (factState / runtimeState / edge.state), never a class name alone. The leaf pieces live in
// geometry.ts (pure rules), badges.tsx, engines.tsx, ledger.tsx, conduits.tsx, and remote.tsx.
import { useRef } from "react";
import { AnimatePresence, motion } from "motion/react";

import { useEngineTimeline } from "./useEngineTimeline";
import { useShouldAnimate } from "./useShouldAnimate";
import { EngineFxOverlay } from "./EngineFxOverlay";
import type {
  EngineProcessEdge,
  EngineProcessNode,
  GateNode,
  LedgerNode,
  ProviderNode,
} from "../../types/projection";
import { engineState } from "../../data/selectors";
import {
  enclosureBorder,
  engineDropout,
  officialWire,
  scanRing,
  sceneSvg,
  svgNodeMeta,
  worktreeWire,
  worldLabel,
} from "./styles";
import {
  CanopyFrame,
  Gate,
  Attention,
  ReasonBadge,
  NodeBlock,
  MovedBadge,
  ProviderBlock,
  RecoveryChips,
  FleetingEnclosure,
  TerminalStop,
  RefusedConduit,
  LaneFlag,
  CloseoutTrain,
} from "./badges";
import { BranchNode, EngineGauge } from "./engines";
import { WarpCoupler } from "./ledger";
import { Conduit, LandingFlows } from "./conduits";
import { RemoteStrip } from "./remote";
import {
  COL_MAIN_CX,
  COL_WT_CX,
  COUPLER_X,
  EDGE_GEOM,
  ENGINE,
  LANDING_PHASES,
  NODE_H,
  OFFICIAL_COUPLER_X,
  POS,
  alertProps,
  isBlocked,
  refusedPolarityOf,
  runtimeState,
  short,
} from "./geometry";
export function EnclosureCanvas({ node, gateNode, workspaceEngines = [], officialLedger }: {
  node: EngineProcessNode;
  gateNode?: GateNode;
  workspaceEngines?: ProviderNode[];
  officialLedger?: LedgerNode;
}) {
  const animate = useShouldAnimate();
  // GSAP owns the strokeDashoffset draw-ons ([data-draw='on']) + the repeating fx ([data-fx=…]) as one
  // gsap.context scoped to this <svg> root; Motion (below) owns opacity/transform/scaleY/fill + enter/exit;
  // CSS is static. The hook self-gates on useShouldAnimate (no context, no ticker, under effects=off).
  const rootRef = useRef<SVGSVGElement>(null);
  const fxRootRef = useRef<SVGSVGElement>(null);
  useEngineTimeline(rootRef, node, fxRootRef);
  const code = node.providers.find((p) => p.role === "code");
  const memory = node.providers.find((p) => p.role === "memory");
  // PREDICTIVE BOOT — the clone arrow draws toward the worktree engine for ~0.6s; the engine should
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
  // T3B/T1B — the pre-block verify sweeps, all read off the projection (never a class alone). `memChecking`
  // (T3B): the memory side is verified (ledger-map running) before the ledger gate decides, while the memory
  // worktree is not yet on disk. `baseChecking` (T1B): the base/code side is preflighted (worktree-add running,
  // code worktree not yet on disk) — "is local main current with upstream?" — before the stale-base gate.
  // `scanAt` anchors the cyan scan ring at the lane under check (memory y=403 / code y=281, both on the gap
  // centre). `memGated`: the memory lane is held (no ledger map / a missing memory repo) → it ghosts while the
  // code lane stays solid. `baseStale`: local main is behind upstream and the start is blocked → the main code
  // node reads pruned/dormant (the pruned register).
  const memChecking =
    node.edges.some((edge) => edge.kind === "ledger-map" && edge.state === "running") && !memWtMaterialised;
  const baseChecking =
    node.edges.some((edge) => edge.kind === "worktree-add" && edge.state === "running") && !codeWtMaterialised;
  const memGated =
    node.memoryWorktree?.factState === "missing" ||
    node.edges.some((edge) => edge.kind === "ledger-map" && edge.state === "blocked");
  // T7B — the provider-plan block: PRE-CONTRACT, but distinct from T1B/T3B (which gate a SOURCE lane).
  // Here BOTH worktrees already materialised (observed), NO provider boot nodes exist yet (engines unlit),
  // and the runtime setup config is missing → the alarm bar sits BESIDE the worktree provider engine (the
  // barred runtime, NOT the code node — see ProviderBlock) and the engines never light. Signal off the
  // projection: blocked + setupState 'blocked' + zero providers +
  // both worktrees on disk + a provider-plan/setup-config missing fact. Derived ABOVE `fleeting` because a
  // T7B block must NOT fall into the big red FleetingEnclosure box (it shares the 'contract not yet written'
  // fact) — `fleeting` is tightened with `&& !providerPlanBlocked` below.
  const providerPlanBlocked =
    isBlocked(node) &&
    node.setupState === "blocked" &&
    node.providers.length === 0 &&
    codeWtMaterialised &&
    memWtMaterialised &&
    node.missingFacts.some((fact) => /provider (plan|setup|runtime)|setup config/i.test(fact));
  // the provider-plan VERIFY sweep (P3): setupState 'running' with no boot nodes yet (pre-contract provider
  // check) → a cyan scan ring AT the worktree CGC engine centre, not on a source lane.
  const providerChecking =
    node.setupState === "running" && node.providers.length === 0 && codeWtMaterialised && memWtMaterialised;
  const providerScanAt = providerChecking
    ? { x: ENGINE.cgc.x + ENGINE.w / 2, y: ENGINE.cgc.y + ENGINE.h / 2 }
    : null;
  // a born-blocked (pre-contract) enclosure — the reducer marks it "contract not yet written"; it renders the
  // big red FleetingEnclosure box (stale-base / pre-contract). A T7B provider-plan block carries the same
  // fact but anchors a node gate + unlit engines instead, so it is excluded here.
  const fleeting =
    node.missingFacts.some((fact) => /contract not yet written/i.test(fact)) && !providerPlanBlocked;
  // a stale-base block is the FLEETING (pre-contract) preflight case — `&& fleeting` keeps it distinct from a
  // live sync-needed block (which is also `behindSource > 0` but has a real worktree).
  const baseStale = (node.codeSource.behindSource ?? 0) > 0 && isBlocked(node) && fleeting;
  // The verify/block indicators anchor ON the checked REPOSITORY node (its rectangle), never the
  // connector lane: T1B points at the official-line CODE base (stale), T3B at the official-line MEMORY base
  // (no ledger map). `scanAt` centres the scan ring on that node; `blockNode` puts the steady gate at the
  // node's top edge + the reason badge above it, so the gate visibly points at the repository (the prototype).
  const scanAt = memChecking
    ? { x: COL_MAIN_CX, y: POS.memorySource.y + NODE_H / 2 }
    : baseChecking
      ? { x: COL_MAIN_CX, y: POS.codeSource.y + NODE_H / 2 }
      : null;
  // the topmost scan ring lights for a source-lane verify (T1B/T3B) OR the provider-plan verify-at-engine (T7B)
  const scanCenter = scanAt ?? providerScanAt;
  // T12B — a LIVE memory sync block: the memory worktree is REAL but origin/mem-main MOVED ahead
  // (memorySource.behindSource > 0) while the worktree holds local commits. `memMoved` (the soft cyan ▲
  // notification) shows BEFORE the gate (running, not yet blocked — podstage Y1: imsg, no gate). `memSyncMoved`
  // is the escalated gate beat: the memory ledger-map lane is held STEADY while the CODE lane keeps advancing.
  // Restricted to a blocked LEDGER-MAP edge (the memory lane) so it never reclassifies the code-side
  // `engine-sync-needed` gallery state (a blocked `sync` edge), which keeps its existing edge-gate rendering.
  const memMoved =
    !fleeting &&
    (node.memorySource?.behindSource ?? 0) > 0 &&
    memWtMaterialised &&
    !isBlocked(node);
  const movedAt = memMoved ? { cx: COL_WT_CX, cy: POS.memoryWorktree.y - 36 } : null;
  const memSyncMoved =
    !fleeting &&
    (node.memorySource?.behindSource ?? 0) > 0 &&
    memWtMaterialised &&
    isBlocked(node) &&
    node.edges.some((edge) => edge.kind === "ledger-map" && edge.state === "blocked");
  const blockNode = baseStale
    ? { cx: COL_MAIN_CX, top: POS.codeSource.y } // T1B — the stale official-line CODE base
    : memSyncMoved
      ? { cx: COL_WT_CX, top: POS.memoryWorktree.y } // T12B — the held memory WORKTREE (upstream moved)
      : memGated && isBlocked(node)
        ? { cx: COL_MAIN_CX, top: POS.memorySource.y } // T3B — the unmappable official-line MEMORY base
        : null;
  // T9B/T9C/T14C — the refused-conduit flash lanes (seed/integration edges that are failed or stale).
  // Polarity is derived from the edge state (refusedPolarityOf), never a class. Rendered topmost so the flash is
  // never covered; each path is a one-shot GSAP flash that rests at opacity 0 (absent) under effects=off.
  const refusedEdges = node.edges
    .map((edge) => ({ edge, polarity: refusedPolarityOf(edge) }))
    .filter((entry): entry is { edge: EngineProcessEdge; polarity: "amber" | "red" } => entry.polarity !== null);
  // Official-line (workspace) engines — the real shared CGC/GrepAI feeding the official line (left
  // world); runtime derived like the OfficialStrip so the two surfaces always agree.
  const officialCode = workspaceEngines.find((engine) => engine.role === "code");
  const officialMemory = workspaceEngines.find((engine) => engine.role === "memory");
  // failure overlays — `fleeting` is derived above (it gates `baseStale`).
  // t14c — a terminal integration conflict draws a STOP (not the recoverable Gate) and no recovery chips.
  const terminal = node.phase === "integration-blocked";
  const terminalEdge = terminal
    ? node.edges.find((e) => e.kind === "integration" && e.state === "blocked")
    : undefined;
  // blocked = STEADY gate (a choice required); failed/down = FAULT → the engine flickers, no gate.
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
  // Show the landing dock only while the enclosure is actually retiring to the official line,
  // and only the refs the probe could resolve: a `missing` ref (probe couldn't run, e.g. gh absent)
  // carries no signal and is dropped, never rendered as an "unknown" chip.
  const landingRefs = node.landing.filter((ref) => ref.factState !== "missing");
  const showLanding =
    landingRefs.length > 0 && (LANDING_PHASES.has(node.phase) || Boolean(node.integrationStrategy));
  // Cleanup teardown: a retiring enclosure (abandon OR a landed cleanup) keeps the historical
  // contract chip; on a successful cleanup the "back into main" seam reads the resolved origin-main tip.
  const retiring = node.phase === "abandoned" || node.phase === "cleanup-pending";
  const cleanupTip =
    node.phase === "cleanup-pending"
      ? node.landing.find((ref) => ref.kind === "origin-main" && ref.factState !== "missing")
      : undefined;
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
      data-gate-kind={gateNode?.kind}
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
      {hasMemory && !fleeting ? (
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

      {/* The landing arc: the closeout train (T13) on closeout-pending, and the official source
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
      {/* The remote/landing dock beyond the official line (copied from the mockup): code remotes
          at the top (origin/feat ▸ PR ▸ origin/main, reading right→left), the memory remote mirrored to
          the bottom, wired to the branch nodes by the directional landing flows (push ↑ / pull ↓ /
          push-mem ↓). Shown only while the enclosure is landing, with `missing` probe refs dropped. */}
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

      {!hasMemory ? (
        <text className={svgNodeMeta} x={930} y={420} textAnchor="middle" data-testid="memory-lane-absent">
          memory: {node.memoryMode} — no external lane
        </text>
      ) : null}

      {/* failure overlays: the big red `FleetingEnclosure` box (born-blocked, worktree footprint),
          the alarm-parity attention badge, the terminal STOP, and the bottom recovery chips. ORDER MATTERS:
          these come first, then the verify/block POINTERS render LAST (below) so they are the topmost layer —
          a pointer is centred ON / sits ABOVE a repository node and must never be covered by it. */}
      <AnimatePresence>
        {fleeting ? (
          <FleetingEnclosure key="fleeting-enclosure" summary={node.summary} choices={recovery} />
        ) : null}
      </AnimatePresence>
      {/* refused-conduit flash (T9B red seed fault / T9C amber seed reroute / T14C red integrate
          conflict): a one-shot GSAP flash tracing the refused lane. NOT gated on `animate` — it rests at
          opacity 0 (the cva base) so it is present-but-absent under effects=off (the STOP/gate carries the
          settled state); GSAP plays the flash when effects are on. Rendered before the STOP so the steady
          STOP/gate paints over it as the conflict resolves. */}
      {!fleeting && refusedEdges.length ? (
        <g data-testid="refused-flash">
          {refusedEdges.map(({ edge, polarity }) => (
            <RefusedConduit key={`refused-${edge.id}`} edge={edge} polarity={polarity} />
          ))}
        </g>
      ) : null}
      <AnimatePresence>
        {!fleeting && terminalEdge ? (
          <motion.g key="terminal" {...alertProps(animate)}>
            <TerminalStop edge={terminalEdge} />
          </motion.g>
        ) : null}
      </AnimatePresence>
      <AnimatePresence>
        {isBlocked(node) ? (
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
      {/* T7B — the unlit-engine DROPOUT halo: a static alarm dashed outline over the two worktree engine
          footprints when the provider plan is blocked (the engines never light because the runtime config is
          missing). The engines themselves stay absent (no providers → faded out), so this is the only mark
          that the slot is HELD. Rendered before the scan/gate so the gate sits over it. */}
      <AnimatePresence>
        {providerPlanBlocked ? (
          <motion.g key="engine-dropout" {...alertProps(animate)} data-testid="engine-dropout">
            <rect className={engineDropout} x={ENGINE.cgc.x - 6} y={ENGINE.cgc.y - 6} width={ENGINE.w + 12} height={ENGINE.h + 12} rx={6} />
            <rect className={engineDropout} x={ENGINE.grepai.x - 6} y={ENGINE.grepai.y - 6} width={ENGINE.w + 12} height={ENGINE.h + 12} rx={6} />
          </motion.g>
        ) : null}
      </AnimatePresence>
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
            <NodeBlock cx={blockNode.cx} top={blockNode.top} reason={node.summary} />
          </motion.g>
        ) : providerPlanBlocked ? (
          <motion.g key="provider-block" {...alertProps(animate)}>
            <ProviderBlock reason={node.summary} />
          </motion.g>
        ) : !fleeting && gatedEdges.length ? (
          <motion.g key="edge-gate" {...alertProps(animate)}>
            {gatedEdges.map((edge) => <Gate key={`gate-${edge.id}`} edge={edge} />)}
            {reasonCenter ? <ReasonBadge reason={node.summary} cx={reasonCenter.cx} cy={reasonCenter.cy} /> : null}
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
      </svg>
      {animate ? (
        <EngineFxOverlay
          ref={fxRootRef}
          attention={isBlocked(node)}
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
      ) : null}
    </>
  );
}

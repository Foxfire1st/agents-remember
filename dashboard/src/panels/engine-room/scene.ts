import type {
  EngineProcessEdge,
  EngineProcessNode,
  LandingRefNode,
} from "../../types/projection";
import {
  COL_MAIN_CX,
  COL_WT_CX,
  EDGE_GEOM,
  LANDING_PHASES,
  NODE_H,
  POS,
  isBlocked,
  refusedPolarityOf,
} from "./geometry";

export interface EnclosureScene {
  hasMemory: boolean;
  codeWtMaterialised: boolean;
  memWtMaterialised: boolean;
  memChecking: boolean;
  baseChecking: boolean;
  memGated: boolean;
  fleeting: boolean;
  baseStale: boolean;
  scanAt: { x: number; y: number } | null;
  scanCenter: { x: number; y: number } | null;
  memMoved: boolean;
  movedAt: { cx: number; cy: number } | null;
  memSyncMoved: boolean;
  blockNode: { cx: number; top: number } | null;
  refusedEdges: { edge: EngineProcessEdge; polarity: "amber" | "red" }[];
  terminal: boolean;
  terminalEdge: EngineProcessEdge | undefined;
  gatedEdges: EngineProcessEdge[];
  firstGated: readonly [number, number, number, number] | undefined;
  stopGeom: readonly [number, number, number, number] | undefined;
  reasonCenter: { cx: number; cy: number } | undefined;
  recovery: string[];
  landingRefs: LandingRefNode[];
  showLanding: boolean;
  retiring: boolean;
  cleanupTip: LandingRefNode | undefined;
}

function ledgerMapBlocked(node: EngineProcessNode): boolean {
  return node.edges.some(
    (edge) => edge.kind === "ledger-map" && edge.state === "blocked",
  );
}

export function resolveEngines(node: EngineProcessNode) {
  // The memory-lane presence (external memory worktree) drives the scene.
  const hasMemory = node.memoryMode === "external" && !!node.memoryWorktree;
  return {
    hasMemory,
  };
}

export function resolveMaterialisation(node: EngineProcessNode) {
  // Build-up materialisation gates (the honesty axis): the enclosure shell + the worktree coupler only
  // appear once the matching worktree ref is observed on disk. At main-only B0 these are all
  // `planned`/absent → faded out, so the left world stands alone and the enclosure assembles as the
  // projection advances.
  const codeWtMaterialised =
    node.codeWorktree.factState === "observed" ||
    node.codeWorktree.factState === "derived";
  const memWtMaterialised =
    node.memoryWorktree?.factState === "observed" ||
    node.memoryWorktree?.factState === "derived";
  // T3B/T1B — the pre-block verify sweeps, all read off the projection (never a class alone). `memChecking`
  // (T3B): the memory side is verified (ledger-map running) before the ledger gate decides, while the memory
  // worktree is not yet on disk. `baseChecking` (T1B): the base/code side is preflighted (worktree-add running,
  // code worktree not yet on disk) — "is local main current with upstream?" — before the stale-base gate.
  const memChecking =
    node.edges.some(
      (edge) => edge.kind === "ledger-map" && edge.state === "running",
    ) && !memWtMaterialised;
  const baseChecking =
    node.edges.some(
      (edge) => edge.kind === "worktree-add" && edge.state === "running",
    ) && !codeWtMaterialised;
  // `memGated`: the memory lane is held (no ledger map / a missing memory repo) → it ghosts while the
  // code lane stays solid. `baseStale`: local main is behind upstream and the start is blocked → the main code
  // node reads pruned/dormant (the pruned register).
  const memGated =
    node.memoryWorktree?.factState === "missing" ||
    ledgerMapBlocked(node);
  return { codeWtMaterialised, memWtMaterialised, memChecking, baseChecking, memGated };
}

export function resolveBlocks(node: EngineProcessNode) {
  // A born-blocked (pre-contract) enclosure — the reducer marks it "contract not yet written"; it renders the
  // big red FleetingEnclosure box (stale-base / pre-contract). This is the ONE pre-contract block shape:
  // there is no engine-anchored variant, so a missing runtime config reads as the same state.
  const fleeting = node.missingFacts.some((fact) => /contract not yet written/i.test(fact));
  // a stale-base block is the FLEETING (pre-contract) preflight case — `&& fleeting` keeps it distinct from a
  // live sync-needed block (which is also `behindSource > 0` but has a real worktree).
  const baseStale =
    (node.codeSource.behindSource ?? 0) > 0 && isBlocked(node) && fleeting;
  return { fleeting, baseStale };
}

export function resolveScanPointers(
  memChecking: boolean,
  baseChecking: boolean,
) {
  // The verify/block indicators anchor ON the checked REPOSITORY node (its rectangle), never the
  // connector lane: T1B points at the official-line CODE base (stale), T3B at the official-line MEMORY base
  // (no ledger map). `scanAt` centres the scan ring on that node.
  const scanAt = memChecking
    ? { x: COL_MAIN_CX, y: POS.memorySource.y + NODE_H / 2 }
    : baseChecking
      ? { x: COL_MAIN_CX, y: POS.codeSource.y + NODE_H / 2 }
      : null;
  // the topmost scan ring lights for a source-lane verify (T1B/T3B).
  const scanCenter = scanAt;
  return { scanAt, scanCenter };
}

function memoryBehindSource(node: EngineProcessNode): boolean {
  return (node.memorySource?.behindSource ?? 0) > 0;
}

export function resolveMemoryMovement(
  node: EngineProcessNode,
  fleeting: boolean,
  memWtMaterialised: boolean,
) {
  // T12B — a LIVE memory sync block: the memory worktree is REAL but origin/mem-main MOVED ahead
  // (memorySource.behindSource > 0) while the worktree holds local commits. `memMoved` (the soft cyan ▲
  // notification) shows BEFORE the gate (running, not yet blocked). `memSyncMoved`
  // is the escalated gate beat: the memory ledger-map lane is held STEADY while the CODE lane keeps advancing.
  // Restricted to a blocked LEDGER-MAP edge (the memory lane) so it never reclassifies the code-side
  // `engine-sync-needed` gallery state (a blocked `sync` edge), which keeps its existing edge-gate rendering.
  const behind = memoryBehindSource(node);
  const memMoved =
    !fleeting && behind && memWtMaterialised && !isBlocked(node);
  const memSyncMoved =
    !fleeting &&
    behind &&
    memWtMaterialised &&
    isBlocked(node) &&
    ledgerMapBlocked(node);
  return {
    memMoved,
    movedAt: memMoved ? { cx: COL_WT_CX, cy: POS.memoryWorktree.y - 36 } : null,
    memSyncMoved,
  };
}

export function resolveBlockPointer(
  node: EngineProcessNode,
  baseStale: boolean,
  memSyncMoved: boolean,
  memGated: boolean,
) {
  if (baseStale) return { cx: COL_MAIN_CX, top: POS.codeSource.y }; // T1B — the stale official-line CODE base
  if (memSyncMoved) return { cx: COL_WT_CX, top: POS.memoryWorktree.y }; // T12B — the held memory WORKTREE
  if (memGated && isBlocked(node)) {
    return { cx: COL_MAIN_CX, top: POS.memorySource.y }; // T3B — the unmappable official-line MEMORY base
  }
  return null;
}

export function resolveGates(node: EngineProcessNode) {
  // failure overlays — `fleeting` is derived above (it gates `baseStale`).
  // t14c — a terminal integration conflict draws a STOP (not the recoverable Gate) and no recovery chips.
  const terminal = node.phase === "integration-blocked";
  const terminalEdge = terminal
    ? node.edges.find((e) => e.kind === "integration" && e.state === "blocked")
    : undefined;
  // blocked = STEADY gate (a choice required); failed = FAULT → flicker, no gate.
  // The terminal-conflict integration edge is excluded — it renders as a STOP instead of a Gate.
  const gatedEdges = node.edges.filter(
    (e) => e.state === "blocked" && e !== terminalEdge,
  );
  const firstGated = gatedEdges.length ? EDGE_GEOM[gatedEdges[0].kind] : undefined;
  const stopGeom = terminalEdge ? EDGE_GEOM[terminalEdge.kind] : undefined;
  // the reason badge anchors at the STOP / blocked lane, else beside the faulting lane.
  const reasonCenter = stopGeom
    ? { cx: (stopGeom[0] + stopGeom[2]) / 2, cy: (stopGeom[1] + stopGeom[3]) / 2 + 16 }
    : firstGated
      ? { cx: (firstGated[0] + firstGated[2]) / 2, cy: (firstGated[1] + firstGated[3]) / 2 + 14 }
      : undefined;
  return { terminal, terminalEdge, gatedEdges, firstGated, stopGeom, reasonCenter };
}

export function resolveRecovery(node: EngineProcessNode): string[] {
  return [
    ...new Set(
      [
        node.nextAction,
        ...node.actions.filter((a) => a.enabled).map((a) => a.action),
      ].filter((value): value is string => Boolean(value)),
    ),
  ];
}

export function resolveLanding(node: EngineProcessNode) {
  // Show the landing dock only while the enclosure is actually retiring to the official line,
  // and only the refs the probe could resolve: a `missing` ref (probe couldn't run, e.g. gh absent)
  // carries no signal and is dropped, never rendered as an "unknown" chip.
  const landingRefs = node.landing.filter((ref) => ref.factState !== "missing");
  const showLanding =
    landingRefs.length > 0 &&
    (LANDING_PHASES.has(node.phase) || Boolean(node.integrationStrategy));
  // Cleanup teardown: a retiring enclosure (abandon OR a landed cleanup) keeps the historical
  // contract chip; on a successful cleanup the "back into main" seam reads the resolved origin-main tip.
  const retiring = node.phase === "abandoned" || node.phase === "cleanup-pending";
  const cleanupTip =
    node.phase === "cleanup-pending"
      ? node.landing.find(
          (ref) => ref.kind === "origin-main" && ref.factState !== "missing",
        )
      : undefined;
  return { landingRefs, showLanding, retiring, cleanupTip };
}

export function resolveScene(node: EngineProcessNode): EnclosureScene {
  const engines = resolveEngines(node);
  const material = resolveMaterialisation(node);
  const blocks = resolveBlocks(node);
  const scans = resolveScanPointers(
    material.memChecking,
    material.baseChecking,
  );
  const movement = resolveMemoryMovement(
    node,
    blocks.fleeting,
    material.memWtMaterialised,
  );
  const gates = resolveGates(node);
  const recovery = resolveRecovery(node);
  const landing = resolveLanding(node);
  // T14C — the refused-conduit flash lane (the integration return edge when failed or stale).
  // Polarity is derived from the edge state (refusedPolarityOf), never a class.
  const refusedEdges = node.edges
    .map((edge) => ({ edge, polarity: refusedPolarityOf(edge) }))
    .filter(
      (entry): entry is { edge: EngineProcessEdge; polarity: "amber" | "red" } =>
        entry.polarity !== null,
    );
  return {
    ...engines,
    ...material,
    ...blocks,
    ...scans,
    ...movement,
    blockNode: resolveBlockPointer(
      node,
      blocks.baseStale,
      movement.memSyncMoved,
      material.memGated,
    ),
    refusedEdges,
    ...gates,
    recovery,
    ...landing,
  };
}


// Pure geometry and state-derivation helpers for the Engine Room pod-stage canvas. No JSX here:
// every coordinate, lane path, flash polarity, and landing-flow rule used by the canvas components
// lives in one module so the SVG files stay readable and the derived truth stays testable.
import type {
  CommitRefNode,
  EngineProcessEdge,
  EngineProcessNode,
  LandingRefNode,
} from "../../types/projection";

export type ConduitState =
  | "nominal" | "complete" | "running" | "blocked" | "failed" | "stale" | "skipped" | "planned" | "unknown";
export type RuntimeState = "nominal" | "configured" | "indexing" | "down" | "missing" | "unknown";

export function conduitState(value: string): ConduitState {
  switch (value) {
    case "nominal": case "complete": case "running": case "blocked":
    case "failed": case "stale": case "skipped": case "planned":
      return value;
    default:
      return "unknown";
  }
}

export function runtimeState(value: string | undefined): RuntimeState {
  switch (value) {
    case "nominal": case "configured": case "indexing": case "down": case "missing":
      return value;
    default:
      return "unknown";
  }
}

// The canopy housing (podstage.html .canopy): a decorative HUD frame — a double bevel rim, the four L
// corner brackets, and the edge ticks. Pure amber line-art at the stage edges; carries no state, so
// it is aria-hidden. Stroke is inherited from the group; per-element strokeWidth/opacity set inline.

// --- geometry (ported 1:1 from podstage.html) --------------------------------
export const NODE_H = 62;
// The three middle columns are anchored on these centres (viewBox 1200 wide). They are evenly spaced so
// the gap zones either side of feat breathe equally (~72px edge-to-edge), and main/worktree are pulled
// apart symmetrically about the stage centre (~600). Every dependent coordinate below — node x, couplers,
// remote chips, conduit/edge geometry, the engine→node wires, the landing-flow paths, and the enclosure
// border — is derived from or aligned to these three centres, so a future re-space is a small, local edit.
// POS.x is the LEFT edge (centre = x + w/2); the remote chips + couplers sit ON these centres.
export const COL_MAIN_CX = 365; // official-line · main column (left of the middle three)
export const COL_FEAT_CX = 595; // feat ▸ source column (the landing-only middle tier, in the main↔worktree gap)
export const COL_WT_CX = 835; // worktree code/memory column (right of the middle three)
export const POS = {
  codeSource: { x: COL_MAIN_CX - 90, y: 250, w: 180 }, // = mockup m-code (the OFFICIAL LINE = main, left)
  memorySource: { x: COL_MAIN_CX - 90, y: 372, w: 180 },
  codeWorktree: { x: COL_WT_CX - 100, y: 250, w: 200 }, // = mockup w-code (worktree, right)
  memoryWorktree: { x: COL_WT_CX - 100, y: 372, w: 200 },
  // The feat/fix source branch — the THIRD tier the worktree was actually branched off. It
  // lives in the GAP between main and the worktree and is shown only during landing (mockup feat-code /
  // feat-mem, w136), so the closeout reads main → feat → worktree, never main = feat.
  featCode: { x: COL_FEAT_CX - 68, y: 250, w: 136 },
  featMemory: { x: COL_FEAT_CX - 68, y: 372, w: 136 },
} as const;
export const ENGINE = {
  cgc: { x: 1057, y: 102 }, grepai: { x: 1057, y: 452 }, // worktree (enclosure) engines, right world
  mcgc: { x: 81, y: 102 }, mgrep: { x: 81, y: 452 }, // official-line (workspace) engines, left world
  w: 54, h: 96,
} as const;
export const COUPLER_X = COL_WT_CX; // worktree code↔memory coupler — on the worktree column centre
export const OFFICIAL_COUPLER_X = COL_MAIN_CX; // official-line code↔memory coupler (podstage cpl-main) — on the main centre

// Flow-conduit endpoints by edge kind, anchored to node/engine edges so a line never crosses a box.
export const EDGE_GEOM: Record<string, readonly [number, number, number, number]> = {
  // main right edge (COL_MAIN_CX+90) → worktree left edge (COL_WT_CX-100): the intake lanes spanning the gap.
  "worktree-add": [COL_MAIN_CX + 90, 281, COL_WT_CX - 100, 281],
  "ledger-map": [COL_MAIN_CX + 90, 403, COL_WT_CX - 100, 403],
  // Provider CLONE arrows ("cloned-from, not re-indexed"): the worktree engines are SEEDED BY
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

// The conduit path string for an edge — a straight line for settled lanes, the cross-stage BOW for the
// provider clone arcs (CGC over the top / GrepAI under the bottom). Shared by Conduit and the refused-
// conduit flash overlay so the flash traces the EXACT same lane geometry (no duplicated arc maths).
export function conduitPathD(edge: EngineProcessEdge): string | null {
  const geom = EDGE_GEOM[edge.kind];
  if (!geom) return null;
  const [x1, y1, x2, y2] = geom;
  const cloneArc = edge.kind === "cgc-seed" || edge.kind === "grepai-clone";
  const dip = edge.kind === "grepai-clone" ? 104 : -116; // GrepAI bows down; CGC bows up
  return cloneArc
    ? `M${x1} ${y1} C ${x1 + 210} ${y1 + dip}, ${x2 - 210} ${y2 + dip}, ${x2} ${y2}`
    : `M${x1} ${y1} L ${x2} ${y2}`;
}

// Refused-conduit flash polarity (T9B/T9C/T14C), DERIVED from the edge state — never a class, and
// never a field on the edge. A `failed` lane is a fault (red), a `stale` lane a reroute (amber).
// Any other kind/state → no flash.
//
// `integration` / `integration-mem` are dead against TODAY's reducer: its two edge builders --
// `_process_edges` and `_start_process_node` in `observer/reducer.py` -- emit only worktree-add,
// cgc-seed, ledger-map, grepai-clone and sync, so no served payload reaches these two
// arms. They are kept anyway, and the honest reason is NOT forward-compatibility — nothing is
// scheduled to start emitting them. It is that (a) `EngineProcessEdge`'s own documented kind
// vocabulary (`observer/projection.py`, the comment above `kind: str`) lists `integration` as a
// valid kind, unlike the `refused` STATE removed alongside, which that model's state comment never
// listed; and (b) the whole integration lane — geometry, the T14C conflict scenario, the replay
// strategy — is authored in the dev fixtures and covered by tests, so the arms are exercised even
// though the server does not drive them. Delete the lane and its coverage together, or not at all.
// (`integration-mem` is the memory-side mirror and is not itself in that documented list; it lives
// or dies with `integration`, which is why it is named here rather than quietly assumed.)
export function refusedPolarityOf(edge: EngineProcessEdge): "amber" | "red" | null {
  const isSeedOrIntegration =
    edge.kind === "cgc-seed" ||
    edge.kind === "grepai-clone" ||
    edge.kind === "integration" ||
    edge.kind === "integration-mem";
  if (!isSeedOrIntegration) return null;
  if (edge.state === "failed") return "red";
  if (edge.state === "stale") return "amber";
  return null;
}

// The build-up "branch-copy": a worktree node is born from its official-line node, rising from nothing
// while sliding in from the main side. Driven by the honesty axis — a `planned` worktree ref is not yet
// on disk (hidden, offset toward main), `observed`/`derived` is materialised (in place). Official-line
// nodes are always `observed`, so they stay settled. The sceneSvg transition tweens between these as the
// projection advances (and freezes instant under data-effects=off, so the count/presence tests stand).
export function branchEnter(factState: CommitRefNode["factState"]): { opacity: number; dx: number } {
  switch (factState) {
    case "observed":
    case "derived":
      return { opacity: 1, dx: 0 };
    case "planned":
      return { opacity: 0, dx: -90 };
    case "missing":
      return { opacity: 0.22, dx: 0 };
    case "stale":
      return { opacity: 0.55, dx: 0 };
    default:
      return { opacity: 0.45, dx: 0 };
  }
}

// The ledger-popover content default: the memory.md lookup table the coupler stands for.
export const LEDGER_PREVIEW = 8;

export const short = (commit: string | null | undefined): string => (commit ? commit.slice(0, 8) : "—");

// The commit's recorded wall-clock: "2026-06-18T18:19:48+02:00" -> "06-18 18:19". A plain
// string slice — no Date/timezone conversion, so it is deterministic + screenshot-stable and shows the
// committer's recorded offset, not the viewer's locale. Absent date -> empty cell (honest hash-only row).
export const compactDate = (iso: string | undefined): string =>
  iso && iso.length >= 16 ? iso.slice(5, 16).replace("T", " ") : "";

// The warp coupler = the memory.md LEDGER link: the lookup-table row binding this side's code commit to
// its memory commit across the two physically distinct repos (coupler-semantics fix; NOT the task
// series contract). A drawn chain-link glyph + the two linked short-hashes as the label, and — when bound —
// the warp-core surge (two hot bands born at the link, splitting up + down; ported from podstage.html).
// Default-show the newest LEDGER_PREVIEW rows; "▾ show N more" expands in place to the full served window
// (≤ LEDGER_WINDOW = 25), which scrolls. Older rows stay in the file ("+N more in memory.md"). The
// full-history browser is the post-ship viewer (agents-remember#88).

export function isBlocked(node: EngineProcessNode): boolean {
  return (
    node.health === "blocked" ||
    node.health === "failed" ||
    node.health === "stale" ||
    node.missingFacts.length > 0
  );
}
export function truncate(value: string, max: number): string {
  return value.length > max ? `${value.slice(0, max - 1)}…` : value;
}

// Every failure/alert overlay (gate, reason, attention, chips, STOP, the block pointer) ENTERS with a
// quick fade + subtle pop and EXITS the same way when the block clears (via AnimatePresence). The dashboard
// never hard-pops state in: Motion owns the enter/exit. Gated by `useShouldAnimate` → instant end-state
// under effects-off so the snapshots stay deterministic. `transform-box: fill-box` scales from the element's
// own centre (the `engineCharge` pattern), so the pop grows in place instead of sliding from the SVG origin.
export function alertProps(animate: boolean) {
  return {
    initial: animate ? { opacity: 0, scale: 0.88 } : false,
    animate: { opacity: 1, scale: 1 },
    exit: { opacity: 0, scale: 0.88 },
    transition: { duration: animate ? 0.28 : 0, ease: [0.2, 0.7, 0.2, 1] as [number, number, number, number] },
    style: { transformBox: "fill-box" as const, transformOrigin: "center" as const },
  };
}

// Steady red gate over a blocked/failed lane — a human choice required, never the fault flicker (the
// flicker is the engine). Drawn at the blocked edge's midpoint.

export const CLOSEOUT_BEATS = ["code", "onboard", "quality", "memory", "ledger"] as const;

export const RBOX_W = 148; // = mockup r-box width
export const RBOX_H = 36;
export const REMOTE_POS: Record<string, { x: number; y: number }> = {
  "origin-main": { x: COL_MAIN_CX - RBOX_W / 2, y: 66 }, // top, centred above the main column (merges into it)
  "origin-feat": { x: COL_FEAT_CX - RBOX_W / 2, y: 66 }, // top, centred above the feat column (just pushed)
  "origin-mem-main": { x: COL_MAIN_CX - RBOX_W / 2, y: 522 }, // mirrored to the bottom, under the main column
};
export const PR_CX = (COL_MAIN_CX + COL_FEAT_CX) / 2; // centre of the gap between origin/main and origin/feat
// The dock is the SUCCESSFUL-LANDING arc — it shows only while an enclosure is actually retiring to the
// official line (closeout → integration → cleanup), not for every live worktree the probe touched.
export const LANDING_PHASES = new Set(["closeout-pending", "integration-pending", "cleanup-pending"]);
export type RemoteTone = "planned" | "live" | "done" | "stale";

export function remoteTone(ref: LandingRefNode): RemoteTone {
  if (ref.factState === "stale") return "stale";
  if (ref.factState === "planned" || ref.state === "planned") return "planned";
  if (ref.state === "tip" || ref.state === "merged" || ref.state === "pushed") return "done";
  return "live";
}

// One short status word per chip — the colour already carries the tone, so the line stays terse and
// always fits; the full ref + detail lives in the hover <title>.
export function remoteStateWord(ref: LandingRefNode): string {
  if (ref.factState === "stale") return "stale";
  if (ref.factState === "planned" || ref.state === "planned") return "planned";
  return ref.state || "—";
}

export function remoteTitle(ref: LandingRefNode): string {
  const age = ref.staleSeconds == null ? "" : ` · ${Math.round(ref.staleSeconds)}s old`;
  const freshness = ref.factState === "stale" ? `stale${age}` : ref.factState;
  return `${ref.label} · ${freshness} · ${ref.detail ?? ref.state}`;
}


export type FlowState = "active" | "settled" | "hidden";

function refResolved(refs: LandingRefNode[], kind: string): boolean {
  const ref = refs.find((r) => r.kind === kind);
  return ref !== undefined && ref.factState === "observed" && ref.state !== "planned";
}

function prMerged(refs: LandingRefNode[]): boolean {
  const pr = refs.find((r) => r.kind === "pr");
  return pr !== undefined && pr.factState === "observed" && pr.state === "merged";
}

function memPushed(refs: LandingRefNode[]): boolean {
  const memory = refs.find((r) => r.kind === "origin-mem-main");
  return memory !== undefined && memory.factState === "observed" && memory.state === "pushed";
}

export function landingFlowState(refs: LandingRefNode[], kind: string): FlowState {
  if (kind === "push") {
    return !refResolved(refs, "origin-feat") ? "hidden" : prMerged(refs) ? "settled" : "active";
  }
  if (kind === "pull") return !prMerged(refs) ? "hidden" : memPushed(refs) ? "settled" : "active";
  return memPushed(refs) ? "active" : "hidden"; // carry + push-mem: the carryover frontier
}

export function chargeMotion(runtime: RuntimeState): { scaleY: number; opacity: number } {
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

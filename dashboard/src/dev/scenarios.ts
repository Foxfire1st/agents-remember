// Slice 5i — the scenario model that drives the REAL cockpit through phase-transition timelines.
// Each mode is an ordered `ScenarioFrame[]`; each frame is a FULL `WorkspaceProjection` + a caption. The
// player applies a complete frame to the real store and the real cockpit animates the diff (center-out
// charge, draw-on conduits, promote-in-place, the landing strip, the H4 teardown) — so the integrated
// MOTION is verifiable end-to-end, not just static frames. Frames reuse the existing engine-room fixtures
// (no new substrate), so a scenario is a sequence of named states wrapped into full projections.
import { ENGINE_ROOM_SCENARIOS } from "../panels/engine-room/fixtures";
import type { ObserverEvent } from "../types/event";
import type { WorkspaceProjection } from "../types/projection";
import { engineRoomProjection, GALLERY } from "./fixtures";

export interface ScenarioFrame {
  caption: string;
  projection: WorkspaceProjection;
  events?: ObserverEvent[];
  durMs?: number;
}

export interface Scenario {
  name: string;
  label: string;
  frames: ScenarioFrame[];
}

// A frame built from a named engine-room scenario (wrapped into a full projection). Throws on a bad name
// so an authoring typo fails loud in dev rather than silently rendering an empty stage.
function erFrame(scenarioName: string, caption: string, durMs?: number): ScenarioFrame {
  const scenario = ENGINE_ROOM_SCENARIOS.find((entry) => entry.name === scenarioName);
  if (!scenario) throw new Error(`scenario player: unknown engine-room scenario "${scenarioName}"`);
  return { caption, projection: engineRoomProjection(scenario), durMs };
}

// The mockup keeps build-up and tear-down as TWO separate animations; we mirror that. BUILD-UP is the
// worktree birth (B0→B5 boot choreography). TEAR-DOWN is the full land → dispose arc (D0→D6): idle →
// closeout → integrate/code-lands → memory carryover → de-materialise → stack removed.
const buildUp: Scenario = {
  name: "build-up",
  label: "Build-up · worktree assembles (B0→B5)",
  frames: [
    erFrame("engine-boot-0-main-only", "B0 · worktree_start — the official line (main) is at rest; no enclosure yet"),
    erFrame("engine-boot-1-code-worktree", "B1 · code worktree copies in from main (branch-copy)"),
    erFrame("engine-boot-2-memory-contract", "B2 · memory worktree copies in; the contract coupler binds"),
    erFrame("engine-boot-3-providers-dim", "B3 · provider runtime — engines materialise dim, cloned from main"),
    erFrame("engine-boot-4-seeding", "B4 · seed / clone — engines charge cyan (center-out boot-fill)"),
    erFrame("engine-boot-5-nominal", "B5 · powered — went green, settle nominal · the idle constellation"),
  ],
};

const tearDown: Scenario = {
  name: "tear-down",
  label: "Tear-down · land → dispose (D0→D6)",
  frames: [
    erFrame("engine-boot-5-nominal", "D0 · idle / working enclosure — the whole constellation, at rest"),
    erFrame("engine-landing-closeout", "D1 · closeout (gated) — code → onboarding → quality → memory → ledger"),
    erFrame("engine-landing-ffonly", "D2 · integrate — worktree → feat/fix source (push feat → origin/feat, PR open)"),
    erFrame("engine-landing-pushed", "D3 · code lands — PR merged → origin/main advances → local main pulls"),
    erFrame("engine-landing-merged", "D4 · memory carryover — feat → local mem-main, then push → origin/mem-main"),
    erFrame("engine-cleanup-pending", "D5 · de-materialise — providers power down, the worktree detaches, the border collapses", 2200),
    erFrame("engine-retired", "D6 · stack removed — only the main constellation remains (+ a dim historical contract chip)"),
  ],
};

// One failure mode for S1: a GrepAI seed fault, then the reindex reroute (an amber fallback, not a failure).
const seedFault: Scenario = {
  name: "seed-fault",
  label: "Seed fault → reindex reroute",
  frames: [
    erFrame("engine-boot-0-main-only", "B0 · worktree_start — the official line is at rest"),
    erFrame("engine-boot-3-providers-dim", "B3 · provider runtime — engines materialise, clone begins"),
    erFrame("engine-grepai-failed", "S · GrepAI clone failed — the engine flickers red (CGC unaffected)"),
    erFrame("engine-cgc-fallback", "S → reindex reroute — amber center-out pulse (a fallback, not a failure)", 2000),
  ],
};

// 05o T3B — the memory/ledger block: the first recoverable failure mode, mirroring the prototype's T3b M0→M7
// (verify → block → gate → reconcile → provider clone → settle), the pattern 5 of the 8 modes share. One
// boot-demo enclosure throughout, so the recover animates the same enclosure (gate lifts, ghost clears,
// memory materialises) instead of remounting. The recover does NOT teleport to nominal: after the ledger
// maps it runs the provider seed/clone beats (B3/B4 — the cross-stage copy arrows sweep, engines charge
// cyan), exactly as the mockup's M5/M6, before settling — so the recovered engines boot honestly on-screen.
const memoryBlock: Scenario = {
  name: "memory-block",
  label: "Memory block · ledger gate → reconcile (T3B)",
  frames: [
    erFrame("engine-boot-1-code-worktree", "M1 · code worktree is real; the ledger gate verifies the memory side next"),
    erFrame("engine-boot-memory-verify", "M2 · ledger-map scan — checking the memory mapping (code lane solid)"),
    erFrame("engine-boot-memory-blocked", "M3 · BLOCK — no ledger map: the memory lane gates + ghosts; the code lane stays solid", 2400),
    erFrame("engine-boot-2-memory-contract", "M4 · reconcile — the ledger maps; the gate lifts, the ghost clears, memory materialises"),
    erFrame("engine-boot-3-providers-dim", "M5 · provider runtime — the engines materialise dim, clone arrows begin"),
    erFrame("engine-boot-4-seeding", "M6 · seed / clone — CGC seeds over the top, GrepAI clones under the bottom; engines charge cyan"),
    erFrame("engine-boot-5-nominal", "M7 · running — recovered after the memory block; settled nominal, coupler bound"),
  ],
};

// 05o T1B — the stale-base block: a recoverable pre-contract failure mode, mirroring the prototype's T1B
// F0→F8 (preflight → block → fast-forward → boot). One boot-demo enclosure throughout; the base (local main)
// is behind upstream, so the preflight scans the code lane, a fleeting enclosure is born blocked with the
// main node pruned, and fast-forward recovers through the same provider clone beats (copy-arrows) as the boot.
const staleBase: Scenario = {
  name: "stale-base",
  label: "Stale base · preflight → fast-forward (T1B)",
  frames: [
    erFrame("engine-boot-0-main-only", "F0 · worktree_start — the official line (main) is at rest"),
    erFrame("engine-boot-stale-verify", "F1 · preflight — scanning the base: is local main current with upstream?"),
    erFrame("engine-boot-stale-blocked", "F2 · BLOCK — base behind upstream: the main node prunes (dormant), a fleeting enclosure is born blocked", 2400),
    erFrame("engine-boot-1-code-worktree", "F3·F4 · fast-forward — the base updates; the code worktree copies in from the now-current main"),
    erFrame("engine-boot-2-memory-contract", "F5 · memory worktree copies in; the contract coupler binds"),
    erFrame("engine-boot-3-providers-dim", "F6 · provider runtime — the engines materialise dim, clone arrows begin"),
    erFrame("engine-boot-4-seeding", "F7 · seed / clone — CGC seeds over the top, GrepAI clones under the bottom; engines charge cyan"),
    erFrame("engine-boot-5-nominal", "F8 · running — recovered after the stale-base block; settled nominal"),
  ],
};

// The old static gallery states fold in as single-frame "resting" scenarios — no coverage lost, and each
// stays reachable by name (so `?scenario=` / `?state=` and the existing screenshot tooling keep working).
const restingScenarios: Scenario[] = GALLERY.map((entry) => ({
  name: entry.name,
  label: entry.name,
  frames: [{ caption: entry.name, projection: entry.projection, events: entry.events }],
}));

// Timelines first (build-up · tear-down · the failure mode), then the folded-in resting frames.
export const SCENARIOS: Scenario[] = [buildUp, tearDown, seedFault, memoryBlock, staleBase, ...restingScenarios];

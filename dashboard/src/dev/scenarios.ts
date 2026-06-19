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
    erFrame("engine-landing-ffonly", "D2·D3 · integrate ff-only → code lands (worktree → feat → origin/feat → PR → origin/main)"),
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

// The old static gallery states fold in as single-frame "resting" scenarios — no coverage lost, and each
// stays reachable by name (so `?scenario=` / `?state=` and the existing screenshot tooling keep working).
const restingScenarios: Scenario[] = GALLERY.map((entry) => ({
  name: entry.name,
  label: entry.name,
  frames: [{ caption: entry.name, projection: entry.projection, events: entry.events }],
}));

// Timelines first (build-up · tear-down · the failure mode), then the folded-in resting frames.
export const SCENARIOS: Scenario[] = [buildUp, tearDown, seedFault, ...restingScenarios];

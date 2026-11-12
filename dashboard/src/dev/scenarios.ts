// Slice 5i — the scenario model that drives the REAL cockpit through phase-transition timelines.
// Each mode is an ordered `ScenarioFrame[]`; each frame is a FULL `WorkspaceProjection` + a caption. The
// player applies a complete frame to the real store and the real cockpit animates the diff (center-out
// charge, draw-on conduits, promote-in-place, the landing strip, the H4 teardown) — so the integrated
// MOTION is verifiable end-to-end, not just static frames. Frames reuse the existing engine-room fixtures
// (no new substrate), so a scenario is a sequence of named states wrapped into full projections.
//
// The three provider arcs that used to live here (the GrepAI seed fault, the CGC reindex reroute and
// the pre-contract provider-plan block) went with the search providers: their frames existed only to
// animate engine materialisation and seed/clone conduits, neither of which the server composes any
// more. What is left is enclosure-centred: the worktrees, the ledger gate, the sync gate, the landing
// arc and the failure modes that survive on those facts.
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
    erFrame("engine-boot-5-nominal", "B5 · powered — both lanes bound, settle nominal · the idle constellation"),
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
    erFrame("engine-cleanup-pending", "D5 · de-materialise — the worktree detaches, the border collapses", 2200),
    erFrame("engine-retired", "D6 · stack removed — only the main constellation remains (+ a dim historical contract chip)"),
  ],
};

// 05o T12B — the live memory-sync block: origin/mem-main moved ahead while the worktree holds local memory
// commits — the memory lane gates + ghosts (soft cyan "moved ▲" → steady gate) while the CODE lane keeps
// advancing. Recover is merge-memory: the memory worktree FAST-FORWARDS (a ref/ff diff). One live-sync
// enclosure throughout.
const liveSync: Scenario = {
  name: "live-sync",
  label: "Live sync · memory moved → merge (T12B)",
  frames: [
    erFrame("engine-sync-recovered", "Y0 · live · running — the worktree is working; both lanes bound, worktrees nominal"),
    erFrame("engine-sync-moved", "Y1 · upstream memory moves — origin/mem-main advances; a soft “moved ▲” badge announces the sync choice"),
    erFrame("engine-sync-memory-blocked", "Y2 · BLOCKED · memory sync — a steady gate drops on the memory lane only; the code lane keeps advancing (commit ●)", 2400),
    erFrame("engine-sync-recovered", "Y3·Y4 · recover · merge-memory — the gate lifts, the ghost clears, the memory worktree fast-forwards; back in sync"),
  ],
};

// 05o T14C — the integration conflict: a TERMINAL failure mode (idle → closeout → replay → ⚡CONFLICT flash
// → steady STOP). The all-or-nothing replay hits a conflict, the integrate arrows flash RED and resolve into
// a steady STOP; the source branch did NOT move. NO recover tail — the developer resolves it manually.
const integrationConflict: Scenario = {
  name: "integration-conflict",
  label: "Integration conflict · replay → STOP (T14C · terminal)",
  frames: [
    erFrame("engine-boot-5-nominal", "C0 · idle / working enclosure — closeout about to begin"),
    erFrame("engine-landing-closeout", "C1 · closeout (gated · approved) — code commit ● + memory refresh + ledger maps + contract flips"),
    erFrame("engine-landing-ffonly", "C2 · integrate · replay — the closeout commits attempt to replay onto the feat/fix source branch (code + memory)", 1400),
    erFrame("engine-integration-conflict-flash", "C3 · ⚡ CONFLICT — the replay hits a conflict: the integrate arrows flash red and stop (all-or-nothing)"),
    erFrame("engine-integration-conflict", "C4 · BLOCKED · integration conflict — a steady STOP; the source branch did NOT move. Terminal — no auto-recovery; resolve manually", 2600),
  ],
};

// 05o T18 — abandon: the live enclosure DISSOLVES with no landing (idle → abandon invoked → dissolve →
// gone). One boot-demo identity across every frame, so the X0→X2 step IS the Motion dissolve (a prop diff).
// TERMINAL — no recover/boot-back tail; only the official line + an abandoned record remain.
const abandon: Scenario = {
  name: "abandon",
  label: "Abandon · dissolve, no landing (T18)",
  frames: [
    erFrame("engine-boot-5-nominal", "X0 · idle / working enclosure — the worktree is live (both lanes bound)"),
    erFrame("engine-boot-abandoned", "X1 · worktree_abandon — abandon invoked: no integration, no landing; nothing is pushed"),
    erFrame("engine-boot-abandoned", "X2 · dissolve — the enclosure fades + slightly collapses; branches detach (no landing beats)", 1300),
    erFrame("engine-boot-abandoned", "X3 · gone · abandoned — only the official line remains; an 'abandoned' record is kept (no merge, no history)"),
  ],
};

// 05o T3B — the memory/ledger block: the first recoverable failure mode (verify → block → gate →
// reconcile → settle), the pattern the surviving failure modes share. One boot-demo enclosure
// throughout, so the recover animates the same enclosure (gate lifts, ghost clears, memory
// materialises) instead of remounting. The recover does not teleport to nominal: the ledger map is
// its own beat, and the settle follows it.
const memoryBlock: Scenario = {
  name: "memory-block",
  label: "Memory block · ledger gate → reconcile (T3B)",
  frames: [
    erFrame("engine-boot-1-code-worktree", "M1 · code worktree is real; the ledger gate verifies the memory side next"),
    erFrame("engine-boot-memory-verify", "M2 · ledger-map scan — checking the memory mapping (code lane solid)"),
    erFrame("engine-boot-memory-blocked", "M3 · BLOCK — no ledger map: the memory lane gates + ghosts; the code lane stays solid", 2400),
    erFrame("engine-boot-2-memory-contract", "M4 · reconcile — the ledger maps; the gate lifts, the ghost clears, memory materialises"),
    erFrame("engine-boot-5-nominal", "M7 · running — recovered after the memory block; settled nominal, coupler bound"),
  ],
};

// 05o T1B — the stale-base block: a recoverable pre-contract failure mode (preflight → block →
// fast-forward → boot). One boot-demo enclosure throughout; the base (local main) is behind
// upstream, so the preflight scans the code lane, a fleeting enclosure is born blocked with the
// main node pruned, and the fast-forward recovers into the worktree copy.
const staleBase: Scenario = {
  name: "stale-base",
  label: "Stale base · preflight → fast-forward (T1B)",
  frames: [
    erFrame("engine-boot-0-main-only", "F0 · worktree_start — the official line (main) is at rest"),
    erFrame("engine-boot-stale-verify", "F1 · preflight — scanning the base: is local main current with upstream?"),
    erFrame("engine-boot-stale-blocked", "F2 · BLOCK — base behind upstream: the main node prunes (dormant), a fleeting enclosure is born blocked", 2400),
    erFrame("engine-boot-1-code-worktree", "F3·F4 · fast-forward — the base updates; the code worktree copies in from the now-current main"),
    erFrame("engine-boot-2-memory-contract", "F5 · memory worktree copies in; the contract coupler binds"),
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

// Timelines first (build-up · tear-down · the failure modes), then the folded-in resting frames.
export const SCENARIOS: Scenario[] = [
  buildUp,
  tearDown,
  memoryBlock,
  staleBase,
  liveSync,
  integrationConflict,
  abandon,
  ...restingScenarios,
];

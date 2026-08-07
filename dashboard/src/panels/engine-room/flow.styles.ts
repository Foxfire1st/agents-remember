// Engine Room flow/overlay tokens: seed/clone/integrate conduits, refused flashes, stop bars,
// attention badges, closeout beats, and teardown records.
import { css, cva } from "../../../styled-system/css";

export const flowConduit = cva({
  base: { fill: "none", strokeWidth: "2.4" },
  variants: {
    state: {
      nominal: { stroke: "token(colors.amber)", opacity: "0.8" },
      complete: { stroke: "token(colors.amber)", opacity: "0.6" },
      // running (seed/clone): solid cyan. GSAP DrawSVG draws it on once per activation (05n) via
      // useEngineTimeline — DrawSVG owns the stroke-dasharray/offset, so the recipe carries NO dash here
      // (a CSS dash would fight DrawSVG, and pathLength is gone — DrawSVG measures the real length). Under
      // data-effects=off no tween runs and the path simply rests solid (= fully drawn).
      // 5o glow pass — the active flow glows its state colour (spec: 3px on the running line). The packet
      // carries the brighter 5px (flowPacket). Settled/planned lanes stay glow-less (a connection, not an action).
      running: { stroke: "token(colors.cyan)", filter: "drop-shadow(0 0 3px token(colors.cyan))" },
      blocked: { stroke: "token(colors.alarm)" },
      failed: { stroke: "token(colors.alarm)" },
      stale: { stroke: "token(colors.alarm)", opacity: "0.55" },
      skipped: { stroke: "token(colors.grid)", opacity: "0.4" },
      // 05n — real-unit dash (was "3 5" normalized to pathLength 100, now removed). Tuned on the bench.
      planned: { stroke: "token(colors.muted)", strokeDasharray: "9 7", opacity: "0.5" },
      unknown: { stroke: "token(colors.dormant)", opacity: "0.4" },
    },
  },
});

// The travelling flow packet — a cyan energy dot that rides a seeding/cloning conduit via GSAP MotionPath
// (05n — the conduit path string is on the packet's data-path; replaces CSS offset-path). The packet only
// renders while animate, so under effects=off / reduced-motion there is no static dot.
export const flowPacket = css({ fill: "token(colors.cyan)", opacity: "0.95", filter: "drop-shadow(0 0 5px token(colors.cyan))" });

// --- failure-mode primitives (05o §10) ---------------------------------------
// Scan ring — the pre-block verify sweep (a ledger-map check / stale-base preflight / provider probe): a
// cyan ring expands + fades on the lane being checked, cyan because a check IS the active step. Transient,
// like the flow packet — no settled state (opacity 0 at rest); GSAP (data-fx='scan') drives the r/opacity
// expand-fade, and the <circle> is only rendered while animate, so under effects=off / reduced-motion it
// is absent (no frozen ring noise). Matches the engine-room visual-language spec §10 (docs/design).
export const scanRing = css({
  fill: "none",
  stroke: "token(colors.cyan)",
  strokeWidth: "2",
  opacity: "0",
  filter: "drop-shadow(0 0 4px token(colors.cyan))",
});

// Ghosted lane — one lane held under a gate while its sibling proceeds: the held lane dims + desaturates so
// "this side is blocked, that side is fine" reads at a glance (the memory/ledger block — code stays a solid
// wire, the memory lane ghosts under a steady gate). Distinct from `planned` (dashed grey): a ghosted lane
// is REAL but HELD, not not-yet. Projection-driven (applied off the blocked memory edge) and STATIC (no
// animation); it lives on the inner conduit <path>, NOT the Motion group, so it never fights Motion's
// group opacity (a className opacity on a Motion element loses on a static frame — see worktreeWire).
export const ghostedLane = css({ opacity: "0.32", filter: "grayscale(0.45)" });

// 05o refused-conduit flash (SHARED — T9B red fault · T9C amber reroute · T14C red conflict). The seed/
// return conduit that did not take (podstage `.refused`/`.refred`): cyan → white spark → its polarity
// colour → fade out. AMBER = a fallback/reroute (CGC seed stale → reindex), RED = a fault/conflict (GrepAI
// seed fault, integration conflict). Polarity is NEVER a class alone and never a field on the edge — it is
// DERIVED from edge.state (failed→red / stale→amber) and carried as the cva variant.
// The one-shot colour sweep + the fade are owned by GSAP (data-fx='refuse', repeat:0 — CSS stays static,
// 05f §8); the base recipe rests at opacity 0 (a one-shot flash has no settled state — it ends GONE, like
// the prototype's `animation:refused .9s ease forwards` ending at opacity 0), so under effects=off the lane
// is present-but-absent (the steady STOP/gate carries the settled state).
export const refusedConduit = cva({
  base: { fill: "none", strokeWidth: "2.6", opacity: "0" },
  variants: {
    polarity: {
      // amber = reroute (a fallback, not a failure) — the T9C seed-refused lane
      amber: { stroke: "token(colors.amber)", filter: "drop-shadow(0 0 4px token(colors.amber))" },
      // red = fault / conflict — the T9B seed fault + the T14C integration conflict
      red: { stroke: "token(colors.alarm)", filter: "drop-shadow(0 0 4px token(colors.alarm))" },
    },
  },
});

// 05o/T7B — a faint dropout halo behind the UNLIT worktree engines (the provider-plan block: the engines
// never light because the runtime config is missing). A static, alarm-toned dashed outline marking the
// engine slot as HELD (not merely not-yet, which is the build-up's faded-absent engine). No animation.
export const engineDropout = css({
  fill: "none",
  stroke: "token(colors.alarm)",
  strokeWidth: "1.4",
  strokeDasharray: "5 5",
  opacity: "0.5",
  filter: "drop-shadow(0 0 4px token(colors.alarm))",
});

// 05o/T12B — the "moved" remote indicator (podstage .imsg `moved`, soft/cyan): a ▲ up-triangle + a pill
// announcing the UPSTREAM memory ref advanced (origin/mem-main moved while the worktree holds local
// commits). SOFT register — cyan, a notification that a sync CHOICE is needed, NOT the alarm gate (that
// escalates a beat later). Mirrors reasonBadge/reasonDot/reasonText geometry but cyan, with a ▲ pointer.
export const movedBadge = css({ fill: "oklch(0.18 0.04 230)", stroke: "token(colors.cyan)", strokeWidth: "1.1" });
export const movedTriangle = css({ fill: "token(colors.cyan)", filter: "drop-shadow(0 0 3px token(colors.cyan))" });
export const movedText = css({ fill: "token(colors.cyan)", fontSize: "11px", letterSpacing: "0.03em", fontWeight: "600" });

// 05o T1B — the FLEETING block enclosure (the prototype's big red provisional box, podstage `.fbox`): a
// born-blocked enclosure (stale-base / pre-contract) renders as a dark-red dashed box over the worktree
// footprint — the BLOCKED title + reason centred + the recovery chips along the bottom — REPLACING the
// dashed-amber `enclosureBorder`, so "this enclosure is gated, not yet real" reads at a glance. The box rect
// carries the .55 dim; the title/reason sit above it at full opacity (siblings, like the prototype).
export const fleetingBox = css({
  // brighter + more opaque than the prototype's .55 so it reads as a clear red panel over the dashboard's
  // blueprint backdrop video (which the prototype doesn't have); a soft alarm glow lifts it off the scene.
  fill: "oklch(0.22 0.06 25)",
  stroke: "token(colors.alarm)",
  strokeWidth: "1.8",
  strokeDasharray: "8 7",
  opacity: "0.82",
  filter: "drop-shadow(0 0 6px token(colors.alarm))",
});
export const fleetingBoxTitle = css({
  fill: "oklch(0.82 0.16 25)",
  fontSize: "15px",
  letterSpacing: "0.06em",
  fontWeight: "700",
});
export const fleetingBoxReason = css({ fill: "token(colors.muted)", fontSize: "12px", letterSpacing: "0.02em" });

// --- failure overlays (5g G3) ------------------------------------------------
// blocked = STEADY red gate over the blocked lane (a human choice required) — never the fault flicker
// (that's the engine, G4). Every blocked/fault raises the alarm-parity attention badge. A local reason
// badge (cyan-dot pointer + pill) states WHY at the lane; recovery chips offer the next action. All
// driven off node.health / edge.state / missingFacts / nextAction — colour-as-state, no inferred chrome.
export const gateBar = css({ fill: "token(colors.alarm)", opacity: "0.92", filter: "drop-shadow(0 0 7px token(colors.alarm))" });
// Alarm-parity attention badge. GSAP (data-fx='breath') drives the gentle breathing; static at rest.
export const attnBadge = css({
  fill: "oklch(0.26 0.09 25)",
  stroke: "token(colors.alarm)",
  strokeWidth: "1.3",
});
export const attnText = css({ fill: "oklch(0.93 0.08 25)", fontSize: "11px", letterSpacing: "0.12em", fontWeight: "600" });
export const reasonBadge = css({ fill: "oklch(0.2 0.05 25)", stroke: "token(colors.alarm)", strokeWidth: "1.1" });
export const reasonDot = css({ fill: "token(colors.cyan)" });
export const reasonText = css({ fill: "oklch(0.96 0.05 25)", fontSize: "11px", letterSpacing: "0.03em", fontWeight: "600" });
export const svgChip = css({ fill: "token(colors.bgPanel)", stroke: "token(colors.amber)", strokeWidth: "1.1" });
export const svgChipText = css({ fill: "token(colors.amber)", fontSize: "10.5px", letterSpacing: "0.02em" });

// --- live + teardown overlays (5g G5) ----------------------------------------
// t14c — terminal integration conflict: a STOP (flash 3× → steady), visually heavier than the
// recoverable Gate. Source stays put (all-or-nothing); paired with NO recovery chips (human-only).
// t14c terminal STOP. GSAP (data-fx='stop') flashes it ×3 then steady; static at rest under !animate.
export const stopBar = css({
  fill: "token(colors.alarm)",
  stroke: "oklch(0.96 0.05 25)",
  strokeWidth: "1.2",
});
export const stopText = css({
  fill: "token(colors.bg)",
  fontSize: "11px",
  fontWeight: "700",
  letterSpacing: "0.14em",
});

// t18 — abandon: the enclosure (canvas) dissolves to a dim, desaturated ghost while the record
// banner above it stays legible. A flex passthrough so the svg keeps its flex:1 sizing.
// t18 abandon: the enclosure (canvas) dissolves to a dim, desaturated ghost. Motion (EnclosureProcessMap)
// owns the opacity + grayscale fade; this recipe is the layout passthrough only (keeps the svg's flex:1).
export const dissolveShell = css({
  display: "flex",
  flexDirection: "column",
  flex: "1",
  minHeight: "0",
});
export const abandonRecord = css({
  display: "flex",
  alignItems: "center",
  gap: "0.4rem",
  padding: "0.3rem 0.6rem",
  border: "1px dashed token(colors.dormant)",
  borderRadius: "3px",
  color: "muted",
  fontSize: "0.74rem",
  letterSpacing: "0.04em",
});

// 5h H4 — cleanup teardown: the SUCCESS dissolve (landed, now retiring into the official line). Reuses
// `dissolveShell` for the canvas, but the record reads success — solid mint, not abandon's dashed-dormant.
export const cleanupRecord = css({
  // 5k F6 — overlay the canvas top instead of sitting in the column flow (which pushed the whole canvas
  // DOWN when the banner popped in). Absolute within the relative `stageContent`; a panel background keeps
  // it readable over the scene.
  position: "absolute",
  top: "0",
  left: "0",
  right: "0",
  zIndex: "3",
  display: "flex",
  alignItems: "center",
  gap: "0.4rem",
  padding: "0.3rem 0.6rem",
  border: "1px solid token(colors.mint)",
  borderRadius: "3px",
  color: "token(colors.mint)",
  backgroundColor: "token(colors.bgPanel)",
  fontSize: "0.74rem",
  letterSpacing: "0.04em",
});

// --- 5h H2: closeout train + landing integration --------------------------------
// T13 closeout train — the known closeout order (code → onboarding → quality → memory → ledger) as a
// derived 5-beat strip on closeout-pending (5f §9). Each beat group sweeps in via `closeoutSweep` (with
// a per-beat animation-delay set inline in the canvas); the global effects=off freeze settles it to the
// all-done strip. mint = the settled/done look (colour parity with the green=active engine palette, G5).
// Bare caption over the textured backdrop (no chip plate), so it needs real contrast: `ink` (not the
// dim `muted`) at 10px reads cleanly while the neutral-vs-mint tone keeps it a caption for the green beats.
export const closeoutTrainLabel = css({ fill: "token(colors.ink)", fontSize: "10px", letterSpacing: "0.06em" });
export const closeoutRail = css({ stroke: "token(colors.mint)", strokeWidth: "1.4", opacity: "0.35", strokeDasharray: "2 4" });
export const closeoutBeat = css({
  fill: "oklch(0.24 0.04 160)",
  stroke: "token(colors.mint)",
  strokeWidth: "1",
  opacity: "0.95",
});
export const closeoutBeatLabel = css({ fill: "token(colors.mint)", fontSize: "9.5px", letterSpacing: "0.02em" });

// --- 5h H3: the remote/PR strip beyond the official line (T15 code PR+push, T16 carryover) --------
// The upstream the official line reports into — origin/<feat>, the PR, origin/main, origin/mem-main —
// read left→right in the governed order: code lands first (feat → PR → main), memory carries over
// AFTER (mem-main). Each ref is a state chip honest to its factState: `planned` = dashed/muted
// ("expected, not yet"; the PR is never shown live until observed — honest-motion §4), `live` = solid
// amber (observed, in-flight), `done` = mint (a landed tip/merge/push — colour parity with the
// green-active engine + closeout-done palette), `stale` = dashed alarm (last truthful observation,
// no longer current). Stale is static and never drives an active landing-flow packet. The fill/stroke
// transition is the only motion (a
// projection state flip), frozen to the settled end-state under html[data-effects=off] (index.css).
// The strip header + the wiring between chips. Chips are sized + typed as peers of the branch nodes so
// they read at the same scale; the connectors give the landing chain a visible path (solid amber for the
// code refs feat→PR→main, dashed for the code→memory carryover handoff — "memory after").

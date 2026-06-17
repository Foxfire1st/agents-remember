// The Engine Room visual language as Panda recipes (slice 5e, 05e §8/§9.3): state and
// severity are carried by COLOUR (note 08), never by ad-hoc chrome, and every animation is
// one of the shared global keyframes (freezable by html[data-effects="off"]). One recipe per
// semantic axis — process health, fact state, conduit state, engine runtime — so the truth
// always comes from the model, never from a class name alone.

import { css, cva } from "../../../styled-system/css";

// --- layout ------------------------------------------------------------------

// Full-bleed Engine Room layout (5f S1, §4.2): a header strip over a 3-zone full-width grid —
// enclosure stack list | the animated pod stage | boot timeline + diagnostics on the RIGHT.
export const roomShell = css({
  display: "flex",
  flexDirection: "column",
  gap: "0.6rem",
  flex: "1",
  minHeight: "0",
});

export const roomGrid = css({
  display: "grid",
  gridTemplateColumns: "minmax(230px, 20rem) minmax(0, 1fr) minmax(280px, 22rem)",
  gap: "0.7rem",
  flex: "1",
  minHeight: "0",
  alignItems: "stretch",
});

// The pod stage — the large, full-height centre where all §6–§7 motion plays.
export const roomStage = css({
  display: "flex",
  flexDirection: "column",
  minWidth: "0",
  minHeight: "0",
});

// A side zone (left stack list / right boot+diagnostics) that scrolls on its own.
export const roomZone = css({
  display: "flex",
  flexDirection: "column",
  gap: "0.6rem",
  minWidth: "0",
  minHeight: "0",
  overflowY: "auto",
});

// --- room header (selected enclosure · health · phase · next action + master-caution) ---------
export const roomHeader = css({
  display: "flex",
  flexWrap: "wrap",
  alignItems: "center",
  gap: "0.6rem",
  padding: "0.4rem 0.6rem",
  border: "1px solid token(colors.grid)",
  borderRadius: "3px",
  borderLeftWidth: "3px",
  borderLeftColor: "amber",
});

export const roomHeaderName = css({ color: "ink", fontSize: "0.85rem", fontWeight: "600" });
export const roomHeaderMeta = css({
  display: "flex",
  flexWrap: "wrap",
  alignItems: "center",
  gap: "0.45rem",
  color: "muted",
  fontSize: "0.72rem",
});
export const roomHeaderSpacer = css({ flex: "1" });
export const roomHeaderNext = css({ color: "cyan", fontSize: "0.72rem" });

// The master-caution badge lifted into the room header while the rails are hidden (§4.1).
export const roomCaution = cva({
  base: {
    display: "inline-flex",
    alignItems: "center",
    gap: "0.3rem",
    fontSize: "0.72rem",
    letterSpacing: "0.04em",
    paddingInline: "0.4rem",
    paddingBlock: "0.1rem",
    borderRadius: "2px",
    borderWidth: "1px",
    borderStyle: "solid",
  },
  variants: {
    sev: {
      clear: { color: "mint", borderColor: "mint" },
      info: { color: "cyan", borderColor: "cyan" },
      warn: { color: "amber", borderColor: "amber" },
      alarm: { color: "alarm", borderColor: "alarm", animation: "pulse 0.6s steps(1) infinite" },
    },
  },
});

export const officialStrip = css({
  display: "flex",
  flexWrap: "wrap",
  alignItems: "center",
  gap: "0.6rem",
  padding: "0.5rem 0.6rem",
  border: "1px solid token(colors.grid)",
  borderRadius: "3px",
  borderLeftWidth: "3px",
  borderLeftColor: "amber",
  marginBottom: "0.6rem",
});

export const sectionLabel = css({
  color: "muted",
  fontSize: "0.7rem",
  letterSpacing: "0.08em",
  textTransform: "uppercase",
});

// --- enclosure stack list ----------------------------------------------------

export const stackList = css({
  display: "grid",
  gap: "0.4rem",
  listStyle: "none",
  margin: "0",
  padding: "0",
  minWidth: "0", // shrink within the grid track so items can ellipsize, not overflow
  maxHeight: "100%",
  overflowX: "hidden", // vertical scroll only; never a horizontal scrollbar
  overflowY: "auto",
  outline: "none",
});

export const stackItem = cva({
  base: {
    display: "grid",
    gap: "0.25rem",
    minWidth: "0",
    padding: "0.45rem 0.55rem",
    border: "1px solid token(colors.grid)",
    borderLeftWidth: "3px",
    borderRadius: "3px",
    cursor: "pointer",
    background: "bg",
    transition: "border-color 0.15s ease, background 0.15s ease",
    _hover: { borderColor: "muted" },
    _selected: { background: "bgPanel", borderColor: "amber" },
    _focusVisible: { outline: "1px solid token(colors.amber)", outlineOffset: "1px" },
  },
  variants: {
    health: {
      nominal: { borderLeftColor: "amber" },
      running: { borderLeftColor: "cyan" },
      blocked: { borderLeftColor: "alarm" },
      failed: { borderLeftColor: "alarm" },
      stale: { borderLeftColor: "alarm" },
      skipped: { borderLeftColor: "muted" },
      unknown: { borderLeftColor: "dormant" },
      complete: { borderLeftColor: "mint" },
    },
  },
});

export const stackItemHead = css({
  display: "flex",
  alignItems: "center",
  gap: "0.4rem",
  justifyContent: "space-between",
  minWidth: "0", // let the name column shrink/ellipsize so the phase pill never clips
});

export const stackTaskName = css({
  color: "ink",
  fontSize: "0.82rem",
  whiteSpace: "nowrap",
  overflow: "hidden",
  textOverflow: "ellipsis",
});

// The repo label sits on its own line above the status chips so the chip row reads as a clean group.
export const stackRepo = css({
  color: "muted",
  fontSize: "0.68rem",
  whiteSpace: "nowrap",
  overflow: "hidden",
  textOverflow: "ellipsis",
  minWidth: "0",
});

export const stackMeta = css({
  display: "flex",
  flexWrap: "wrap",
  alignItems: "center",
  gap: "0.35rem",
  color: "muted",
  fontSize: "0.68rem",
});

// --- health / state dots & chips ---------------------------------------------

export const healthDot = cva({
  base: {
    display: "inline-block",
    width: "0.62em",
    height: "0.62em",
    borderRadius: "full",
    flexShrink: "0",
    background: "muted",
  },
  variants: {
    health: {
      nominal: { background: "amber" },
      running: { background: "cyan" },
      blocked: { background: "alarm" },
      failed: { background: "alarm", animation: "pulse 0.6s steps(1) infinite" },
      stale: { background: "alarm", opacity: "0.55" },
      skipped: { background: "muted" },
      unknown: { background: "dormant" },
      complete: { background: "mint" },
    },
  },
});

export const chip = css({
  display: "inline-flex",
  alignItems: "center",
  gap: "0.25rem",
  fontSize: "0.66rem",
  paddingInline: "0.4rem",
  paddingBlock: "0.08rem",
  borderRadius: "2px",
  border: "1px solid token(colors.grid)",
  color: "muted",
  whiteSpace: "nowrap",
});

export const phaseChip = cva({
  base: {
    display: "inline-flex",
    alignItems: "center",
    fontSize: "0.66rem",
    paddingInline: "0.4rem",
    paddingBlock: "0.08rem",
    borderRadius: "2px",
    borderWidth: "1px",
    borderStyle: "solid",
    letterSpacing: "0.03em",
    whiteSpace: "nowrap", // never wrap the phase ("provider-setup") in the stack-item head row
    flexShrink: "0",
  },
  variants: {
    health: {
      nominal: { borderColor: "amber", color: "amber" },
      running: { borderColor: "cyan", color: "cyan" },
      blocked: { borderColor: "alarm", color: "alarm" },
      failed: { borderColor: "alarm", color: "alarm" },
      stale: { borderColor: "alarm", color: "alarm" },
      skipped: { borderColor: "muted", color: "muted" },
      unknown: { borderColor: "dormant", color: "dormant" },
      complete: { borderColor: "mint", color: "mint" },
    },
  },
});

// Fact-state honesty: observed = solid; derived = dashed (recorded, not on disk);
// planned = dotted; missing = ghosted; not-applicable = faint.
export const factChip = cva({
  base: {
    display: "inline-flex",
    alignItems: "center",
    gap: "0.2rem",
    fontSize: "0.62rem",
    paddingInline: "0.35rem",
    paddingBlock: "0.05rem",
    borderRadius: "2px",
    borderWidth: "1px",
    borderStyle: "solid",
    textTransform: "uppercase",
    letterSpacing: "0.05em",
  },
  variants: {
    factState: {
      observed: { borderStyle: "solid", borderColor: "mint", color: "mint" },
      derived: { borderStyle: "dashed", borderColor: "amber", color: "amber" },
      planned: { borderStyle: "dotted", borderColor: "muted", color: "muted" },
      missing: { borderStyle: "dotted", borderColor: "dormant", color: "dormant", opacity: "0.7" },
      "not-applicable": { borderStyle: "solid", borderColor: "grid", color: "dormant" },
    },
  },
});

// --- process-map nodes & conduits --------------------------------------------

export const mapGrid = css({
  display: "grid",
  gridTemplateColumns: "1fr auto 1fr",
  gridTemplateAreas: '"official coupler worktree"',
  gap: "0.5rem",
  alignItems: "stretch",
  padding: "0.6rem",
  border: "1px solid token(colors.grid)",
  borderRadius: "3px",
  background: "bg",
  md: { gridTemplateColumns: "1fr auto 1fr" },
});

export const lane = css({
  display: "grid",
  gap: "0.5rem",
  alignContent: "start",
  minWidth: "0",
});

export const nodeBox = cva({
  base: {
    display: "grid",
    gap: "0.2rem",
    padding: "0.45rem 0.55rem",
    borderWidth: "1px",
    borderStyle: "solid",
    borderRadius: "3px",
    background: "bgPanel",
    minWidth: "0",
  },
  variants: {
    factState: {
      observed: { borderColor: "amber" },
      derived: { borderStyle: "dashed", borderColor: "amber", opacity: "0.85" },
      planned: { borderStyle: "dotted", borderColor: "muted", opacity: "0.7" },
      missing: { borderStyle: "dotted", borderColor: "dormant", opacity: "0.6" },
      "not-applicable": { borderColor: "grid", opacity: "0.5" },
    },
  },
});

export const nodeLabel = css({
  color: "muted",
  fontSize: "0.66rem",
  letterSpacing: "0.05em",
  textTransform: "uppercase",
});

export const nodeBranch = css({
  color: "ink",
  fontSize: "0.76rem",
  wordBreak: "break-all",
});

export const nodeCommit = css({ color: "muted", fontSize: "0.68rem" });

export const couplerLane = css({
  gridArea: "coupler",
  display: "grid",
  alignContent: "center",
  justifyItems: "center",
  gap: "0.3rem",
  paddingInline: "0.2rem",
});

export const couplerBar = css({
  writingMode: "vertical-rl",
  textOrientation: "mixed",
  textAlign: "center",
  fontSize: "0.62rem",
  letterSpacing: "0.08em",
  color: "amber",
  border: "1px solid token(colors.amber)",
  borderRadius: "3px",
  padding: "0.4rem 0.2rem",
  background: "bgPanel",
});

// SVG conduit (5f S0). The lane connector is now an SVG primitive (was a 2px <div> span) so later
// slices can draw it on (stroke-dashoffset), carry travelling flow chevrons, and reroute. `conduitSvg`
// sizes the <svg> like the old span; `conduitLine` colours the <line> by state — colour parity with
// the prior recipe (running/planned read as dashed, failed flickers via the global `pulse` keyframe,
// so the determinism freeze still applies). State always comes from the model, never a class alone.
export const conduitSvg = css({
  display: "block",
  width: "100%",
  height: "2px",
  alignSelf: "center",
  overflow: "visible",
});

export const conduitLine = cva({
  base: { stroke: "token(colors.grid)", strokeWidth: "2", fill: "none" },
  variants: {
    state: {
      nominal: { stroke: "token(colors.amber)" },
      complete: { stroke: "token(colors.mint)" },
      running: { stroke: "token(colors.cyan)", strokeDasharray: "6 6" },
      blocked: { stroke: "token(colors.alarm)" },
      failed: { stroke: "token(colors.alarm)", animation: "pulse 0.6s steps(1) infinite" },
      stale: { stroke: "token(colors.alarm)", opacity: "0.55" },
      skipped: { stroke: "token(colors.grid)" },
      planned: { stroke: "token(colors.muted)", strokeDasharray: "3 5", opacity: "0.6" },
      unknown: { stroke: "token(colors.dormant)", opacity: "0.5" },
    },
  },
});

// --- fleeting (pre-contract blocked-start) banner (5f S2, §2.1) ---------------
// A provisional enclosure born blocked: shown in the ghost/alarm register, honestly stating that
// creation is gated and the contract is not yet written, plus the recovery choice. Provisional ≠ fake.
export const fleetingBanner = css({
  display: "grid",
  gap: "0.2rem",
  padding: "0.4rem 0.55rem",
  border: "1px dashed token(colors.alarm)",
  borderRadius: "3px",
  background: "bgPanel",
  opacity: "0.92",
});
export const fleetingLabel = css({
  color: "alarm",
  fontSize: "0.66rem",
  letterSpacing: "0.06em",
  textTransform: "uppercase",
});
export const fleetingReason = css({ color: "ink", fontSize: "0.74rem", wordBreak: "break-word" });
export const fleetingChoices = css({ display: "flex", flexWrap: "wrap", gap: "0.3rem" });
export const fleetingChoice = css({
  color: "cyan",
  fontSize: "0.68rem",
  border: "1px solid token(colors.cyan)",
  borderRadius: "2px",
  paddingInline: "0.35rem",
  paddingBlock: "0.05rem",
});

// Power-up flow packet (5f S4, T8/T9): the travelling energy packet that runs along a conduit while
// it carries real flow (DB clone / index seed). Cyan; GSAP animates its cx, gated by useShouldAnimate.
export const conduitChevron = css({ fill: "token(colors.cyan)" });

export const engineRow = css({
  display: "flex",
  alignItems: "center",
  gap: "0.45rem",
});

export const engineSilhouette = cva({
  base: {
    width: "1.4rem",
    height: "2.4rem",
    borderWidth: "1px",
    borderStyle: "solid",
    borderColor: "amber",
    borderRadius: "2px",
    background:
      "repeating-linear-gradient(0deg, token(colors.bg) 0 4px, transparent 4px 8px)",
    flexShrink: "0",
  },
  variants: {
    runtimeState: {
      // Active engine = GREEN (booted, running) — green/go, distinct from amber=warning, red=error, empty=off.
      nominal: {
        borderColor: "mint",
        background: "repeating-linear-gradient(0deg, token(colors.mint) 0 4px, transparent 4px 8px)",
      },
      configured: { borderColor: "dormant", opacity: "0.7" }, // configured but not running → empty/off
      indexing: {
        borderColor: "cyan",
        background:
          "repeating-linear-gradient(0deg, token(colors.cyan) 0 4px, transparent 4px 8px)",
      },
      down: { borderColor: "alarm", animation: "pulse 0.6s steps(1) infinite" },
      unknown: { borderColor: "dormant", borderStyle: "dotted", opacity: "0.6" },
    },
  },
});

export const engineMeta = css({ display: "grid", gap: "0.1rem", minWidth: "0" });
export const engineName = css({ color: "ink", fontSize: "0.74rem" });
export const engineState = css({ color: "muted", fontSize: "0.66rem" });

// --- boot timeline -----------------------------------------------------------

export const timeline = css({
  display: "grid",
  gap: "0.25rem",
  padding: "0.5rem 0.6rem",
  border: "1px solid token(colors.grid)",
  borderRadius: "3px",
});

export const timelineStep = cva({
  base: {
    display: "flex",
    alignItems: "center",
    gap: "0.45rem",
    fontSize: "0.72rem",
    color: "muted",
  },
  variants: {
    state: {
      complete: { color: "mint" },
      current: { color: "cyan" },
      running: { color: "cyan" },
      failed: { color: "alarm" },
      blocked: { color: "alarm" },
      pending: { color: "muted", opacity: "0.65" },
      skipped: { color: "muted", opacity: "0.5" },
    },
  },
});

export const timelineMark = cva({
  base: {
    width: "0.55em",
    height: "0.55em",
    borderRadius: "full",
    flexShrink: "0",
    background: "muted",
  },
  variants: {
    state: {
      complete: { background: "mint" },
      current: { background: "cyan", animation: "pulse 0.9s steps(1) infinite" },
      running: { background: "cyan", animation: "pulse 0.9s steps(1) infinite" },
      failed: { background: "alarm", animation: "pulse 0.6s steps(1) infinite" },
      blocked: { background: "alarm" },
      pending: { background: "dormant", opacity: "0.6" },
      skipped: { background: "grid" },
    },
  },
});

// --- diagnostics -------------------------------------------------------------

export const diagPanel = css({
  display: "grid",
  gap: "0.5rem",
  padding: "0.55rem 0.65rem",
  border: "1px solid token(colors.grid)",
  borderRadius: "3px",
  alignContent: "start", // rows stay top-aligned when the box grows
  flex: "1", // fill the right zone's remaining height (the boot timeline stays fixed) — stretch to the floor
});

export const diagRow = css({
  display: "grid",
  gridTemplateColumns: "minmax(7rem, auto) 1fr",
  gap: "0.5rem",
  fontSize: "0.72rem",
});

export const diagKey = css({ color: "muted" });
export const diagValue = css({ color: "ink", wordBreak: "break-word" });

export const missingNotice = css({
  display: "grid",
  gap: "0.2rem",
  padding: "0.45rem 0.55rem",
  border: "1px dashed token(colors.dormant)",
  borderRadius: "3px",
  color: "muted",
  fontSize: "0.7rem",
});

export const missingTitle = css({
  color: "amber",
  fontSize: "0.68rem",
  letterSpacing: "0.05em",
  textTransform: "uppercase",
});

export const phaseLineList = css({
  display: "grid",
  gap: "0.15rem",
  margin: "0",
  padding: "0",
  listStyle: "none",
  fontSize: "0.68rem",
});

export const emptyState = css({
  color: "muted",
  fontSize: "0.8rem",
  padding: "1rem",
  textAlign: "center",
});

export const actionRow = css({
  display: "flex",
  flexWrap: "wrap",
  gap: "0.4rem",
  alignItems: "center",
});

// --- pod-stage bird's-eye (5g G1) --------------------------------------------
// The two-world SVG scene: world labels, the dashed enclosure, branch nodes, podracer engine
// gauges, the warp coupler, and the flow conduits. G1 is the static frame (nominal end-state) —
// no keyframes here yet; the boot/failure motion (draw-on, center-out fill, gates) lands in G2+.
// Colour still carries state (note 08): one recipe per semantic axis, driven off the model.

export const sceneSvg = css({
  display: "block",
  width: "100%",
  flex: "1",
  minHeight: "0",
  overflow: "visible",
});

export const worldLabel = css({
  fill: "token(colors.muted)",
  fontSize: "14px",
  letterSpacing: "0.16em",
  textTransform: "uppercase",
});

export const enclosureBorder = css({
  fill: "none",
  stroke: "token(colors.amber)",
  strokeWidth: "1.8",
  strokeDasharray: "9 7",
  strokeLinecap: "round",
  opacity: "0.5",
});

// Branch node (official / worktree, code / memory) — fact-state honesty carried by the stroke.
export const svgNodeBox = cva({
  base: { fill: "token(colors.bgPanel)", strokeWidth: "1.5" },
  variants: {
    factState: {
      observed: { stroke: "token(colors.amber)" },
      derived: { stroke: "token(colors.cyan)", strokeDasharray: "5 4" },
      planned: { stroke: "token(colors.muted)", strokeDasharray: "2 4", opacity: "0.7" },
      missing: { stroke: "token(colors.dormant)", strokeDasharray: "2 4", opacity: "0.6" },
      "not-applicable": { stroke: "token(colors.grid)", opacity: "0.5" },
    },
  },
});

export const svgNodeLabel = css({
  fill: "token(colors.muted)",
  fontSize: "11px",
  letterSpacing: "0.05em",
  textTransform: "uppercase",
});
export const svgNodeTitle = css({ fill: "token(colors.ink)", fontSize: "14px", fontWeight: "600" });
export const svgNodeMeta = css({ fill: "token(colors.muted)", fontSize: "11px" });

// Podracer engine gauge — outer column coloured by runtime; the charge fill shows the settled
// (nominal) energy level. The center-out boot-fill GROWTH is G2 (this is the static end-state).
export const engineGaugeOut = cva({
  base: { fill: "token(colors.bg)", strokeWidth: "1.5", opacity: "0.95" },
  variants: {
    runtimeState: {
      nominal: { stroke: "token(colors.mint)" }, // active = green
      configured: { stroke: "token(colors.dormant)", opacity: "0.6" }, // inactive = empty/off
      indexing: { stroke: "token(colors.cyan)" },
      // down = FAULT → flicker (≤3/s, distinct from the STEADY blocked gate). Isolated to this engine.
      down: { stroke: "token(colors.alarm)", animation: "pulse 0.5s steps(1) infinite" },
      unknown: { stroke: "token(colors.dormant)", strokeDasharray: "4 3", opacity: "0.6" },
    },
  },
});

// Reindex reroute (t9c, seedFallback) — an AMBER center-out pulse (a fallback, NOT the red fault).
export const engineReindexCharge = css({
  fill: "token(colors.amber)",
  opacity: "0.85",
  transformBox: "fill-box",
  transformOrigin: "center",
  animation: "chargeSweep 1.5s ease-out infinite",
});

// The reindex OUTER stays amber (warning) so a rerouting engine reads amber-on-amber, not green-nominal.
export const engineReindexOut = css({
  fill: "token(colors.bg)",
  stroke: "token(colors.amber)",
  strokeWidth: "1.5",
  opacity: "0.95",
});

export const engineCharge = cva({
  // transform-box/origin so the charge scaleY (chargeSweep keyframe) grows center-out, not bottom-up.
  base: { transformBox: "fill-box", transformOrigin: "center" },
  variants: {
    runtimeState: {
      nominal: { fill: "token(colors.mint)", opacity: "0.5" }, // active = a clear green fill ("on"), not a faint amber
      configured: { fill: "token(colors.dormant)", opacity: "0" }, // inactive = empty (no fill)
      // indexing/booting: a center-out charge sweep (frozen to a settled full charge under effects=off).
      indexing: { fill: "token(colors.cyan)", opacity: "0.85", animation: "chargeSweep 1.5s ease-out infinite" },
      down: { fill: "token(colors.alarm)", opacity: "0.5" },
      unknown: { fill: "token(colors.dormant)", opacity: "0" },
    },
  },
});

export const engineDiv = css({ stroke: "token(colors.bg)", strokeWidth: "2", opacity: "0.85" });
export const engineGaugeLabel = css({
  fill: "token(colors.ink)",
  fontSize: "12px",
  letterSpacing: "0.1em",
  fontWeight: "600",
});

// Podracer gauge detail (ported 1:1 from podstage.html .e-spine / .e-petal): a faint centre spine
// plus the fanned flank petals — the fine lines that read each unit as an engine, not a bar. The
// spine is constant; the petals follow the engine's runtime colour so they stay state-honest.
export const engineSpine = css({ stroke: "token(colors.amber)", strokeWidth: "0.8", opacity: "0.28" });
export const enginePetal = cva({
  base: { strokeWidth: "1.4", strokeLinecap: "round" },
  variants: {
    runtimeState: {
      nominal: { stroke: "token(colors.mint)", opacity: "0.6" },
      configured: { stroke: "token(colors.dormant)", opacity: "0" }, // off → no petals
      indexing: { stroke: "token(colors.cyan)", opacity: "0.6" },
      down: { stroke: "token(colors.alarm)", opacity: "0.6" },
      unknown: { stroke: "token(colors.dormant)", opacity: "0" },
    },
  },
});

// Official-line provider→branch wiring (podstage.html .wire): the workspace CGC/GrepAI feeding the
// official code/memory nodes. Structural truth, present whenever the official engines exist.
export const officialWire = css({
  fill: "none",
  stroke: "token(colors.amber)",
  strokeWidth: "2",
  opacity: "0.8",
  strokeLinecap: "round",
});

// Canopy housing (podstage.html .canopy): the decorative HUD frame — a double bevel rim, the four L
// corner brackets, and the edge ticks. Pure amber line-art at the stage edges; the group's stroke is
// inherited by its children, while per-element strokeWidth/opacity are set inline. Carries no state.
export const canopyStroke = css({ fill: "none", stroke: "token(colors.amber)" });

// Lane annotation flags (podstage.html #ledger / #hist): small labelled markers on the worktree +
// official landing lanes. Descriptive lane-role labels — the live status lives in the diagnostics
// panel + node fact-states — toned per lane: ledger=amber, historical=dormant.
export const laneFlag = cva({
  base: { strokeWidth: "1" },
  variants: {
    tone: {
      ledger: { fill: "oklch(0.24 0.03 250)", stroke: "token(colors.amber)" },
      historical: { fill: "oklch(0.18 0.02 25)", stroke: "token(colors.dormant)" },
    },
  },
});
export const laneFlagText = cva({
  base: { fontSize: "11px" },
  variants: {
    tone: {
      ledger: { fill: "token(colors.amber)" },
      historical: { fill: "token(colors.dormant)" },
    },
  },
});

// Warp coupler — the contract binding code===memory in the worktree (bound when external memory).
export const warpCouplerG = cva({
  base: {},
  variants: { bound: { true: { opacity: "1" }, false: { opacity: "0.3" } } },
});
export const warpCouplerBar = css({
  stroke: "token(colors.amber)",
  strokeWidth: "9",
  strokeLinecap: "round",
  opacity: "0.95",
});
export const warpCouplerNode = css({
  fill: "token(colors.amber)",
  stroke: "token(colors.amber)",
  strokeWidth: "1.2",
});
export const warpCouplerLabel = css({ fill: "token(colors.amber)", fontSize: "11px", letterSpacing: "0.04em" });

// Flow conduit — the seed/clone/integrate/sync lanes; colour parity with `conduitLine` (5e), now
// on a positioned SVG path. The travelling packet + draw-on tween return in G2.
export const flowConduit = cva({
  base: { fill: "none", strokeWidth: "2.4" },
  variants: {
    state: {
      nominal: { stroke: "token(colors.amber)", opacity: "0.8" },
      complete: { stroke: "token(colors.amber)", opacity: "0.6" },
      // running (seed/clone): draws on (needs pathLength=100 on the path) then settles drawn.
      running: { stroke: "token(colors.cyan)", strokeDasharray: "100 100", animation: "conduitDraw 0.6s cubic-bezier(.25,1,.4,1)" },
      blocked: { stroke: "token(colors.alarm)" },
      failed: { stroke: "token(colors.alarm)" },
      stale: { stroke: "token(colors.alarm)", opacity: "0.55" },
      skipped: { stroke: "token(colors.grid)", opacity: "0.4" },
      planned: { stroke: "token(colors.muted)", strokeDasharray: "3 5", opacity: "0.5" },
      unknown: { stroke: "token(colors.dormant)", opacity: "0.4" },
    },
  },
});

// The travelling flow packet — a cyan energy dot that runs along a seeding/cloning conduit (its
// offset-path is set per-conduit in EnclosureCanvas). Hidden under effects=off (see index.css freeze).
export const flowPacket = css({ fill: "token(colors.cyan)", opacity: "0.95" });

// --- failure overlays (5g G3) ------------------------------------------------
// blocked = STEADY red gate over the blocked lane (a human choice required) — never the fault flicker
// (that's the engine, G4). Every blocked/fault raises the alarm-parity attention badge. A local reason
// badge (cyan-dot pointer + pill) states WHY at the lane; recovery chips offer the next action. All
// driven off node.health / edge.state / missingFacts / nextAction — colour-as-state, no inferred chrome.
export const gateBar = css({ fill: "token(colors.alarm)", opacity: "0.92" });
export const attnBadge = css({
  fill: "oklch(0.26 0.09 25)",
  stroke: "token(colors.alarm)",
  strokeWidth: "1.3",
  animation: "attnBreath 1.3s ease-in-out infinite",
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
export const stopBar = css({
  fill: "token(colors.alarm)",
  stroke: "oklch(0.96 0.05 25)",
  strokeWidth: "1.2",
  animation: "stopFlash 0.5s steps(1) 3",
});
export const stopText = css({
  fill: "token(colors.bg)",
  fontSize: "11px",
  fontWeight: "700",
  letterSpacing: "0.14em",
});

// t18 — abandon: the enclosure (canvas) dissolves to a dim, desaturated ghost while the record
// banner above it stays legible. A flex passthrough so the svg keeps its flex:1 sizing.
export const dissolveShell = css({
  display: "flex",
  flexDirection: "column",
  flex: "1",
  minHeight: "0",
  opacity: "0.4",
  filter: "grayscale(0.75)",
  transition: "opacity 0.6s ease, filter 0.6s ease",
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

// --- G6: atmospheric blueprint backdrop (the faint amber-tinted boomerang) ----
// Mounts behind the scene, gated to effects-on (useShouldAnimate) so it is absent + lazy under
// reduced-motion / data-effects=off. aria-hidden + pointer-events:none — pure atmosphere, never state.
export const backdrop = css({ position: "absolute", inset: "0", zIndex: "0", pointerEvents: "none", overflow: "hidden" });
export const backdropVideo = css({
  width: "100%",
  height: "100%",
  objectFit: "cover",
  opacity: "0.14",
  filter: "grayscale(1) sepia(1) saturate(2.6) hue-rotate(6deg) brightness(0.85) contrast(1.05)",
  mixBlendMode: "screen",
});
// The scene content sits in its own layer above the backdrop.
export const stageContent = css({
  position: "relative",
  zIndex: "1",
  display: "flex",
  flexDirection: "column",
  gap: "0.45rem",
  flex: "1",
  minWidth: "0",
  minHeight: "0",
});

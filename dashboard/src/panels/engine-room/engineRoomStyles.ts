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

// Static layout only (05f §8): all canvas motion is GSAP (useEngineTimeline) + Motion (EnclosureCanvas),
// never CSS. The old global `& g,& rect,…{ transition }` substrate (ported from podstage.html's #scene
// trick) is removed — CSS cannot stage a sequence or animate an unmounting node, which is exactly what
// broke the tear-down de-materialise + the conditional landing apparatus.
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

// The dashed worktree-enclosure border. Motion (EnclosureCanvas) owns its opacity — 0.5 at rest, 0 while
// the shell hasn't materialised / has collapsed — so the build-up draws it in and the teardown collapses it.
export const enclosureBorder = css({
  fill: "none",
  stroke: "token(colors.amber)",
  strokeWidth: "1.8",
  strokeDasharray: "9 7",
  strokeLinecap: "round",
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

// 05o T1B — pruned/stale node: a real-but-disposed base node reads DORMANT (the spec §3 "pruned/retired"):
// a desaturated dormant stroke + a dark muted fill, distinct from `planned`/`missing` (dotted, not-yet) and
// from a live amber box. Projection-driven (applied to the stale base node when local main is behind upstream
// in the stale-base block), static. Mirrors the spec `.node.pruned .box` (stroke var(--dormant), dark fill).
export const prunedNode = css({
  stroke: "token(colors.dormant)",
  fill: "oklch(0.18 0.02 25)",
  strokeDasharray: "3 3",
  opacity: "0.8",
});

// Podracer engine gauge — outer column coloured by runtime; the charge fill shows the settled
// (nominal) energy level. The center-out boot-fill GROWTH is G2 (this is the static end-state).
export const engineGaugeOut = cva({
  // 5o/05o — constant GOLD bezel, FLAT (no glow): the body charge carries runtime state, not the frame, so the
  // gold frame stays a quiet structural outline. FAULT is the one exception that re-colours the frame red (+ a
  // red glow) so a down engine is unmistakable. Matches the engine-room visual-language spec (docs/design/engine-room).
  base: {
    fill: "token(colors.bg)",
    stroke: "token(colors.amber)",
    strokeWidth: "2",
    opacity: "0.95",
  },
  variants: {
    runtimeState: {
      nominal: {}, // gold bezel
      configured: { opacity: "0.5" }, // materialised but drained: dim bezel
      indexing: {}, // gold bezel (the cyan body shows it's charging)
      // down = FAULT → frame goes red + a red glow + a GENTLE breathe (data-fx='fault', ~1.7s sine; never a strobe).
      down: { stroke: "token(colors.alarm)", filter: "drop-shadow(0 0 5px token(colors.alarm))" },
      unknown: { strokeDasharray: "4 3", opacity: "0.55" },
    },
  },
});

// Reindex reroute (t9c, seedFallback) — an AMBER center-out pulse (a fallback, NOT the red fault). GSAP
// (data-fx='reindex') drives the scaleY/opacity pulse; under !animate it rests at this charged amber bar.
export const engineReindexCharge = css({
  fill: "token(colors.amber)",
  opacity: "0.85",
  transformBox: "fill-box",
  transformOrigin: "center",
});

// The reindex OUTER stays amber (warning) so a rerouting engine reads amber-on-amber, not green-nominal.
export const engineReindexOut = css({
  fill: "token(colors.bg)",
  stroke: "token(colors.amber)",
  strokeWidth: "1.5",
  opacity: "0.95",
});

// The boot-fill charge rect. transform-box/origin make the scaleY grow CENTER-OUT (not bottom-up); the
// recipe carries only the static FILL as colour-as-state (cyan charging → mint "went green" → amber/alarm).
// Motion (EnclosureCanvas chargeMotion) owns the animated scaleY (the boot-fill growth) + opacity, so CSS
// and Motion never write the same property. Under !animate the rect mounts at the runtime end-state.
export const engineCharge = cva({
  base: { transformBox: "fill-box", transformOrigin: "center" },
  variants: {
    runtimeState: {
      // 5o glow pass — the charged body glows its state colour so the engine reads as powered, not flat.
      nominal: { fill: "token(colors.mint)", filter: "drop-shadow(0 0 4px token(colors.mint))" }, // healthy green
      configured: { fill: "token(colors.cyan)" }, // materialised, drained (dim) — no glow
      indexing: { fill: "token(colors.cyan)", filter: "drop-shadow(0 0 4px token(colors.cyan))" }, // charging cyan
      down: { fill: "token(colors.alarm)", filter: "drop-shadow(0 0 5px token(colors.alarm))" },
      unknown: { fill: "token(colors.dormant)" },
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
  // 05o — petals are now constant GOLD line-art, matching the always-amber `engineSpine`: they read the engine
  // as a podracer, they do not carry runtime state (the body charge + bezel do). Only PRESENCE varies by state
  // (an off/unknown engine fans no petals), so the colour is amber throughout and opacity is the state axis.
  base: { strokeWidth: "1.4", strokeLinecap: "round", stroke: "token(colors.amber)" },
  variants: {
    runtimeState: {
      nominal: { opacity: "0.6" },
      configured: { opacity: "0" }, // off → no petals
      indexing: { opacity: "0.6" },
      down: { opacity: "0.6" },
      unknown: { opacity: "0" },
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

// The worktree engine→branch wiring (mirror of officialWire) — but Motion (EnclosureCanvas) owns its
// opacity: it fades in when the engine materialises (B3) and out when the engine powers down (D5). So this
// variant carries NO opacity — a className `opacity` shadows Motion's animated value under initial=false
// (the inline animated value and the class fight, and the class wins on a static frame), which is exactly
// what left the wires dangling on the worktree side when no engine was present.
export const worktreeWire = css({
  fill: "none",
  stroke: "token(colors.amber)",
  strokeWidth: "2",
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

// Warp coupler — the contract binding code===memory in the worktree (bound when external memory). Motion
// (EnclosureCanvas) owns the coupler group's opacity (the bound dim + the build-up `visible` gate).
export const warpCouplerBar = css({
  stroke: "token(colors.amber)",
  strokeWidth: "9",
  strokeLinecap: "round",
  opacity: "0.95",
  filter: "drop-shadow(0 0 2px token(colors.amber))", // 5o glow pass — structural 2px (spec importance scale)
});
export const warpCouplerNode = css({
  fill: "token(colors.amber)",
  stroke: "token(colors.amber)",
  strokeWidth: "1.2",
});
export const warpCouplerLabel = css({ fill: "token(colors.amber)", fontSize: "11px", letterSpacing: "0.04em" });

// The ledger-coupler link icon (5h coupler fix) — a drawn chain-link glyph (two interlocking rings,
// amber line-art) replacing the contract node; reads as 🔗 but in the blueprint ink + can carry state.
export const warpLinkGlyph = css({ fill: "none", stroke: "token(colors.amber)", strokeWidth: "1.6" });
// Warp-core surge: two hot bands born at the link, splitting up + down (only when bound). GSAP
// (data-fx='surge' + data-dir) drives them; opacity 0 at rest so under !animate they're invisible (no
// settled state, like the flow packet). Ported from podstage.html.
export const warpSurge = css({
  stroke: "oklch(0.95 0.1 90)",
  strokeWidth: "7",
  strokeLinecap: "round",
  opacity: "0",
  filter: "drop-shadow(0 0 5px token(colors.amber))",
});

// 5h ledger popover — clicking a coupler's link glyph opens the memory.md lookup table (code⇄memory
// rows, this enclosure's row highlighted). The trigger is an SVG hit-rect over the glyph (a <button> can't
// live in SVG); the popover content is HTML in a React-Aria Dialog, portaled out of the <svg>.
// The coupler label is now a visible BUTTON (an SVG rect — a <button> can't live in svg): a faint amber
// chip that brightens on hover, so it reads as "click me" (5h feedback). The label text sits on top with
// pointer-events off, so clicks land on the rect.
export const ledgerButton = css({
  fill: "oklch(0.22 0.02 250 / 0.55)",
  stroke: "token(colors.amber)",
  strokeWidth: "1",
  cursor: "pointer",
  _hover: { fill: "oklch(0.3 0.04 250 / 0.85)" },
  _focusVisible: { outline: "none", stroke: "token(colors.cyan)", strokeWidth: "1.6" },
});
export const ledgerButtonLabel = css({
  fill: "token(colors.amber)",
  fontSize: "11px",
  letterSpacing: "0.03em",
  pointerEvents: "none",
});
export const ledgerCard = css({
  display: "grid",
  gap: "0.3rem",
  padding: "0.5rem 0.6rem",
  background: "bgPanel",
  border: "1px solid token(colors.amber)",
  borderRadius: "4px",
  boxShadow: "0 6px 20px oklch(0 0 0 / 0.5)",
  // widened for the Tier 2 6-column row (date | message | hash ⇄ hash | message | date); stays on-screen
  // on narrow viewports, messages ellipsize (ledgerMsg), and the Tier-3 ledgerScroll owns height.
  maxWidth: "min(92vw, 46rem)",
});
export const ledgerCardHead = css({
  color: "muted",
  fontSize: "0.62rem",
  letterSpacing: "0.06em",
  textTransform: "uppercase",
});
export const ledgerTable = css({
  borderCollapse: "collapse",
  fontSize: "0.72rem",
  color: "ink",
  "& td": { paddingInline: "0.35rem", paddingBlock: "0.12rem", whiteSpace: "nowrap" },
});
export const ledgerRowCss = cva({
  base: { color: "muted" },
  variants: {
    current: {
      true: { color: "amber", background: "oklch(0.24 0.03 250)", fontWeight: "600" },
    },
  },
});
export const ledgerMore = css({ color: "muted", fontSize: "0.64rem", fontStyle: "italic" });
// The rows scroll within a bounded height; the header + footer stay fixed. Collapsed (8 rows) is compact;
// expanding EXTENDS the frame to use the available height (≈ the full 25-row window), the inner scroll
// kicking in only if it still overflows the viewport (React-Aria keeps the portaled popover on-screen).
export const ledgerScroll = cva({
  base: { overflowY: "auto" },
  variants: {
    expanded: {
      true: { maxHeight: "min(72vh, 46rem)" },
      false: { maxHeight: "13rem" },
    },
  },
});
// The "▾ show N more" expand control at the bottom of the popover — collapsed (8) → expanded (≤25), in place.
export const ledgerShowMore = css({
  display: "block",
  width: "100%",
  textAlign: "left",
  background: "transparent",
  border: "none",
  borderTop: "1px solid token(colors.grid)",
  color: "cyan",
  fontSize: "0.66rem",
  fontFamily: "inherit",
  cursor: "pointer",
  paddingBlock: "0.3rem",
  marginTop: "0.1rem",
  _hover: { color: "amber" },
});
// Tier 2 row columns — date (muted, compact, tabular), message (truncates with ellipsis), the two hashes
// meeting the centre seam (code right-aligned, memory left-aligned, mono), and the ⇄ seam glyph itself.
export const ledgerDate = css({
  color: "muted",
  fontSize: "0.62rem",
  fontVariantNumeric: "tabular-nums",
  whiteSpace: "nowrap",
});
export const ledgerMsg = css({
  maxWidth: "12rem",
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
});
export const ledgerHashCode = css({ fontFamily: "mono", textAlign: "right" });
export const ledgerHashMem = css({ fontFamily: "mono", textAlign: "left" });
export const ledgerSeam = css({ color: "muted", paddingInline: "0.15rem", textAlign: "center" });

// Flow conduit — the seed/clone/integrate/sync lanes; colour parity with `conduitLine` (5e), now
// on a positioned SVG path. The travelling packet + draw-on tween return in G2.
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
// return conduit that is REFUSED (podstage `.refused`/`.refred`): cyan → white spark → its polarity colour
// → fade out. AMBER = a fallback/reroute (CGC seed refused → reindex), RED = a fault/conflict (GrepAI seed
// fault, integration conflict). Polarity is NEVER a class alone — it is read off the projection (edge.state
// failed→red / stale→amber, or edge.refusedPolarity on a `refused` edge) and carried as the cva variant.
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
// green-active engine + closeout-done palette). The fill/stroke transition is the only motion (a
// projection state flip), frozen to the settled end-state under html[data-effects=off] (index.css).
// The strip header + the wiring between chips. Chips are sized + typed as peers of the branch nodes so
// they read at the same scale; the connectors give the landing chain a visible path (solid amber for the
// code refs feat→PR→main, dashed for the code→memory carryover handoff — "memory after").
export const remoteStripHeader = css({
  fill: "token(colors.muted)",
  fontSize: "12.5px",
  letterSpacing: "0.14em",
  textTransform: "uppercase",
});
export const remoteConnector = css({
  fill: "none",
  stroke: "token(colors.amber)",
  strokeWidth: "2",
  opacity: "0.8",
  strokeLinecap: "round",
});
export const remoteConnectorCarry = css({
  fill: "none",
  stroke: "token(colors.muted)",
  strokeWidth: "2",
  opacity: "0.6",
  strokeDasharray: "5 5",
  strokeLinecap: "round",
});
export const remoteChip = cva({
  base: { strokeWidth: "1.4" },
  variants: {
    tone: {
      planned: { fill: "token(colors.bgPanel)", stroke: "token(colors.muted)", strokeDasharray: "4 5", opacity: "0.8" },
      live: { fill: "token(colors.bgPanel)", stroke: "token(colors.amber)" },
      done: { fill: "oklch(0.24 0.04 160)", stroke: "token(colors.mint)" },
    },
  },
});
export const remoteChipLabel = cva({
  base: { fontSize: "15px", letterSpacing: "0.02em", fontWeight: "600" },
  variants: {
    tone: {
      planned: { fill: "token(colors.muted)" },
      live: { fill: "token(colors.amber)" },
      done: { fill: "token(colors.mint)" },
    },
  },
});
export const remoteChipState = cva({
  base: { fontSize: "12px", letterSpacing: "0.02em" },
  variants: {
    tone: {
      planned: { fill: "token(colors.muted)", fontStyle: "italic" },
      live: { fill: "token(colors.muted)" },
      done: { fill: "token(colors.mint)", opacity: "0.85" },
    },
  },
});

// The PR badge — a distinct pill among the remote refs. open = amber outline (not yet merged);
// merged = mint-filled "merged". Never animated as live until observed (honest-motion §4).
export const prBadge = cva({
  base: { strokeWidth: "1.5" },
  variants: {
    state: {
      open: { fill: "token(colors.bgPanel)", stroke: "token(colors.amber)" },
      merged: { fill: "oklch(0.24 0.04 160)", stroke: "token(colors.mint)" },
    },
  },
});
export const prBadgeLabel = cva({
  base: { fontSize: "14px", letterSpacing: "0.03em", fontWeight: "600" },
  variants: {
    state: {
      open: { fill: "token(colors.amber)" },
      merged: { fill: "token(colors.mint)" },
    },
  },
});
export const prBadgeSub = cva({
  base: { fontSize: "12px", letterSpacing: "0.02em" },
  variants: {
    state: {
      open: { fill: "token(colors.muted)" },
      merged: { fill: "token(colors.mint)", opacity: "0.85" },
    },
  },
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
  // Vignette the video edges only (a radial mask): with the `screen` blend, the faded edges fall back
  // to the dark stage, concentrating the boomerang in the centre. Scoped to the <video>, so the SVG
  // scene layered above (`stageContent`, a higher z-index) is untouched.
  maskImage: "radial-gradient(ellipse at center, #000 42%, transparent 100%)",
  WebkitMaskImage: "radial-gradient(ellipse at center, #000 42%, transparent 100%)",
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

// Engine Room SVG-stage tokens: the two-world scene, branch nodes, podracer engine gauges, the warp
// coupler, wires, and lane flags. Static layout only — all canvas motion is GSAP + Motion.
import { css, cva } from "../../../styled-system/css";

export const sceneSvg = css({
  display: "block",
  width: "100%",
  flex: "1",
  minHeight: "0",
  overflow: "visible",
});

// Repeating transforms live in a sparse sibling SVG aligned to the structural scene's 1200×660
// viewBox. Chromium still lays out the animated SVG, but no longer relays out the text-heavy scene.
// Reusing the original SVG recipes preserves the exact paint and layering of each moving primitive.
export const fxOverlaySvg = css({
  position: "absolute",
  inset: "0",
  zIndex: "2",
  display: "block",
  width: "100%",
  height: "100%",
  overflow: "visible",
  pointerEvents: "none",
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
      stale: { stroke: "token(colors.alarm)", strokeDasharray: "4 4", opacity: "0.8" },
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
      missing: { stroke: "token(colors.alarm)", strokeDasharray: "5 4", opacity: "0.65" },
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
      missing: { fill: "token(colors.alarm)" },
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
      missing: { opacity: "0" },
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

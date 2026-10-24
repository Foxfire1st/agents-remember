// Engine Room SVG-stage tokens: the two-world scene, branch nodes, the warp coupler, and lane flags.
// Static layout only — all canvas motion is GSAP + Motion.
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

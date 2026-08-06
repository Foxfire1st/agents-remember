// Engine Room remote/landing-dock tokens: origin chips, PR merge badges, and the dock wiring.
import { css, cva } from "../../../styled-system/css";

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
      stale: { fill: "token(colors.bgPanel)", stroke: "token(colors.alarm)", strokeDasharray: "4 4", opacity: "0.8" },
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
      stale: { fill: "token(colors.alarm)" },
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
      stale: { fill: "token(colors.alarm)", fontStyle: "italic" },
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
      stale: { fill: "token(colors.bgPanel)", stroke: "token(colors.alarm)", strokeDasharray: "4 4", opacity: "0.8" },
    },
  },
});
export const prBadgeLabel = cva({
  base: { fontSize: "14px", letterSpacing: "0.03em", fontWeight: "600" },
  variants: {
    state: {
      open: { fill: "token(colors.amber)" },
      merged: { fill: "token(colors.mint)" },
      stale: { fill: "token(colors.alarm)" },
    },
  },
});
export const prBadgeSub = cva({
  base: { fontSize: "12px", letterSpacing: "0.02em" },
  variants: {
    state: {
      open: { fill: "token(colors.muted)" },
      merged: { fill: "token(colors.mint)", opacity: "0.85" },
      stale: { fill: "token(colors.alarm)", fontStyle: "italic" },
    },
  },
});

// --- G6: atmospheric blueprint backdrop (the faint amber-tinted boomerang) ----
// Mounts behind the scene, gated to effects-on (useShouldAnimate) so it is absent + lazy under
// reduced-motion / data-effects=off. aria-hidden + pointer-events:none — pure atmosphere, never state.

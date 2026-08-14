// Engine Room ledger-popover tokens: the memory.md lookup table the warp coupler opens.
import { css, cva } from "../../../styled-system/css";

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

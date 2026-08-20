import { css, cva } from "../../../styled-system/css";

// The sprint graph wave-grid layout (L12-R2/R6): at most 3 boxes per row before wrapping on
// wide screens, collapsing to a single wave-ordered column on narrow/phone viewports. The grid
// styles object is exported so tests can pin the declarative responsive contract (jsdom cannot
// evaluate media queries).
export const waveGridStyles = {
  display: "grid",
  gridTemplateColumns: "repeat(3, minmax(0, 1fr))",
  gap: "0.5rem",
  "@media (max-width: 720px)": { gridTemplateColumns: "1fr" },
} as const;

export const graph = css({ display: "flex", flexDirection: "column", gap: "0.4rem" });
export const wave = css({ minWidth: "0" });
export const waveHead = css({
  margin: "0.4rem 0 0.35rem",
  fontSize: "0.8rem",
  letterSpacing: "0.05em",
  color: "muted",
});
export const waveGrid = css(waveGridStyles);
export const box = css({
  display: "flex",
  flexDirection: "column",
  gap: "0.3rem",
  minWidth: "0",
  padding: "0.5rem 0.6rem",
  background: "bgPanel",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "grid",
  borderRadius: "2px",
});
export const boxHead = css({ display: "flex", alignItems: "center", gap: "0.4rem", minWidth: "0" });
export const boxTitle = css({
  fontWeight: "600",
  fontSize: "0.82rem",
  minWidth: "0",
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
});
export const frontier = cva({
  base: {
    flexShrink: "0",
    fontSize: "0.62rem",
    letterSpacing: "0.06em",
    textTransform: "uppercase",
    borderWidth: "1px",
    borderStyle: "solid",
    paddingInline: "0.28rem",
    paddingBlock: "0.05rem",
    borderRadius: "2px",
  },
  variants: {
    state: {
      landed: { color: "mint", borderColor: "mint" },
      ready: { color: "cyan", borderColor: "cyan" },
      waiting: { color: "amber", borderColor: "amber" },
      "in-flight": { color: "alarm", borderColor: "alarm" },
    },
  },
});
export const leaves = css({
  listStyle: "none",
  margin: "0",
  padding: "0",
  display: "flex",
  flexDirection: "column",
  gap: "0.15rem",
});
// One ellipsized line per leaf; the visible character range grows with the viewport (ch-based
// caps stepped up at the sm/lg breakpoints).
// The ellipsized leaf-line declaration, exported so tests can pin the ch-based growth
// contract (jsdom cannot evaluate ch units or media queries).
export const leafLineStyles = {
  fontSize: "0.74rem",
  color: "ink",
  opacity: "0.85",
  whiteSpace: "nowrap",
  overflow: "hidden",
  textOverflow: "ellipsis",
  maxWidth: "min(100%, 30ch)",
  sm: { maxWidth: "min(100%, 48ch)" },
  lg: { maxWidth: "min(100%, 72ch)" },
} as const;
export const leafLine = css(leafLineStyles);
export const lump = css({ fontSize: "0.7rem", color: "amber", letterSpacing: "0.05em" });
export const preds = css({
  listStyle: "none",
  margin: "0",
  padding: "0",
  display: "flex",
  flexDirection: "column",
  gap: "0.12rem",
});
export const pred = css({
  fontSize: "0.7rem",
  color: "muted",
  whiteSpace: "nowrap",
  overflow: "hidden",
  textOverflow: "ellipsis",
});
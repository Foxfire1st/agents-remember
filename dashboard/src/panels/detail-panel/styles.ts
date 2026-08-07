// DetailPanel style tokens (Panda recipes): the phase stepper, doc reader, master overview,
// sub-task index, spine lanes, and change-set bar surfaces. One recipe per semantic axis.
import { css, cva } from "../../../styled-system/css";

export const sizing = css({ flex: "1" });
export const where = css({ fontSize: "0.76rem", color: "muted", marginBottom: "0.4rem" });

export const stepper = css({
  listStyle: "none",
  margin: "0.4rem 0",
  padding: "0",
  display: "flex",
  flexWrap: "wrap",
  gap: "0.3rem",
});
export const step = cva({
  base: {
    fontSize: "0.72rem",
    letterSpacing: "0.04em",
    paddingInline: "0.4rem",
    paddingBlock: "0.15rem",
    borderWidth: "1px",
    borderStyle: "solid",
    borderColor: "grid",
    borderRadius: "2px",
    color: "oklch(0.6 0.02 250)",
  },
  variants: {
    state: {
      todo: {},
      done: { color: "cyan", borderColor: "cyan" },
      current: {
        color: "amber",
        borderColor: "amber",
        textShadow: "0 0 calc(5px * var(--glow-strength)) oklch(0.82 0.16 75 / 0.5)",
      },
    },
  },
});

export const badge = css({
  fontSize: "0.68rem",
  paddingInline: "0.35rem",
  paddingBlock: "0.05rem",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "grid",
  borderRadius: "2px",
  color: "muted",
});

export const series = css({ margin: "0.5rem 0" });
export const taskHead = css({ fontSize: "0.82rem" });
export const slices = css({ listStyle: "none", margin: "0.3rem 0 0", padding: "0", display: "grid", gap: "0.15rem" });
export const slice = css({
  display: "flex",
  justifyContent: "space-between",
  gap: "0.5rem",
  fontSize: "0.78rem",
  paddingInline: "0.4rem",
  paddingBlock: "0.2rem",
  background: "bg",
  borderLeftWidth: "2px",
  borderLeftStyle: "solid",
  borderLeftColor: "grid",
});
export const sliceMeta = css({ color: "muted", fontSize: "0.72rem" });
// A clickable sub-task row (drill-in) and the breadcrumb back from a slice to the series.
export const sliceButton = css({
  display: "flex",
  justifyContent: "space-between",
  gap: "0.5rem",
  width: "100%",
  textAlign: "left",
  font: "inherit",
  fontSize: "0.78rem",
  paddingInline: "0.4rem",
  paddingBlock: "0.2rem",
  background: "bg",
  border: "0",
  borderLeftWidth: "2px",
  borderLeftStyle: "solid",
  borderLeftColor: "cyan",
  color: "ink",
  cursor: "pointer",
  _hover: { background: "oklch(0.7 0.1 200 / 0.12)", borderLeftColor: "amber" },
  _focusVisible: { outline: "1px solid token(colors.amber)", outlineOffset: "1px" },
});
// Cross-master link: a row that jumps to a parallel/external series (amber "→"), distinct from
// the cyan in-series drill rows so leaving the current series reads as a deliberate hop.
export const crossButton = css({
  display: "flex",
  justifyContent: "space-between",
  gap: "0.5rem",
  width: "100%",
  textAlign: "left",
  font: "inherit",
  fontSize: "0.78rem",
  paddingInline: "0.4rem",
  paddingBlock: "0.2rem",
  background: "bg",
  border: "0",
  borderLeftWidth: "2px",
  borderLeftStyle: "solid",
  borderLeftColor: "amber",
  color: "amber",
  cursor: "pointer",
  _hover: { background: "oklch(0.82 0.16 75 / 0.12)" },
  _focusVisible: { outline: "1px solid token(colors.amber)", outlineOffset: "1px" },
});
export const crumb = css({
  font: "inherit",
  fontSize: "0.76rem",
  color: "cyan",
  background: "transparent",
  border: "0",
  padding: "0",
  marginBottom: "0.5rem",
  cursor: "pointer",
  _hover: { textDecoration: "underline" },
  _focusVisible: { outline: "1px solid token(colors.amber)", outlineOffset: "1px" },
});

export const spine = css({ margin: "0.6rem 0" });
export const spineHead = css({
  fontSize: "0.72rem",
  letterSpacing: "0.06em",
  textTransform: "uppercase",
  color: "muted",
  marginBottom: "0.3rem",
});
export const lanes = css({ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "0.5rem" });
export const lane = cva({
  base: {
    display: "grid",
    gap: "0.25rem",
    borderWidth: "1px",
    borderStyle: "solid",
    borderColor: "grid",
    borderRadius: "3px",
    padding: "0.4rem 0.5rem",
    borderLeftWidth: "2px",
  },
  variants: { kind: { code: { borderLeftColor: "amber" }, memory: { borderLeftColor: "cyan" } } },
});
export const laneTitle = css({ fontSize: "0.76rem", color: "ink" });
export const laneRepo = css({ fontSize: "0.74rem", color: "muted" });
export const laneMeta = css({
  display: "flex",
  flexWrap: "wrap",
  gap: "0.5rem",
  fontSize: "0.72rem",
  color: "muted",
});

export const changeSetBar = css({ display: "flex", flexWrap: "wrap", gap: "0.4rem", marginBottom: "0.4rem" });
export const changeSetBtn = css({
  display: "inline-flex",
  alignItems: "center",
  gap: "0.35rem",
  fontSize: "0.72rem",
  fontFamily: "mono",
  color: "cyan",
  background: "transparent",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "grid",
  borderRadius: "3px",
  paddingInline: "0.45rem",
  paddingBlock: "0.15rem",
  cursor: "pointer",
  _hover: { borderColor: "cyan", color: "ink" },
});
export const changeSetCounts = css({ color: "muted" });

export const tokensRow = css({ display: "flex", alignItems: "center", gap: "0.5rem", marginTop: "0.5rem" });
export const label = css({
  fontSize: "0.72rem",
  letterSpacing: "0.08em",
  textTransform: "uppercase",
  color: "muted",
});

export const stepsList = css({ listStyle: "none", margin: "0.45rem 0 0", padding: "0", display: "grid", gap: "0.25rem" });
export const stepRow = css({
  display: "grid",
  gridTemplateColumns: "auto 1fr",
  alignItems: "baseline",
  gap: "0.45rem",
  fontSize: "0.82rem",
});
export const stepMarkBase = css({
  width: "0.62em",
  height: "0.62em",
  alignSelf: "center",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "grid",
  borderRadius: "2px",
});
// step status is data-driven; map by record so an unknown status renders as a neutral mark.
export const STEP_MARK: Record<string, string> = {
  done: css({ background: "mint", borderColor: "mint" }),
  inProgress: css({ background: "amber", borderColor: "amber" }),
  blocked: css({ background: "alarm", borderColor: "alarm" }),
};
export const STEP_TITLE: Record<string, string> = { done: css({ color: "oklch(0.6 0.02 250)" }) };
export const substeps = css({
  gridColumn: "2",
  listStyle: "none",
  margin: "0.15rem 0 0",
  padding: "0",
  display: "grid",
  gap: "0.1rem",
  fontSize: "0.76rem",
  color: "muted",
});
export const SUBSTEP: Record<string, string> = { inProgress: css({ color: "amber" }) };
export const skippedDisposition = css({
  display: "inline",
  marginInlineStart: "0.4rem",
  color: "amber",
  fontSize: "0.72rem",
});
export const skippedWord = css({ fontWeight: "700", letterSpacing: "0.06em" });

export const taskdoc = css({ display: "grid", gap: "0.75rem" });
export const taskdocHead = css({ display: "flex", alignItems: "center", gap: "0.5rem", flexWrap: "wrap" });
export const taskdocTitle = css({ fontWeight: "600" });
export const taskdocStatus = css({ color: "cyan", fontSize: "0.8rem" });
export const masterTokens = css({ display: "flex", alignItems: "center", gap: "0.5rem", color: "cyan", fontSize: "0.78rem" });
export const masterTokenValue = css({ fontWeight: "600" });
export const taskdocSection = css({ display: "grid", gap: "0.3rem" });
export const taskdocH = css({
  margin: "0",
  fontSize: "0.72rem",
  letterSpacing: "0.08em",
  textTransform: "uppercase",
  color: "amber",
});
export const taskdocBullets = css({
  margin: "0",
  paddingLeft: "1.1rem",
  maxWidth: "78ch",
  display: "grid",
  gap: "0.2rem",
  fontSize: "0.84rem",
  lineHeight: "1.45",
});
export const taskdocCode = css({
  display: "grid",
  gap: "0.2rem",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "grid",
  borderRadius: "3px",
  padding: "0.45rem 0.55rem",
});
export const taskdocCodeHead = css({ color: "amber", fontSize: "0.82rem" });
export const taskdocCodeMeta = css({ fontSize: "0.78rem", color: "muted" });
export const taskdocSnippet = css({
  margin: "0.2rem 0 0",
  padding: "0.5rem 0.6rem",
  background: "bg",
  borderRadius: "2px",
  overflow: "auto",
  fontSize: "0.78rem",
  lineHeight: "1.45",
});
export const taskdocDecisions = css({
  listStyle: "none",
  margin: "0",
  padding: "0",
  display: "grid",
  gap: "0.4rem",
  maxWidth: "78ch",
});
export const taskdocDecision = css({ fontSize: "0.84rem" });
export const taskdocDecisionMeta = css({ fontSize: "0.76rem", color: "muted" });

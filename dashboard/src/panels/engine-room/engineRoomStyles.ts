// The Engine Room visual language as Panda recipes (slice 5e, 05e §8/§9.3): state and
// severity are carried by COLOUR (note 08), never by ad-hoc chrome, and every animation is
// one of the shared global keyframes (freezable by html[data-effects="off"]). One recipe per
// semantic axis — process health, fact state, conduit state, engine runtime — so the truth
// always comes from the model, never from a class name alone.

import { css, cva } from "../../../styled-system/css";

// --- layout ------------------------------------------------------------------

export const roomLayout = css({
  display: "grid",
  gridTemplateColumns: "minmax(190px, 18rem) 1fr",
  gap: "0.8rem",
  flex: "1",
  minHeight: "0",
  alignItems: "start",
});

export const detailColumn = css({
  display: "grid",
  gap: "0.7rem",
  minWidth: "0",
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
  maxHeight: "100%",
  overflowY: "auto",
  outline: "none",
});

export const stackItem = cva({
  base: {
    display: "grid",
    gap: "0.25rem",
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
});

export const stackTaskName = css({
  color: "ink",
  fontSize: "0.82rem",
  whiteSpace: "nowrap",
  overflow: "hidden",
  textOverflow: "ellipsis",
});

export const stackMeta = css({
  display: "flex",
  flexWrap: "wrap",
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
      nominal: { borderColor: "amber" },
      configured: { borderColor: "amber", opacity: "0.8" },
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

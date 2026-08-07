import { css, cva } from "../../../styled-system/css";

export const railBody = css({
  display: "flex",
  flexDirection: "column",
  gap: "0.35rem",
  flex: "1",
  minHeight: "0",
  overflowY: "auto",
  overflowX: "hidden",
});
export const staleBanner = css({
  fontSize: "0.68rem",
  color: "amber",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "amber",
  borderRadius: "2px",
  paddingInline: "0.4rem",
  paddingBlock: "0.15rem",
});
export const attnStrip = css({
  display: "flex",
  gap: "0.3rem",
  flexWrap: "wrap",
  flexShrink: 0,
});
export const attnButton = cva({
  base: {
    font: "inherit",
    fontSize: "0.66rem",
    paddingInline: "0.4rem",
    paddingBlock: "0.08rem",
    borderRadius: "2px",
    borderWidth: "1px",
    borderStyle: "solid",
    borderColor: "grid",
    background: "bg",
    color: "muted",
    cursor: "pointer",
    fontVariantNumeric: "tabular-nums",
    _focusVisible: {
      outline: "1px solid token(colors.amber)",
      outlineOffset: "1px",
    },
  },
  variants: {
    tone: {
      warn: {
        color: "amber",
        borderColor:
          "color-mix(in oklch, token(colors.amber) 45%, transparent)",
      },
      alarm: {
        color: "alarm",
        borderColor:
          "color-mix(in oklch, token(colors.alarm) 45%, transparent)",
      },
      info: { color: "cyan" },
      muted: {},
    },
  },
});
export const sprintRow = css({
  display: "flex",
  alignItems: "center",
  justifyContent: "space-between",
  gap: "0.4rem",
  fontSize: "0.66rem",
  color: "muted",
  flexShrink: 0,
});
export const bulkButton = css({
  font: "inherit",
  fontSize: "0.62rem",
  // An action control never crushes into a letter column: it holds its intrinsic width and
  // its own line, so the flex row elides the long copy span (below), never the buttons.
  flex: "none",
  whiteSpace: "nowrap",
  color: "alarm",
  background: "transparent",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "color-mix(in oklch, token(colors.alarm) 35%, transparent)",
  borderRadius: "2px",
  paddingInline: "0.35rem",
  cursor: "pointer",
  _focusVisible: {
    outline: "1px solid token(colors.amber)",
    outlineOffset: "1px",
  },
});
export const confirmRow = css({
  display: "flex",
  alignItems: "baseline",
  gap: "0.35rem",
  fontSize: "0.64rem",
  color: "amber",
  minWidth: "0",
  "& > span": {
    overflow: "hidden",
    textOverflow: "ellipsis",
    whiteSpace: "nowrap",
  },
});
export const masterBox = css({
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "grid",
  background: "bgPanel",
  flexShrink: 0,
});
export const masterHead = css({
  display: "flex",
  alignItems: "center",
  gap: "0.4rem",
  fontSize: "0.72rem",
  color: "ink",
  paddingInline: "0.5rem",
  paddingBlock: "0.3rem",
  borderBottomWidth: "1px",
  borderBottomStyle: "solid",
  borderBottomColor: "grid",
  minWidth: "0",
});
export const masterName = css({
  flex: "1",
  minWidth: "0",
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
});
export const masterBody = css({
  padding: "0.3rem",
  display: "grid",
  gap: "0.25rem",
});
// Leaf clusters: indented, separated by FINE hairlines + margins — never heavy boxes (RULED).
export const leafGroup = css({
  marginLeft: "0.9rem",
  borderTopWidth: "1px",
  borderTopStyle: "solid",
  borderTopColor: "color-mix(in oklch, token(colors.grid) 55%, transparent)",
  paddingTop: "0.28rem",
  marginTop: "0.15rem",
  display: "grid",
  gap: "0.25rem",
});
export const leafCaption = css({
  fontSize: "0.62rem",
  color: "muted",
  letterSpacing: "0.04em",
  // A long leaf id truncates at the end with the full
  // value on hover, never breaking mid-word down the narrow rail.
  display: "block",
  minWidth: "0",
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
});
export const doneFold = css({
  display: "flex",
  alignItems: "center",
  gap: "0.4rem",
  marginLeft: "0.9rem",
  marginTop: "0.15rem",
  borderTopWidth: "1px",
  borderTopStyle: "solid",
  borderTopColor: "color-mix(in oklch, token(colors.grid) 55%, transparent)",
  paddingTop: "0.28rem",
  color: "muted",
  fontSize: "0.7rem",
  minWidth: "0",
});
export const doneToggle = css({
  font: "inherit",
  fontSize: "0.7rem",
  // Hold width + own line so the confirm/cancel controls never wrap letters vertically.
  flex: "none",
  whiteSpace: "nowrap",
  color: "muted",
  background: "transparent",
  border: "none",
  cursor: "pointer",
  padding: "0",
  _focusVisible: {
    outline: "1px solid token(colors.amber)",
    outlineOffset: "1px",
  },
});
export const groupBox = css({
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "grid",
  background: "bgPanel",
  flexShrink: 0,
});
export const groupRows = css({
  padding: "0.3rem",
  display: "grid",
  gap: "0.25rem",
});
// Row anatomy (RULED): a LABEL group (dot | role | title | markers | chip) and an ACTION
// group (End, or the armed confirm/cancel). The row is `flex-wrap: wrap`: when the two groups cannot
// share one line at a narrow rail, the ACTION group wraps whole to a second line — the actions stay
// single-line and reachable at EVERY width down to the collapse threshold, never letter-wrapping,
// clipping, or overflowing the `overflow:hidden` aside. Priority when squeezed: actions > chip >
// inline copy — the label group's title/chip elide (min-width:0 through every nested level) and the
// armed state drops the chip entirely (the confirm copy already carries the state).
export const rowLabelGroup = css({
  display: "flex",
  alignItems: "center",
  gap: "0.35rem",
  flex: "1 1 auto",
  minWidth: "0",
  overflow: "hidden",
});
export const rowActionGroup = css({
  display: "flex",
  alignItems: "center",
  gap: "0.35rem",
  // Grows never, shrinks yes: it takes only the width its controls need on line 1, and on a wrapped
  // line 2 it shrinks so the elidable inline copy yields while the confirm/cancel buttons hold.
  flex: "0 1 auto",
  minWidth: "0",
});
export const rowShell = css({
  display: "flex",
  alignItems: "center",
  flexWrap: "wrap",
  gap: "0.35rem",
  rowGap: "0.2rem",
  minWidth: "0",
  width: "100%",
  textAlign: "left",
  font: "inherit",
  background: "bg",
  color: "muted",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "grid",
  borderRadius: "2px",
  paddingLeft: "0.5rem",
  cursor: "pointer",
  transition: "border-color 0.12s ease",
  // The row gives approach feedback before the click that arms End (there was none).
  _hover: {
    borderColor:
      "color-mix(in oklch, token(colors.amber) 45%, token(colors.grid))",
  },
  "&[data-selected='true']": { color: "amber", borderColor: "amber" },
  "&[data-attention-highlight='true']": { borderColor: "cyan" },
  _focusVisible: {
    outline: "1px solid token(colors.amber)",
    outlineOffset: "1px",
  },
});
export const roleChip = cva({
  base: {
    flex: "none",
    fontSize: "0.6rem",
    textTransform: "uppercase",
    letterSpacing: "0.08em",
    borderWidth: "1px",
    borderStyle: "solid",
    borderColor: "grid",
    borderRadius: "2px",
    paddingInline: "0.25rem",
    color: "muted",
  },
  variants: {
    role: {
      ARC: {
        color: "gold",
        borderColor: "color-mix(in oklch, token(colors.gold) 45%, transparent)",
      },
      ORC: {
        color: "gold",
        borderColor: "color-mix(in oklch, token(colors.gold) 45%, transparent)",
      },
      STR: { color: "gold" },
      DSG: { color: "gold" },
      MGR: {
        color: "purple",
        borderColor:
          "color-mix(in oklch, token(colors.purple) 45%, transparent)",
      },
      WKR: { color: "cyan" },
      CUR: { color: "cyan" },
      SYS: { color: "cyan" },
      REV: { color: "amber" },
    },
  },
});
export const rowTitle = css({
  // The flexible segment truly absorbs (min-width:0): it grows to show the label when there is
  // room and elides to `…` when the rail is tight, so it never forces the row past the aside. The full
  // label stays in the row tooltip (railRowTooltip).
  flex: "1 1 auto",
  minWidth: "0",
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
  fontSize: "0.74rem",
  paddingBlock: "0.25rem",
});
export const attentionSlot = css({
  display: "inline-flex",
  alignItems: "center",
  gap: "0.2rem",
  flex: "none",
});
export const markerChip = cva({
  base: {
    fontSize: "0.6rem",
    borderWidth: "1px",
    borderStyle: "solid",
    borderColor: "grid",
    borderRadius: "2px",
    paddingInline: "0.2rem",
    color: "muted",
  },
  variants: {
    tone: {
      warn: {
        color: "amber",
        borderColor:
          "color-mix(in oklch, token(colors.amber) 45%, transparent)",
      },
    },
  },
});
export const statusChip = cva({
  base: {
    // The state chip shows its whole word when the rail has room (`turn-ended` ~72px < 7rem
    // ceiling). It is elidable (shrinks after the title yields) so it never forces the End
    // action off the row — the whole word stays in the chip tooltip, and the state also lives in the
    // StateDot's accessible name. It is dropped entirely while the row is armed (the confirm copy
    // carries the state), so the confirm/cancel controls always fit inside the aside.
    flex: "0 1 auto",
    minWidth: "0",
    maxWidth: "7rem",
    overflow: "hidden",
    textOverflow: "ellipsis",
    whiteSpace: "nowrap",
    fontSize: "0.64rem",
    borderWidth: "1px",
    borderStyle: "solid",
    borderColor: "grid",
    borderRadius: "2px",
    paddingInline: "0.25rem",
    color: "muted",
  },
  variants: {
    tone: {
      warn: { color: "amber" },
      alarm: { color: "alarm" },
      muted: {},
    },
  },
});
// A destructive control carries DEMOTED weight until the moment of consequence: muted by
// default (red would be six alarms shouting from every row and diluting the danger signal), and it
// warms to alarm only on hover / keyboard focus / the selected row — the approach that arms it.
export const endButton = css({
  font: "inherit",
  fontSize: "0.62rem",
  color: "muted",
  background: "transparent",
  border: "none",
  borderLeftWidth: "1px",
  borderLeftStyle: "solid",
  borderLeftColor: "grid",
  paddingInline: "0.35rem",
  alignSelf: "stretch",
  cursor: "pointer",
  flex: "none",
  transition: "color 0.12s ease",
  _hover: { color: "alarm" },
  "[data-selected='true'] &": { color: "alarm" },
  _focusVisible: {
    color: "alarm",
    outline: "1px solid token(colors.amber)",
    outlineOffset: "-1px",
  },
});
export const treeIndent = css({ display: "grid", gap: "0.25rem" });
export const railTop = css({
  display: "flex",
  alignItems: "center",
  flexWrap: "wrap",
  rowGap: "0.25rem",
  gap: "0.4rem",
  flexShrink: 0,
});
export const treeToggleButton = css({
  font: "inherit",
  fontSize: "0.62rem",
  marginLeft: "auto",
  // The toggle never wraps its own label (`rol/e vie/w`): it holds width + its own line.
  flexShrink: 0,
  whiteSpace: "nowrap",
  color: "muted",
  background: "transparent",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "grid",
  borderRadius: "2px",
  paddingInline: "0.35rem",
  cursor: "pointer",
  "&[data-on='true']": { color: "amber", borderColor: "amber" },
  _focusVisible: {
    outline: "1px solid token(colors.amber)",
    outlineOffset: "1px",
  },
});
export const zeroState = css({
  flex: "1",
  minHeight: "0",
  display: "grid",
  placeItems: "center",
  padding: "0.8rem",
  color: "muted",
  fontSize: "0.72rem",
  lineHeight: "1.5",
  textAlign: "center",
  borderWidth: "1px",
  borderStyle: "dashed",
  borderColor: "grid",
  borderRadius: "2px",
});

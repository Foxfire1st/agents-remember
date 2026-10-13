import { css, cva } from "../../styled-system/css";

// The one shared definition of the view tabs' look (MIK-R133 rule 7). The view
// tabs (ModeBar), the role launcher's selectors, chips, Start and Refresh, and
// the tabs in the head of the tasks column all take their border, corner
// radius, letter spacing and letter case from here; none of them holds a copy.
// The view tabs and the launcher take their height and font size from here too;
// the tasks column has a size of its own, defined below.
//
// The tabs' height is implicit: it falls out of the shared padding and font
// size, so the launcher's controls use height auto with the same padding and
// land at the same height. A selector shows a value as its source spells it,
// so the selector keeps the shared spacing but not the upper case.
export const viewTabMetrics = {
  fontSize: "0.72rem",
  letterSpacing: "0.08em",
  textTransform: "uppercase",
  paddingInline: "0.75rem",
  paddingBlock: "0.3rem",
  borderWidth: "1px",
  borderRadius: "2px",
} as const;

const viewTabSelectedLook = {
  color: "amber",
  borderColor: "amber",
  textShadow: "0 0 calc(5px * var(--glow-strength)) oklch(0.82 0.16 75 / 0.4)",
} as const;

const viewTabFocusLook = {
  outlineWidth: "1px",
  outlineStyle: "solid",
  outlineColor: "amber",
  outlineOffset: "1px",
} as const;

// The view tab itself, and the base of every launcher button.
export const viewTabButton = css({
  font: "inherit",
  ...viewTabMetrics,
  background: "bg",
  color: "muted",
  borderStyle: "solid",
  borderColor: "grid",
  cursor: "pointer",
  _selected: viewTabSelectedLook,
  _focusVisible: viewTabFocusLook,
});

// The same tab at the size of the tasks column (MIK-R144 rule 3b, MIK-R163 rule 1): the grouping
// switch, the bell of the attention queue and "Clear all". The column's text follows the task
// page's smaller text, so the tab is 0.72rem with less room left and right. At the tab's own
// size the switch and the bell do not fit the head of the column beside its
// title. Colours, border, corners, the selected and the focus look are the
// ones above; the focus line lies inside the tab, because the column cuts what
// leaves it.
export const columnTabButton = css({
  font: "inherit",
  ...viewTabMetrics,
  fontSize: "0.72rem",
  lineHeight: "1.4",
  paddingInline: "0.525rem",
  paddingBlock: "0.2625rem",
  whiteSpace: "nowrap",
  background: "bg",
  color: "muted",
  borderStyle: "solid",
  borderColor: "grid",
  cursor: "pointer",
  _selected: viewTabSelectedLook,
  _focusVisible: { ...viewTabFocusLook, outlineOffset: "-1px" },
  _disabled: { opacity: "0.5", cursor: "default" },
});

// Start has the look of the selected tab, Refresh (and the other row buttons)
// the look of a tab that is not selected.
export const launcherButton = cva({
  base: {
    font: "inherit",
    ...viewTabMetrics,
    display: "inline-flex",
    alignItems: "center",
    justifyContent: "center",
    background: "bg",
    borderStyle: "solid",
    cursor: "pointer",
    whiteSpace: "nowrap",
    transition: "background-color 120ms ease, border-color 120ms ease, color 120ms ease",
    _focusVisible: {
      outlineWidth: "2px",
      outlineStyle: "solid",
      outlineColor: "amber",
      outlineOffset: "2px",
    },
    _disabled: { opacity: "0.5", cursor: "not-allowed" },
  },
  variants: {
    selected: {
      true: viewTabSelectedLook,
      false: { color: "muted", borderColor: "grid" },
    },
  },
});

// The row's one selector: the shared tab metrics with the source's own letter
// case, the row's arrow, and an ellipsis for values too long for their box
// (the whole value stays in the hover text).
export const launcherSelect = css({
  font: "inherit",
  ...viewTabMetrics,
  textTransform: "none",
  appearance: "none",
  width: "100%",
  minWidth: "0",
  maxWidth: "100%",
  paddingRight: "1.6rem",
  borderStyle: "solid",
  borderColor: "grid",
  backgroundColor: "bg",
  backgroundImage:
    "linear-gradient(45deg, transparent 50%, var(--muted) 50%), linear-gradient(135deg, var(--muted) 50%, transparent 50%)",
  backgroundPosition: "calc(100% - 11px) 50%, calc(100% - 7px) 50%",
  backgroundSize: "5px 5px, 5px 5px",
  backgroundRepeat: "no-repeat",
  color: "ink",
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
  transition: "border-color 120ms ease, box-shadow 120ms ease",
  _hover: { borderColor: "cyan" },
  _focusVisible: {
    outlineWidth: "2px",
    outlineStyle: "solid",
    outlineColor: "amber",
    outlineOffset: "1px",
    borderColor: "amber",
  },
  _disabled: { opacity: "0.58", cursor: "not-allowed" },
});

// An info chip of the row (the mode, an instruction note, a permission-bearing
// feature): the shared tab metrics in full. A chip is never cut — it keeps its
// whole label at every width (flex none); selectors give way first.
export const launcherChip = css({
  ...viewTabMetrics,
  display: "block",
  flexGrow: 0,
  flexShrink: 0,
  flexBasis: "auto",
  minWidth: "5rem",
  maxWidth: "100%",
  background: "bg",
  color: "muted",
  borderStyle: "solid",
  borderColor: "grid",
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
});

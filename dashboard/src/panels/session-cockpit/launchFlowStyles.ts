import { css } from "../../../styled-system/css";

export const overlay = css({
  position: "fixed",
  inset: "0",
  zIndex: "20",
  background: "oklch(0 0 0 / 0.35)",
  overflow: "hidden",
});
export const box = css({
  position: "fixed",
  top: "max(0.5rem, 6dvh)",
  left: "50%",
  transform: "translateX(-50%)",
  width: "min(620px, calc(100vw - 1rem))",
  maxHeight: "calc(100dvh - 1rem)",
  display: "flex",
  flexDirection: "column",
  gap: "0.55rem",
  background: "bgPanel",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "grid",
  borderRadius: "3px",
  boxShadow: "0 10px 40px oklch(0 0 0 / 0.5)",
  padding: "0.7rem 0.8rem",
  overflowY: "auto",
  fontSize: "0.76rem",
  color: "muted",
});
export const heading = css({
  fontSize: "0.66rem",
  letterSpacing: "0.12em",
  textTransform: "uppercase",
  color: "cyan",
});
export const optionRow = css({ display: "flex", flexWrap: "wrap", gap: "0.35rem" });
export const optionButton = css({
  font: "inherit",
  fontSize: "0.74rem",
  paddingInline: "0.5rem",
  paddingBlock: "0.15rem",
  background: "bg",
  color: "muted",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "grid",
  borderRadius: "2px",
  cursor: "pointer",
  _hover: { color: "amber", borderColor: "amber" },
  "&[aria-pressed='true']": { color: "amber", borderColor: "amber" },
  _disabled: {
    opacity: 0.55,
    cursor: "not-allowed",
    _hover: { color: "muted", borderColor: "grid" },
  },
  _focusVisible: {
    outlineWidth: "1px",
    outlineStyle: "solid",
    outlineColor: "amber",
    outlineOffset: "1px",
  },
});
export const noteLine = css({ fontSize: "0.7rem", color: "muted" });
export const errorLine = css({ fontSize: "0.72rem", color: "alarm", whiteSpace: "pre-wrap" });
export const smallInput = css({
  font: "inherit",
  fontSize: "0.74rem",
  background: "bg",
  color: "ink",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "grid",
  borderRadius: "2px",
  padding: "0.2rem 0.4rem",
  minWidth: "0",
  flex: "1",
  _focusVisible: {
    outlineWidth: "1px",
    outlineStyle: "solid",
    outlineColor: "amber",
    outlineOffset: "1px",
  },
});
export const footerRow = css({
  display: "flex",
  alignItems: "center",
  gap: "0.6rem",
  borderTopWidth: "1px",
  borderTopStyle: "solid",
  borderTopColor: "grid",
  paddingTop: "0.45rem",
});
export const launchButton = css({
  font: "inherit",
  fontSize: "0.76rem",
  // RV-3/V12 — an action control never wraps its own label (`launc/h`): it holds width + one line, so
  // the footer's summary span is the only segment that yields.
  flexShrink: 0,
  whiteSpace: "nowrap",
  paddingInline: "0.7rem",
  paddingBlock: "0.2rem",
  background: "transparent",
  color: "amber",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "amber",
  borderRadius: "2px",
  cursor: "pointer",
  // V7 — a disabled primary must not read as ready: drop the amber prominence to a muted, inert chip
  // so the most emphatic control looks armed ONLY when the pair is complete and the harness detected.
  _disabled: {
    opacity: 0.4,
    cursor: "not-allowed",
    color: "muted",
    borderColor: "grid",
  },
  _focusVisible: {
    outlineWidth: "1px",
    outlineStyle: "solid",
    outlineColor: "amber",
    outlineOffset: "1px",
  },
});
export const quietButton = css({
  font: "inherit",
  fontSize: "0.7rem",
  // RV-3/V12 — never wrap the label (`dismiss (resolves via the catal/og)`): hold width + one line.
  flexShrink: 0,
  whiteSpace: "nowrap",
  paddingInline: "0.45rem",
  paddingBlock: "0.12rem",
  background: "transparent",
  color: "muted",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "grid",
  borderRadius: "2px",
  cursor: "pointer",
  _hover: { color: "amber", borderColor: "amber" },
  _focusVisible: {
    outlineWidth: "1px",
    outlineStyle: "solid",
    outlineColor: "amber",
    outlineOffset: "1px",
  },
});
export const outcomeBox = css({
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "grid",
  borderRadius: "2px",
  padding: "0.4rem 0.5rem",
  display: "grid",
  gap: "0.3rem",
});

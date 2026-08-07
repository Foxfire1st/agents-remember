import { css } from "../../../../styled-system/css";

export const surface = css({
  display: "flex",
  flexDirection: "column",
  flex: "1",
  minHeight: "0",
  minWidth: "0",
  gap: "0.3rem",
});
export const toolbar = css({
  display: "flex",
  alignItems: "center",
  gap: "0.5rem",
  flexWrap: "wrap",
  flexShrink: 0,
  fontSize: "0.66rem",
  color: "muted",
});
export const toggle = css({
  font: "inherit",
  fontSize: "0.66rem",
  color: "muted",
  background: "transparent",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "grid",
  borderRadius: "2px",
  paddingInline: "0.4rem",
  cursor: "pointer",
  _hover: { color: "amber", borderColor: "amber" },
  _focusVisible: { outline: "1px solid token(colors.amber)", outlineOffset: "1px" },
});
export const agentFocusNote = css({
  fontSize: "0.66rem",
  color: "muted",
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
  minWidth: "0",
});
export const agentHistoryError = css({
  display: "flex",
  alignItems: "center",
  justifyContent: "space-between",
  gap: "0.5rem",
  padding: "0.35rem 0.5rem",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "red.700",
  borderRadius: "2px",
  color: "red.300",
  fontSize: "0.66rem",
});

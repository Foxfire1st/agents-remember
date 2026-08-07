// Conversation timeline style tokens: the viewport well, feed rows, older-paging bar, latest chip,
// and the collapsed unknown-vendor run row.
import { css } from "../../../../../styled-system/css";

export const viewport = css({
  flex: "1",
  minHeight: "0",
  overflowY: "auto",
  overflowX: "hidden",
  position: "relative",
  outline: "none",
  background: "well",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "grid",
  borderRadius: "3px",
  paddingInline: "2ch",
});
export const feedInner = css({ position: "relative", width: "100%" });
// The empty conversation remains inside the terminal well, but gets enough space to behave like
// a real TUI landing screen. It is ordinary flow content so short viewports can still scroll it.
export const emptyInWell = css({
  minHeight: "100%",
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
  paddingBlock: "clamp(2rem, 7vh, 5rem)",
});
// Line-grid rhythm: no per-article hairline rule (neither Toad nor Claude Code rules between
// items); one blank line of breathing below each item instead of a boxed web-list divider.
export const rowShell = css({
  position: "absolute",
  top: "0",
  left: "0",
  width: "100%",
  paddingBlockStart: "0.15rem",
  paddingBlockEnd: "0.9rem",
  _focusVisible: { outline: "1px solid token(colors.amber)", outlineOffset: "-1px" },
});
// The viewport's positioning shell. Absolutely-positioned children of a SCROLL container scroll
// with its content (verified live), so anything meant to FLOAT over the visible frame — the latest
// chip — is a child of this shell, a sibling of the scroller, never a child of it. The shell hugs
// the scroller exactly (flex column, the well keeps its own flex:1), so
// the overlay coordinates coincide with the visible viewport.
export const viewportShell = css({
  position: "relative",
  display: "flex",
  flexDirection: "column",
  flex: "1",
  minHeight: "0",
  minWidth: "0",
});
// One state-aware route back to the live edge. It sits clear of the native scrollbar rather
// than sharing its hit corridor, and folds the old "N new updates" pill into the same action.
export const latestChip = css({
  position: "absolute",
  right: "1.6rem",
  bottom: "0.65rem",
  display: "inline-flex",
  alignItems: "center",
  gap: "0.35rem",
  font: "inherit",
  fontSize: "0.68rem",
  lineHeight: "1",
  color: "ink",
  background: "bgPanel",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "grid",
  borderRadius: "3px",
  paddingInline: "0.5rem",
  minHeight: "1.75rem",
  whiteSpace: "nowrap",
  cursor: "pointer",
  boxShadow: "0 2px 8px oklch(0 0 0 / 0.32)",
  transition: "transform 120ms cubic-bezier(0.23, 1, 0.32, 1)",
  _hover: { color: "amber", borderColor: "amber" },
  _active: { transform: "scale(0.97)" },
  _focusVisible: { outline: "1px solid token(colors.amber)", outlineOffset: "1px" },
});
export const latestChipWithUpdates = css({
  color: "amber",
  borderColor: "amber",
});
export const olderBar = css({ display: "flex", justifyContent: "center", paddingBlock: "0.3rem" });
export const olderButton = css({
  font: "inherit",
  fontSize: "0.66rem",
  color: "muted",
  background: "transparent",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "grid",
  borderRadius: "2px",
  paddingInline: "0.5rem",
  paddingBlock: "0.1rem",
  cursor: "pointer",
  _hover: { color: "amber", borderColor: "amber" },
  _focusVisible: { outline: "1px solid token(colors.amber)", outlineOffset: "1px" },
});
// The collapsed unknown-vendor run is a dim gutter line, not a boxed web row.
export const runRow = css({ display: "grid", gap: "0.15rem", color: "dormant", fontSize: "0.7rem", fontFamily: "mono" });
export const runHead = css({ display: "flex", gap: "0.4rem", alignItems: "baseline", minWidth: "0" });
export const runSummary = css({ minWidth: "0", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" });
// The clamp control reads as a text affordance and NEVER wraps its own label.
export const runButton = css({
  font: "inherit",
  fontSize: "0.7rem",
  flex: "none",
  whiteSpace: "nowrap",
  color: "muted",
  background: "transparent",
  border: "none",
  paddingInline: "0",
  cursor: "pointer",
  textDecoration: "underline",
  textDecorationColor: "color-mix(in oklch, token(colors.grid) 60%, transparent)",
  textUnderlineOffset: "2px",
  _hover: { color: "amber" },
  _focusVisible: { outline: "1px solid token(colors.amber)", outlineOffset: "1px" },
});
export const runMember = css({ fontFamily: "mono", fontSize: "0.66rem", color: "dormant", overflowWrap: "anywhere", paddingInlineStart: "2ch" });

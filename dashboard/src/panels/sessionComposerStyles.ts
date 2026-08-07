import { css } from "../../styled-system/css";
import { EditorView } from "@codemirror/view";

export const dock = css({
  display: "grid",
  gap: "0.28rem",
  flexShrink: 0,
  minWidth: "0",
});
export const editorFrame = css({
  minWidth: "0",
  // The composer joins the terminal well (same inset tone as the feed + the pty pane).
  background: "well",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "grid",
  borderRadius: "2px",
  overflow: "hidden",
  // The page's primary input MUST show keyboard focus. The inner .cm-focused outline is clipped
  // by this frame's overflow:hidden, so the frame itself carries the house amber ring on focus.
  "&:focus-within": { borderColor: "amber" },
  "&[data-answer-mode='true']": { borderColor: "amber" },
  "&[data-disabled='true']": { opacity: "0.62" },
  "& .cm-editor": { minHeight: "2.55rem", maxHeight: "8rem" },
  "& .cm-scroller": { maxHeight: "8rem", overflowY: "auto" },
  "& .cm-content": { minHeight: "2.55rem" },
  "& .cm-focused": {
    outline: "1px solid token(colors.amber)",
    outlineOffset: "-1px",
  },
});
export const footer = css({
  display: "flex",
  alignItems: "baseline",
  gap: "0.55rem",
  minWidth: "0",
  color: "muted",
  fontSize: "0.66rem",
});
export const footerLeft = css({
  minWidth: "0",
  flex: "1",
  overflowWrap: "anywhere",
});
export const sendButton = css({
  font: "inherit",
  fontSize: "0.68rem",
  letterSpacing: "0.03em",
  // The send control keeps its full width and single line whatever the stage width: the hint
  // (footerLeft, flex:1 minWidth:0) is the only part that yields, so `ctrl+↵ send` is never shrunk
  // under the inspector's edge or wrapped when the inspector opens and the stage column reflows.
  flexShrink: 0,
  whiteSpace: "nowrap",
  paddingInline: "0.55rem",
  paddingBlock: "0.12rem",
  borderRadius: "2px",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "amber",
  color: "amber",
  background: "transparent",
  cursor: "pointer",
  _hover: {
    background: "color-mix(in oklch, token(colors.amber) 10%, transparent)",
  },
  _active: { transform: "scale(0.97)" },
  _focusVisible: {
    outline: "1px solid token(colors.amber)",
    outlineOffset: "1px",
  },
  _disabled: { opacity: "0.45", cursor: "default", transform: "none" },
});
// The stop control docks beside send — where every chat product puts
// it — not on the working line. Demoted weight until hover/focus takes the amber accent; red
// stays reserved for the moment of consequence. Same welded-evidence gating as before.
export const stopButtonEnabled = css({
  font: "inherit",
  fontSize: "0.68rem",
  flexShrink: 0,
  whiteSpace: "nowrap",
  color: "muted",
  background: "transparent",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "grid",
  borderRadius: "2px",
  paddingInline: "0.45rem",
  paddingBlock: "0.12rem",
  cursor: "pointer",
  _hover: { color: "amber", borderColor: "amber" },
  _focusVisible: {
    outline: "1px solid token(colors.amber)",
    outlineOffset: "1px",
  },
});
export const stopButtonDisabled = css({
  font: "inherit",
  fontSize: "0.68rem",
  flexShrink: 0,
  whiteSpace: "nowrap",
  color: "muted",
  background: "transparent",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "grid",
  borderRadius: "2px",
  paddingInline: "0.45rem",
  paddingBlock: "0.12rem",
  cursor: "not-allowed",
  opacity: 0.7,
  _focusVisible: {
    outline: "1px solid token(colors.amber)",
    outlineOffset: "1px",
  },
});
export const status = css({
  display: "flex",
  alignItems: "baseline",
  gap: "0.45rem",
  flexWrap: "wrap",
  minWidth: "0",
  fontSize: "0.68rem",
  color: "amber",
});
export const error = css({ color: "alarm", overflowWrap: "anywhere" });
export const secondaryButton = css({
  font: "inherit",
  fontSize: "0.66rem",
  color: "muted",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "grid",
  borderRadius: "2px",
  background: "transparent",
  cursor: "pointer",
  paddingInline: "0.4rem",
  _hover: { color: "amber", borderColor: "amber" },
});
export const recoveryText = css({
  maxHeight: "4rem",
  maxWidth: "100%",
  overflow: "auto",
  whiteSpace: "pre-wrap",
  color: "fg",
  borderLeftWidth: "2px",
  borderLeftStyle: "solid",
  borderLeftColor: "amber",
  paddingLeft: "0.4rem",
});

export const composerTheme = EditorView.theme({
  "&": {
    color: "var(--ink)",
    backgroundColor: "transparent",
    fontFamily: "var(--font-mono)",
    fontSize: "0.8rem",
  },
  ".cm-content": { padding: "0.38rem 0.5rem", caretColor: "var(--amber)" },
  ".cm-line": { padding: "0" },
  ".cm-gutters": { display: "none" },
  ".cm-cursor": { borderLeftColor: "var(--amber)" },
  ".cm-selectionBackground, &.cm-focused .cm-selectionBackground": {
    backgroundColor: "color-mix(in oklch, var(--cyan) 24%, transparent)",
  },
});

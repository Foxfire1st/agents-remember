import { css } from "../../../styled-system/css";

export const bar = css({
  display: "grid",
  gap: "0.35rem",
  flexShrink: 0,
  padding: "0.4rem 0.55rem",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "amber",
  borderRadius: "3px",
  background: "oklch(0.82 0.16 75 / 0.07)",
  fontSize: "0.74rem",
});
export const headRow = css({
  display: "flex",
  alignItems: "baseline",
  gap: "0.45rem",
  minWidth: "0",
});
export const kindChip = css({
  flex: "none",
  fontSize: "0.62rem",
  letterSpacing: "0.06em",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "color-mix(in oklch, token(colors.amber) 45%, transparent)",
  borderRadius: "2px",
  paddingInline: "0.3rem",
  color: "amber",
});
export const promptText = css({ color: "ink", minWidth: "0", overflowWrap: "anywhere" });
export const choicesRow = css({ display: "flex", gap: "0.35rem", flexWrap: "wrap" });
export const choiceButton = css({
  font: "inherit",
  fontSize: "0.72rem",
  paddingInline: "0.55rem",
  paddingBlock: "0.16rem",
  borderRadius: "2px",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "amber",
  color: "amber",
  background: "transparent",
  cursor: "pointer",
  _hover: { background: "oklch(0.82 0.16 75 / 0.12)" },
  _focusVisible: { outline: "1px solid token(colors.amber)", outlineOffset: "1px" },
  _disabled: { opacity: 0.55, cursor: "default" },
  // A recorded single-select answer / toggled multiSelect option — visibly held, still changeable
  // until the all-or-nothing submit fires.
  "&[data-selected='true']": { background: "oklch(0.82 0.16 75 / 0.2)" },
});
export const questionBlock = css({
  display: "grid",
  gap: "0.25rem",
  minWidth: "0",
  paddingBlock: "0.15rem",
});
export const questionsGrid = css({ display: "grid", gap: "0.3rem", minWidth: "0" });
export const hint = css({ color: "muted", fontSize: "0.66rem" });
export const statusRow = css({
  display: "flex",
  alignItems: "baseline",
  gap: "0.45rem",
  minWidth: "0",
});
export const errorText = css({ color: "alarm", overflowWrap: "anywhere", minWidth: "0" });
export const answeredText = css({ color: "mint" });
export const announce = css({
  position: "absolute",
  width: "1px",
  height: "1px",
  overflow: "hidden",
  clipPath: "inset(50%)",
});

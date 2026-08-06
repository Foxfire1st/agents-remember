// Sessions view style tokens + layout constants: the rail/stage/inspector group, the ~80-col floor
// chip, reopen affordances, and the panel percentage bounds shared by props and calibration.
import { css } from "../../../../styled-system/css";

export const root = css({
  position: "relative", // anchors the palette overlay inside the scope root
  display: "flex",
  flexDirection: "column",
  flex: "1",
  minHeight: "0",
  minWidth: "0",
  gap: "0.4rem",
});
// The WorkingLine slot — zero-height when idle, docked between conversation and composer.
export const workingLineSlot = css({ flexShrink: 0, minHeight: "0" });
export const pane = css({
  height: "100%",
  minWidth: "0",
  display: "flex",
  flexDirection: "column",
  gap: "0.4rem",
  background: "bgPanel",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "grid",
  borderRadius: "3px",
  padding: "0.5rem 0.6rem",
  overflow: "hidden",
});
export const stagePane = css({
  height: "100%",
  minWidth: "0",
  display: "flex",
  flexDirection: "column",
  gap: "0.4rem",
  overflow: "hidden",
  // The stage sat flush against the rail (a measured 3px gap, zero
  // padding), gluing the stage title to the rail edge. A deliberate inline gutter separates
  // the two panels.
  paddingInlineStart: "0.75rem",
});
export const floorChip = css({
  fontSize: "0.64rem",
  color: "amber",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "amber",
  borderRadius: "2px",
  paddingInline: "0.35rem",
});
export const inspectorScroll = css({ flex: "1", minHeight: "0", overflowY: "auto" });
export const reopenButton = css({
  font: "inherit",
  fontSize: "0.68rem",
  letterSpacing: "0.06em",
  paddingInline: "0.45rem",
  paddingBlock: "0.06rem",
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
export const resizeHandle = css({
  width: "3px",
  background: "grid",
  transition: "background 0.15s ease",
  _hover: { background: "amber" },
  "&[data-resize-handle-state='drag']": { background: "amber" },
});
export const paneHeading = css({ flexShrink: 0 });

export const PANELS_AUTOSAVE_ID = "cockpit.chats.panels";
export const INSPECTOR_OPEN_KEY = "cockpit.chats.inspector-open.v1";
// The rail panel's percentage bounds — shared by the Panel props and the ~280px calibration.
export const RAIL_MIN_PERCENT = 12;
export const RAIL_MAX_PERCENT = 40;

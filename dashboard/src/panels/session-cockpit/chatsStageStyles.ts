import { css } from "../../../styled-system/css";

export const body = css({
  display: "flex",
  flexDirection: "column",
  flex: "1",
  minHeight: "0",
  minWidth: "0",
  gap: "0.3rem",
});
// The live surface stays mounted (its store keeps updating) but goes inert behind the library (§4.4).
export const hiddenBehind = css({ display: "none" });
// The keep-alive pool fixes a switch-back glitch: unloading a chat's surface on blur lost its
// scroll offset, so returning to it no longer stayed pinned to the bottom. Every chat focused in
// this stage keeps its ConversationSurface MOUNTED; only the focused one is visible. The pool is
// the positioning context for the hidden entries.
export const pool = css({
  display: "flex",
  flexDirection: "column",
  flex: "1",
  minHeight: "0",
  minWidth: "0",
  position: "relative",
});
// A hidden-but-mounted surface is taken out of the flow (absolute over the pool) and hidden with
// `visibility` — NEVER display:none. display:none destroys the scroll offset and the virtualizer's
// retained measurements, which is exactly the top-then-jump-to-bottom glitch being fixed. The DOM,
// pixel offset, and TanStack cache remain intact here; the hidden timeline separately detaches its
// observers/listeners so retaining identity does not mean retaining background work.
export const keptHidden = css({
  position: "absolute",
  top: "0",
  right: "0",
  bottom: "0",
  left: "0",
  visibility: "hidden",
  display: "flex",
  flexDirection: "column",
  minHeight: "0",
  minWidth: "0",
});

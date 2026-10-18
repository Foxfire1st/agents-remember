import { memo, useEffect, useState, type KeyboardEvent as ReactKeyboardEvent, type PointerEvent as ReactPointerEvent } from "react";

import { css, cva } from "../../styled-system/css";

// Shell layout (co-located Panda css). The shell pins to the viewport so the top + mode
// bars stay fixed and the rails / viewport scroll internally; the marker classes (cockpit--shell /
// shell__body / rail / viewport) are kept so the per-panel descendant rules + tests still resolve.
export const shell = css({
  position: "relative",
  display: "flex",
  flexDirection: "column",
  height: "100vh",
  minHeight: "0",
  overflow: "hidden",
  padding: "0.6rem 0.8rem",
  gap: "0.6rem",
  "@media (max-width: 40rem)": {
    minWidth: "0",
    maxWidth: "100%",
    overflowY: "auto",
    overflowX: "hidden",
    overflowWrap: "anywhere",
    "& .shell__body": { minWidth: "0", gridTemplateRows: "auto" },
    "& .shell__body[data-fullbleed=false]": { flex: "0 0 auto", gridTemplateRows: "auto auto auto" },
    "& .shell__body[data-fullbleed=false] > .rail": { height: "24rem" },
    "& .shell__body[data-fullbleed=false] > .viewport": { minHeight: "28rem" },
    "& .rail, & .viewport": { maxWidth: "100%" },
    "& [data-testid=open-intent-review]": { flexWrap: "wrap", minWidth: "0", maxWidth: "100%" },
  },
});
export const topbar = css({
  display: "flex",
  flexShrink: 0,
  alignItems: "center",
  justifyContent: "space-between",
  gap: "1rem",
  borderBottomWidth: "1px",
  borderBottomStyle: "solid",
  borderBottomColor: "grid",
  paddingBottom: "0.5rem",
  "@media (max-width: 40rem)": { flexWrap: "wrap", minWidth: "0" },
});
export const title = css({
  margin: "0",
  fontSize: "0.9rem",
  letterSpacing: "0.18em",
  color: "amber",
  // The brand is one lockup, never `AGENTS REMEMBER ·/MISSION/CONTROL` broken across lines.
  whiteSpace: "nowrap",
  "@media (max-width: 40rem)": { whiteSpace: "normal", overflowWrap: "anywhere" },
  textShadow: "0 0 calc(6px * var(--glow-strength)) oklch(0.82 0.16 75 / 0.5)",
});
export const statusRow = css({
  display: "flex",
  alignItems: "center",
  // The top bar wraps BETWEEN fact-chips (each of which is nowrap), never mid-phrase; the
  // cluster reflows onto a second line as a whole instead of shattering into red fragments.
  flexWrap: "wrap",
  justifyContent: "flex-end",
  gap: "0.9rem",
  rowGap: "0.3rem",
  fontSize: "0.78rem",
  "@media (max-width: 40rem)": { justifyContent: "flex-start", minWidth: "0", "& > span": { whiteSpace: "normal" } },
});
export const dim = css({ color: "muted", whiteSpace: "nowrap" });
export const effectsToggle = cva({
  base: {
    font: "inherit",
    fontSize: "0.74rem",
    letterSpacing: "0.06em",
    paddingInline: "0.45rem",
    paddingBlock: "0.08rem",
    borderRadius: "2px",
    borderWidth: "1px",
    borderStyle: "solid",
    cursor: "pointer",
    background: "transparent",
    transition: "color 0.15s ease, border-color 0.15s ease",
    _hover: { borderColor: "muted" },
    _focusVisible: { outline: "1px solid token(colors.amber)", outlineOffset: "1px" },
  },
  variants: {
    on: {
      true: { color: "amber", borderColor: "amber" },
      false: { color: "muted", borderColor: "grid" },
    },
  },
});
// The right-rail River⇄Chat switch: a small two-segment control sitting above the rail
// content. React-state driven (railView) so the rail swaps the Event River for the single-instance chat.
export const railToggle = css({
  display: "flex",
  flexShrink: 0,
  gap: "0.25rem",
  paddingBottom: "0.1rem",
});
export const railToggleButton = cva({
  base: {
    flex: "1",
    font: "inherit",
    fontSize: "0.7rem",
    letterSpacing: "0.06em",
    paddingBlock: "0.12rem",
    borderRadius: "2px",
    borderWidth: "1px",
    borderStyle: "solid",
    cursor: "pointer",
    background: "transparent",
    transition: "color 0.15s ease, border-color 0.15s ease",
    _hover: { borderColor: "muted" },
    _focusVisible: { outline: "1px solid token(colors.amber)", outlineOffset: "1px" },
  },
  variants: {
    on: {
      true: { color: "amber", borderColor: "amber" },
      false: { color: "muted", borderColor: "grid" },
    },
  },
});
export const caution = cva({
  base: { letterSpacing: "0.06em", color: "muted", whiteSpace: "nowrap" },
  variants: {
    sev: {
      clear: { color: "mint" },
      info: { color: "cyan" },
      warn: { color: "amber" },
      alarm: { color: "alarm", animation: "pulse 0.6s steps(1) infinite" },
    },
  },
});
export const connBadge = cva({
  base: { fontWeight: "600", letterSpacing: "0.1em", whiteSpace: "nowrap" },
  variants: {
    state: {
      connecting: { color: "amber" },
      live: { color: "mint" },
      "signal-lost": { color: "alarm", animation: "pulse 0.5s steps(1) infinite" },
    },
  },
});
// The body grid: the railed 3-column shell, or a single full-width column for the machine-map
// views (Engine Room / Topology), which render their own internal layout.
export const bodyGrid = cva({
  base: {
    display: "grid",
    gridTemplateRows: "minmax(0, 1fr)",
    gap: "0.7rem",
    flex: "1",
    minHeight: "0",
  },
  variants: {
    bleed: {
      false: {
        gridTemplateColumns: "minmax(300px, 1fr) minmax(420px, 2.2fr) minmax(260px, 0.95fr)",
      },
      true: { gridTemplateColumns: "1fr" },
    },
  },
});
export const rail = css({
  position: "relative", // anchors the absolutely-positioned drag handle on the rail's inner edge
  display: "flex",
  flexDirection: "column",
  gap: "0.7rem",
  minWidth: "0",
  minHeight: "0",
  overflow: "hidden", // the rail itself does not scroll — each panel scrolls on its own
});
// Operations rails are draggable (like the File Viewer's split): each rail owns a thin gutter on its
// inner edge that drags its pixel width, persisted so the layout survives a reload. Bounds keep a rail
// from collapsing to nothing or eating the centre column. The handle sits flush inside the rail edge so
// the rail's own `overflow:hidden` never clips it.
const RAIL_MIN = 220;
const RAIL_MAX = 560;
const RAIL_STEP = 24; // keyboard nudge per ArrowLeft/ArrowRight
const railHandle = cva({
  base: {
    position: "absolute",
    top: "0",
    bottom: "0",
    width: "7px",
    zIndex: "3",
    cursor: "col-resize",
    background: "transparent",
    transition: "background 0.15s ease",
    _hover: { background: "amber" },
    _focusVisible: { outline: "1px solid token(colors.amber)", outlineOffset: "-1px" },
  },
  variants: {
    side: {
      left: { right: "0" }, // left rail: gutter on its right (centre-facing) edge
      right: { left: "0" }, // right rail: gutter on its left (centre-facing) edge
    },
  },
});

function clampRail(n: number): number {
  return Math.max(RAIL_MIN, Math.min(RAIL_MAX, Math.round(n)));
}

// One rail's resize gutter. Pointer drag adjusts the rail width live (left rail grows rightward, right
// rail grows leftward); Arrow keys nudge it. The new width flows straight to the persisted state so it
// both re-lays-out and survives a reload.
// Memoized like the rail panels it sits beside (tab-switch CPU): the shell re-renders on
// every view switch with unchanged props, and the memo gate skips this handle's re-render then.
export const RailResizeHandle = memo(function RailResizeHandle({
  side,
  width,
  onResize,
}: {
  side: "left" | "right";
  width: number;
  onResize: (next: number) => void;
}) {
  const narrow = useNarrowCockpit();
  const onPointerDown = (event: ReactPointerEvent<HTMLDivElement>) => {
    event.preventDefault();
    const startX = event.clientX;
    const startWidth = width;
    event.currentTarget.setPointerCapture(event.pointerId);
    const move = (moveEvent: globalThis.PointerEvent) => {
      const delta = moveEvent.clientX - startX;
      onResize(clampRail(side === "left" ? startWidth + delta : startWidth - delta));
    };
    const up = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  };
  const onKeyDown = (event: ReactKeyboardEvent<HTMLDivElement>) => {
    const dir = event.key === "ArrowRight" ? 1 : event.key === "ArrowLeft" ? -1 : 0;
    if (!dir) return;
    event.preventDefault();
    onResize(clampRail(width + (side === "left" ? dir : -dir) * RAIL_STEP));
  };
  if (narrow) return null;
  return (
    <div
      role="slider"
      aria-orientation="vertical"
      aria-label={`Resize ${side} rail`}
      aria-valuenow={width}
      aria-valuemin={RAIL_MIN}
      aria-valuemax={RAIL_MAX}
      tabIndex={0}
      className={railHandle({ side })}
      onPointerDown={onPointerDown}
      onKeyDown={onKeyDown}
      data-testid={`rail-resize-${side}`}
    />
  );
});
export const viewport = css({
  display: "flex",
  flexDirection: "column",
  minWidth: "0",
  minHeight: "0",
  overflow: "hidden", // the viewport does not scroll — its panel scrolls on its own
});
// Chats is kept mounted across every view (hidden via display, never unmounted) so the host's
// chat frame is not loaded again on a view switch.
export const chatsLayer = css({
  display: "flex",
  flexDirection: "column",
  flex: "1",
  minHeight: "0",
  minWidth: "0",
});

export const takeoverBody = css({
  "@media (max-width: 40rem)": {
    position: "fixed",
    inset: "0",
    zIndex: "50",
    background: "bg",
  },
});

export function useNarrowCockpit(): boolean {
  const [narrow, setNarrow] = useState(() => window.matchMedia('(max-width: 40rem)').matches);
  useEffect(() => {
    const media = window.matchMedia('(max-width: 40rem)');
    const update = () => setNarrow(media.matches);
    media.addEventListener('change', update);
    return () => media.removeEventListener('change', update);
  }, []);
  return narrow;
}

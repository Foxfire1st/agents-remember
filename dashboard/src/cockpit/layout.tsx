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
    // Operations stacks its rails above and below the task document. The review page is not
    // stacked: its reading column lies over the screen and its chat column over that (MIK-R89 v2
    // rule 11), so the stacked heights never reach it.
    "& .shell__body[data-fullbleed=false]:not([data-page=review])": { flex: "0 0 auto", gridTemplateRows: "auto auto auto" },
    "& .shell__body[data-fullbleed=false]:not([data-page=review]) > .rail": { height: "24rem" },
    "& .shell__body[data-fullbleed=false]:not([data-page=review]) > .viewport": { minHeight: "28rem" },
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
// `label` and `testId` name a handle that is not one of the Operations rails' (the review page's
// tree and chat column): the Operations handles stay in the document while hidden, so a second
// handle with their name would make both ambiguous.
export const RailResizeHandle = memo(function RailResizeHandle({
  side,
  width,
  onResize,
  label,
  testId,
}: {
  side: "left" | "right";
  width: number;
  onResize: (next: number) => void;
  label?: string;
  testId?: string;
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
      aria-label={label ?? `Resize ${side} rail`}
      aria-valuenow={width}
      aria-valuemin={RAIL_MIN}
      aria-valuemax={RAIL_MAX}
      tabIndex={0}
      className={railHandle({ side })}
      onPointerDown={onPointerDown}
      onKeyDown={onKeyDown}
      data-testid={testId ?? `rail-resize-${side}`}
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

// The ids the shared sidebar toggles name as what they control (aria-controls). The right rail is
// also the review page's chat column, so the review's chat toggle names it too.
export const LEFT_RAIL_ID = "cockpit-left-rail";
export const RIGHT_RAIL_ID = "cockpit-right-rail";

// The one-line strip over the Operations task document that holds its two sidebar toggles: the
// task list's at the start, the right column's at the end (MIK-R89 v2 rule 8).
export const sidebarStrip = css({
  display: "flex",
  flexShrink: 0,
  alignItems: "center",
  justifyContent: "space-between",
  gap: "0.5rem",
  minHeight: "26px",
  marginBottom: "0.3rem",
});

// The review page's chat column (MIK-R89 v2 rules 1, 11, 12): the shell's right rail, framed like
// the reading column's other boxes. Below 1440 px it lies over the reading column; at 40rem and
// narrower over the whole screen, never wider than it.
export const reviewChatColumn = css({
  background: "bg",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "grid",
  borderRadius: "3px",
});

// The overlying chat column's own line with its toggle, at the column's edge.
export const reviewChatClose = css({
  display: "flex",
  flexShrink: 0,
  alignItems: "center",
  paddingInline: "0.3rem",
  paddingTop: "0.3rem",
});

export function useMediaQuery(query: string): boolean {
  const [matches, setMatches] = useState(() => window.matchMedia(query).matches);
  useEffect(() => {
    const media = window.matchMedia(query);
    const update = () => setMatches(media.matches);
    media.addEventListener('change', update);
    return () => media.removeEventListener('change', update);
  }, [query]);
  return matches;
}

export function useNarrowCockpit(): boolean {
  return useMediaQuery('(max-width: 40rem)');
}

import { useCallback, useState } from "react";

import { css } from "../../styled-system/css";

// The one shared sidebar toggle (MIK-R89 v2 rule 8, D359): every left and right sidebar of every page
// is hidden and shown by this control, and nothing else. The control lives in the main column, at its
// edge, never inside the sidebar it hides, so it stays reachable while that sidebar is hidden. Its look
// follows the developer's reference (panel-toggle-icon-reference-D359.png): a rounded frame with one
// divider near the panel's side, its band filled while the panel is shown.
//
// The state is remembered per page and side under `cockpit.sidebar.<page>.<side>`. The review page's
// chat side is the one exception: it always opens closed, so it is held in memory only
// (`remember: false`) and its owner resets it for each review it opens.

export type SidebarSide = "left" | "right";
export type SidebarPage = "operations" | "knowledge" | "files" | "changeset" | "notes" | "review";

export function sidebarKey(page: SidebarPage, side: SidebarSide): string {
  return `cockpit.sidebar.${page}.${side}`;
}

function storage(): Storage | undefined {
  try {
    return globalThis.localStorage;
  } catch {
    return undefined;
  }
}

function readOpen(key: string, fallback: boolean): boolean {
  try {
    const raw = storage()?.getItem(key);
    return raw === "1" ? true : raw === "0" ? false : fallback;
  } catch {
    return fallback;
  }
}

export interface SidebarState {
  open: boolean;
  setOpen: (next: boolean) => void;
  toggle: () => void;
}

export function useSidebar(
  page: SidebarPage,
  side: SidebarSide,
  options: { remember?: boolean; initial?: boolean } = {},
): SidebarState {
  const remember = options.remember ?? true;
  const initial = options.initial ?? true;
  const key = sidebarKey(page, side);
  const [open, setState] = useState<boolean>(() => (remember ? readOpen(key, initial) : initial));
  const setOpen = useCallback(
    (next: boolean) => {
      setState(next);
      if (!remember) return;
      try {
        storage()?.setItem(key, next ? "1" : "0");
      } catch {
        // A storage failure keeps the choice for this page's lifetime only.
      }
    },
    [key, remember],
  );
  const toggle = useCallback(() => setOpen(!open), [open, setOpen]);
  return { open, setOpen, toggle };
}

// A resizable page's sidebar (react-resizable-panels) is hidden in place: the panel and its
// handle stay mounted, so the sidebar's own state survives, and leave the row, so the main
// panel, the group's one shown panel, takes the whole width. The library's saved sizes stay as
// they were, and showing the sidebar again restores them. A hidden panel is out of reach.
export function sidebarPanelProps(open: boolean) {
  return open ? {} : ({ style: { display: "none" }, "aria-hidden": true, inert: true } as const);
}

export function sidebarHandleProps(open: boolean) {
  return open ? {} : ({ style: { display: "none" }, disabled: true } as const);
}

const look = css({
  width: "26px",
  height: "24px",
  display: "inline-grid",
  placeItems: "center",
  flex: "none",
  padding: "0",
  background: "transparent",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "transparent",
  borderRadius: "3px",
  color: "muted",
  cursor: "pointer",
  _hover: { color: "cyan", borderColor: "grid", background: "bgPanel" },
  "&[aria-expanded='true']": { color: "cyan" },
  _focusVisible: { outlineWidth: "1px", outlineStyle: "solid", outlineColor: "amber", outlineOffset: "1px" },
  "& svg": { width: "16px", height: "16px", display: "block" },
});

export interface SidebarToggleProps {
  side: SidebarSide;
  open: boolean;
  onToggle: () => void;
  // What the sidebar holds, in the accessible name: "Tree sidebar", "Chat sidebar", "Task list sidebar".
  name: string;
  // The id of the sidebar element (aria-controls), when it has one.
  controls?: string;
  // The key that toggles it, shown in the title (the review page's `[` and `]`).
  keyHint?: string;
  testId?: string;
}

export function SidebarToggle({ side, open, onToggle, name, controls, keyHint, testId }: SidebarToggleProps) {
  const line = side === "right" ? 10.5 : 5.5;
  const bandX = side === "right" ? 10.5 : 1.5;
  const title = `${open ? "Hide" : "Show"} ${name.toLowerCase()}${keyHint ? ` (${keyHint})` : ""}`;
  return (
    <button
      type="button"
      className={look}
      aria-label={name}
      aria-expanded={open}
      aria-controls={controls}
      title={title}
      data-testid={testId}
      data-sidebar-side={side}
      onClick={onToggle}
    >
      <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.25" aria-hidden="true">
        <rect x="1.5" y="2.5" width="13" height="11" rx="1.6" />
        {open ? (
          <rect x={bandX} y="2.5" width="4" height="11" fill="currentColor" fillOpacity=".28" stroke="none" />
        ) : null}
        <line x1={line} y1="2.5" x2={line} y2="13.5" />
      </svg>
    </button>
  );
}

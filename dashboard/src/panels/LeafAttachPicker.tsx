import { useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

import { css } from "../../styled-system/css";
import { findMasterPath, type TaskTreeNode } from "../data/taskIdentity";

// A dark, DRILL-DOWN leaf picker shared by the right-rail chat and the Chats page. It replaces the native
// <select> (whose OS-rendered list was an unthemed white "flashbang" and dumped every leaf of every series
// flat). The task hierarchy nests arbitrarily — a master can itself be a sub-task of another master — so a
// single grouped list cannot express it. Here one Panda-themed popover navigates the tree a level at a
// time: a master row drills IN (a "‹ back" breadcrumb returns), a leaf row attaches. When a master is in
// context the picker opens pre-drilled to it (see `findMasterPath`). Plain React state (no React Aria
// overlay) keeps it trivially testable: click the trigger, drill, click a leaf.

const wrap = css({ display: "inline-flex", minWidth: "0" });
const trigger = css({
  display: "inline-flex",
  alignItems: "center",
  gap: "0.3rem",
  font: "inherit",
  fontSize: "0.72rem",
  letterSpacing: "0.04em",
  paddingInline: "0.55rem",
  paddingBlock: "0.18rem",
  borderRadius: "2px",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "amber",
  color: "amber",
  background: "transparent",
  cursor: "pointer",
  _hover: { background: "rgba(232, 193, 112, 0.1)" },
  _focusVisible: { outline: "1px solid token(colors.amber)", outlineOffset: "1px" },
});
// The popover: dark panel rendered in a PORTAL to <body> with FIXED positioning, so it escapes the rail's
// `overflow: hidden` (which was clipping it mid-air) and any ancestor stacking/transform context. `align`
// decides which trigger edge it pins to; vertical placement uses the actual room above/below instead of
// an arbitrary screen-height cap. Position is measured from the trigger on open and on viewport movement.
const menu = css({
  position: "fixed",
  zIndex: "1000",
  minWidth: "15rem",
  maxWidth: "min(24rem, 82vw)",
  overflowY: "auto",
  background: "bgPanel",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "grid",
  borderRadius: "3px",
  padding: "0.2rem",
  boxShadow: "0 8px 24px rgba(0, 0, 0, 0.5)",
});
const backRow = css({
  display: "flex",
  alignItems: "center",
  gap: "0.3rem",
  width: "100%",
  textAlign: "left",
  font: "inherit",
  fontSize: "0.66rem",
  letterSpacing: "0.06em",
  textTransform: "uppercase",
  color: "amber",
  background: "transparent",
  border: "none",
  borderBottomWidth: "1px",
  borderBottomStyle: "solid",
  borderBottomColor: "grid",
  paddingInline: "0.4rem",
  paddingBlock: "0.28rem",
  marginBottom: "0.15rem",
  cursor: "pointer",
  position: "sticky",
  top: "0",
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
  _hover: { color: "ink" },
});
const row = css({
  display: "flex",
  alignItems: "center",
  gap: "0.35rem",
  width: "100%",
  textAlign: "left",
  font: "inherit",
  fontSize: "0.74rem",
  color: "ink",
  background: "transparent",
  border: "none",
  borderRadius: "2px",
  paddingInline: "0.55rem",
  paddingBlock: "0.22rem",
  cursor: "pointer",
  overflow: "hidden",
  _hover: { background: "color-mix(in oklab, var(--colors-amber) 16%, transparent)" },
  _focusVisible: { outline: "1px solid token(colors.amber)", outlineOffset: "-1px" },
});
const rowText = css({ flex: "1", minWidth: "0", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" });
const chevron = css({ flexShrink: 0, color: "amber" });
const emptyNote = css({ fontSize: "0.72rem", color: "muted", paddingInline: "0.45rem", paddingBlock: "0.3rem" });
const rolePicker = css({
  display: "flex",
  flexWrap: "wrap",
  gap: "0.25rem",
  padding: "0.35rem",
  borderBottomWidth: "1px",
  borderBottomStyle: "solid",
  borderBottomColor: "grid",
  marginBottom: "0.15rem",
});
const roleButton = css({
  font: "inherit",
  fontSize: "0.62rem",
  letterSpacing: "0.05em",
  textTransform: "uppercase",
  color: "muted",
  background: "transparent",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "grid",
  borderRadius: "2px",
  paddingInline: "0.35rem",
  paddingBlock: "0.15rem",
  cursor: "pointer",
  _selected: { color: "amber", borderColor: "amber" },
  _hover: { color: "ink" },
  _focusVisible: { outline: "1px solid token(colors.amber)", outlineOffset: "1px" },
});

export const ATTACH_SEAT_ROLES = [
  "architect",
  "orchestrator",
  "strategist",
  "designer",
  "manager",
  "worker",
  "reviewer",
  "curator",
  "system-specialist",
  "agent",
  "chat",
] as const;

export function LeafAttachPicker({
  tree,
  onPick,
  contextMaster,
  label = "Attach to leaf",
  testId = "attach-leaf-picker",
  align = "left",
  seatRole,
  roleOptions = ATTACH_SEAT_ROLES,
}: {
  tree: TaskTreeNode[];
  onPick: (leafKey: string, seatRole: string) => void;
  contextMaster?: string;
  label?: string;
  testId?: string;
  align?: "left" | "right";
  seatRole?: string;
  roleOptions?: readonly string[];
}) {
  const [open, setOpen] = useState(false);
  // The drilled path of master nodes (breadcrumb). Current level = the last node's children, or the roots.
  const [path, setPath] = useState<TaskTreeNode[]>([]);
  const [selectedRole, setSelectedRole] = useState<string | undefined>(seatRole);
  // The fixed-position anchor for the portaled menu, measured from the trigger (null until opened).
  const [coords, setCoords] = useState<{
    top?: number;
    bottom?: number;
    left?: number;
    right?: number;
    maxHeight: number;
  } | null>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);

  const measure = () => {
    const rect = triggerRef.current?.getBoundingClientRect();
    if (!rect) return;
    const viewportPadding = 8;
    const triggerGap = 4;
    const availableBelow = window.innerHeight - rect.bottom - triggerGap - viewportPadding;
    const availableAbove = rect.top - triggerGap - viewportPadding;
    const openAbove = availableBelow < 240 && availableAbove > availableBelow;
    const vertical = openAbove
      ? { bottom: window.innerHeight - rect.top + triggerGap, maxHeight: availableAbove }
      : { top: rect.bottom + triggerGap, maxHeight: availableBelow };
    setCoords(
      align === "right"
        ? {
            ...vertical,
            right: Math.max(viewportPadding, window.innerWidth - rect.right),
          }
        : {
            ...vertical,
            left: Math.max(viewportPadding, rect.left),
          },
    );
  };

  // Re-measure while open (cover scroll/resize of any container) and wire click-outside + Escape. The
  // menu is portaled out of `ref`, so the outside check spans BOTH the trigger and the portaled menu.
  useLayoutEffect(() => {
    if (!open) return;
    measure();
    const onMove = () => measure();
    const onDoc = (event: MouseEvent) => {
      const target = event.target as Node;
      if (triggerRef.current?.contains(target) || menuRef.current?.contains(target)) return;
      setOpen(false);
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    window.addEventListener("resize", onMove);
    window.addEventListener("scroll", onMove, true);
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("resize", onMove);
      window.removeEventListener("scroll", onMove, true);
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const toggle = () => {
    if (!open) {
      // Opening pre-drills to the in-context master so its leaves show first ("pre-selection via master").
      setPath(contextMaster ? findMasterPath(tree, contextMaster) : []);
      setSelectedRole(seatRole);
      measure();
    }
    setOpen((value) => !value);
  };
  const here = path.length ? path[path.length - 1] : undefined;
  const level = here ? here.children : tree;
  const drillInto = (node: TaskTreeNode) => setPath((current) => [...current, node]);
  const back = () => setPath((current) => current.slice(0, -1));
  const pick = (leafKey: string) => {
    if (!selectedRole) return;
    setOpen(false);
    setPath([]);
    onPick(leafKey, selectedRole);
  };

  return (
    <div className={wrap}>
      <button
        ref={triggerRef}
        type="button"
        className={trigger}
        aria-haspopup="menu"
        aria-expanded={open}
        data-testid={testId}
        onClick={toggle}
      >
        {label} ▾
      </button>
      {open && coords
        ? createPortal(
            <div
              ref={menuRef}
              className={menu}
              style={{
                top: coords.top,
                bottom: coords.bottom,
                left: coords.left,
                right: coords.right,
                maxHeight: coords.maxHeight,
              }}
              role="menu"
              aria-label={label}
              data-testid={`${testId}-menu`}
            >
          <div className={rolePicker} aria-label="Seat role" data-testid={`${testId}-roles`}>
            {roleOptions.map((role) => (
              <button
                key={role}
                type="button"
                className={roleButton}
                aria-pressed={selectedRole === role}
                data-selected={selectedRole === role ? "true" : undefined}
                data-testid={`${testId}-role-${role}`}
                onClick={() => setSelectedRole(role)}
              >
                {role}
              </button>
            ))}
          </div>
          {here ? (
            <button
              type="button"
              className={backRow}
              onClick={back}
              data-testid={`${testId}-back`}
              title={here.title}
            >
              ‹ {here.title}
            </button>
          ) : null}
          {level.length === 0 ? (
            <div className={emptyNote} data-testid={`${testId}-empty`}>
              {here ? "No sub-tasks here." : "No tasks available yet."}
            </div>
          ) : (
            level.map((node) =>
              node.leafKey ? (
                <button
                  key={node.key}
                  type="button"
                  role="menuitem"
                  className={row}
                  title={node.title}
                  data-testid={`${testId}-leaf`}
                  data-leaf-key={node.leafKey}
                  disabled={!selectedRole}
                  onClick={() => pick(node.leafKey as string)}
                >
                  <span className={rowText}>{node.title}</span>
                </button>
              ) : (
                <button
                  key={node.key}
                  type="button"
                  role="menuitem"
                  className={row}
                  title={node.title}
                  data-testid={`${testId}-master`}
                  data-master={node.master}
                  onClick={() => drillInto(node)}
                >
                  <span className={rowText}>{node.title}</span>
                  <span className={chevron} aria-hidden>
                    ▸
                  </span>
                </button>
              ),
            )
          )}
            </div>,
            document.body,
          )
        : null}
    </div>
  );
}

import { useState, type MouseEvent, type PointerEvent, type ReactNode } from "react";
import { GridList, GridListItem } from "react-aria-components";

import { css, cva, cx } from "../../styled-system/css";
import type { GroupedSessions, SessionGroup } from "../data/sessionGroups";
import { sessionSeatRole, type OpenSession } from "../data/sessions";
import { RankBadge } from "../grammar/RankBadge";

// The session switcher (slice 6e-2c): the open terminal/chat sessions as a left-rail list, replacing
// the old horizontal tab strip. A React Aria GridList — not a ListBox — because each row carries a
// focusable End button: ListBox rows are single focus stops, so a nested button would be keyboard-
// unreachable, while GridList gives arrow-nav between rows AND keyboard access to the row action.
// Single selection IS the active session (selectedKeys ↔ onSelect, mirroring LifecycleList); the look
// is Panda's `_selected`/`_focusVisible` state conditions (coding-guidelines: React Aria owns
// behavior, Panda owns looks), and the row's selected colour cascades to the label so selection state
// is never re-derived in JSX.
//
// L16 renders each repo-qualified sprint as a local spawn-edge forest. The forest is complete even
// when a manager is itself spawned by an orchestrator; only manager-owned descendants are
// collapsible, and visual depth is capped at two levels. Collapse state is UI-local.
const list = css({
  display: "grid",
  gap: "0.3rem",
  alignContent: "start",
  listStyle: "none",
  outline: "none",
  overflowY: "auto",
  overflowX: "hidden",
  minHeight: "0",
});
const row = css({
  display: "flex",
  alignItems: "center",
  gap: "0.3rem",
  width: "100%",
  minWidth: "0",
  background: "bg",
  color: "muted",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "grid",
  borderRadius: "2px",
  paddingLeft: "0.5rem",
  cursor: "pointer",
  outline: "none",
  _selected: { color: "amber", borderColor: "amber" },
  _focusVisible: { outline: "1px solid token(colors.amber)", outlineOffset: "1px" },
});
const label = css({
  font: "inherit",
  fontSize: "0.74rem",
  flex: "1",
  minWidth: "0",
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
  paddingBlock: "0.25rem",
});
const badge = css({
  maxWidth: "5rem",
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
  flexShrink: 1,
  fontSize: "0.64rem",
  color: "cyan",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "grid",
  borderRadius: "2px",
  paddingInline: "0.25rem",
});
const statusBadge = css({
  flexShrink: 1,
  minWidth: "0",
  maxWidth: "4rem",
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
  fontSize: "0.64rem",
  color: "muted",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "grid",
  borderRadius: "2px",
  paddingInline: "0.25rem",
});
// The binding-role chip: who this chat currently IS in the run. Colour keys the l-01
// seat — command seats glow gold/purple to match their insignia; the worker/reviewer lanes reuse the
// existing progress/attention hues. Unknown roles fall through to the muted base (never throw).
const roleChip = cva({
  base: {
    flexShrink: 1,
    minWidth: "0",
    maxWidth: "4.5rem",
    overflow: "hidden",
    textOverflow: "ellipsis",
    whiteSpace: "nowrap",
    fontSize: "0.6rem",
    textTransform: "uppercase",
    letterSpacing: "0.08em",
    color: "muted",
    borderWidth: "1px",
    borderStyle: "solid",
    borderColor: "grid",
    borderRadius: "2px",
    paddingInline: "0.3rem",
  },
  variants: {
    role: {
      architect: {
        color: "gold",
        borderColor: "color-mix(in oklch, token(colors.gold) 45%, transparent)",
      },
      orchestrator: {
        color: "gold",
        borderColor: "color-mix(in oklch, token(colors.gold) 45%, transparent)",
      },
      strategist: { color: "gold" },
      designer: {
        color: "gold",
        borderColor: "color-mix(in oklch, token(colors.gold) 45%, transparent)",
      },
      manager: {
        color: "purple",
        borderColor: "color-mix(in oklch, token(colors.purple) 45%, transparent)",
      },
      worker: { color: "cyan" },
      curator: { color: "cyan" },
      "system-specialist": { color: "cyan" },
      reviewer: { color: "amber" },
    },
  },
});
const ROLE_VALUES = [
  "architect",
  "orchestrator",
  "strategist",
  "designer",
  "manager",
  "worker",
  "curator",
  "system-specialist",
  "reviewer",
] as const;
type KnownRole = (typeof ROLE_VALUES)[number];
const KNOWN_ROLES: ReadonlySet<string> = new Set(ROLE_VALUES);
const actions = css({
  display: "flex",
  alignItems: "stretch",
  flexShrink: 0,
  borderLeftWidth: "1px",
  borderLeftStyle: "solid",
  borderLeftColor: "grid",
});
const terminateButton = css({
  font: "inherit",
  fontSize: "0.62rem",
  color: "alarm",
  background: "transparent",
  border: "none",
  cursor: "pointer",
  paddingInline: "0.3rem",
  minWidth: "1.6rem",
  _hover: { color: "alarm" },
  _focusVisible: { outline: "1px solid token(colors.amber)", outlineOffset: "1px" },
});
const tree = css({
  display: "flex",
  flexDirection: "column",
  gap: "0.35rem",
  minHeight: "0",
  overflowY: "auto",
});
const groupBox = css({
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "grid",
  background: "bgPanel",
});
const groupNested = css({ marginLeft: "22px" }); // shared sprint nesting grammar
const groupHeaderRow = css({
  display: "flex",
  alignItems: "stretch",
  borderBottomWidth: "0",
  borderBottomStyle: "solid",
  borderBottomColor: "grid",
  "&[data-open=true]": { borderBottomWidth: "1px" },
});
const groupHeader = css({
  display: "flex",
  alignItems: "center",
  gap: "0.4rem",
  width: "100%",
  font: "inherit",
  fontSize: "0.74rem",
  color: "ink",
  background: "transparent",
  border: "none",
  paddingInline: "0.5rem",
  paddingBlock: "0.35rem",
  cursor: "pointer",
  userSelect: "none",
  textAlign: "left",
  _focusVisible: { outline: "1px solid token(colors.cyan)", outlineOffset: "-1px" },
});
const cleanupButton = css({
  flex: "none",
  font: "inherit",
  fontSize: "0.64rem",
  color: "amber",
  background: "transparent",
  border: "none",
  borderLeftWidth: "1px",
  borderLeftStyle: "solid",
  borderLeftColor: "grid",
  cursor: "pointer",
  paddingInline: "0.5rem",
  _hover: { color: "ink" },
  _focusVisible: { outline: "1px solid token(colors.amber)", outlineOffset: "-1px" },
});
const groupChevron = css({
  color: "muted",
  fontSize: "0.6rem",
  flex: "none",
  transition: "transform 0.12s ease",
  "&[data-expanded=true]": { transform: "rotate(90deg)" },
  _motionReduce: { transition: "none" },
});
const groupName = css({
  flex: "1",
  minWidth: "0",
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
});
const groupCount = css({
  flex: "none",
  color: "muted",
  fontSize: "0.66rem",
  fontVariantNumeric: "tabular-nums",
});
const groupRows = css({
  padding: "0.3rem",
  minWidth: "0",
  overflowX: "hidden",
});

const rowIndent = css({ paddingLeft: "1.1rem" });
const childIndent = css({ paddingLeft: "2.2rem" });
const managerToggle = css({
  flex: "none",
  width: "1rem",
  minWidth: "1rem",
  padding: "0",
  border: "none",
  background: "transparent",
  color: "muted",
  cursor: "pointer",
  _focusVisible: { outline: "1px solid token(colors.cyan)", outlineOffset: "-1px" },
});

function orderedVisibleMembers(
  members: OpenSession[],
  collapsedManagers: Record<string, boolean>,
): Array<{ session: OpenSession; depth: 0 | 1; hasChildren: boolean }> {
  const children = new Map<string, OpenSession[]>();
  for (const session of members) {
    if (!session.spawnedBySession) continue;
    const siblings = children.get(session.spawnedBySession);
    if (siblings) siblings.push(session);
    else children.set(session.spawnedBySession, [session]);
  }
  const roleRank: Record<string, number> = {
    architect: 0,
    orchestrator: 1,
    strategist: 2,
    designer: 2,
    manager: 3,
  };
  const compare = (left: OpenSession, right: OpenSession) => {
    const liveDelta = Number((right.status ?? "running") === "running") - Number((left.status ?? "running") === "running");
    return liveDelta || (roleRank[sessionSeatRole(left)] ?? 4) - (roleRank[sessionSeatRole(right)] ?? 4) || left.id.localeCompare(right.id);
  };
  const roots = members.filter((session) => !session.spawnedBySession || !members.some((parent) => parent.id === session.spawnedBySession));
  roots.sort(compare);
  const output: Array<{ session: OpenSession; depth: 0 | 1; hasChildren: boolean }> = [];
  const emit = (parent: OpenSession, depth: 0 | 1) => {
    const nested = (children.get(parent.id) ?? []).sort(compare);
    const hasChildren = sessionSeatRole(parent) === "manager" && nested.length > 0;
    output.push({ session: parent, depth, hasChildren });
    // A non-manager parent (normally the pinned orchestrator) does not own collapse state, but
    // its descendants must still be emitted. Deeper chains remain visible at the second rail
    // level, preserving the max-two-indent contract without dropping any member.
    if (sessionSeatRole(parent) !== "manager" || !collapsedManagers[parent.id]) {
      for (const child of nested) {
        emit(child, depth === 0 ? 1 : 1);
      }
    }
  };
  for (const root of roots) emit(root, 0);
  return output;
}

function leafKeySegments(leafKey: string): { repo: string; master: string; leafId: string } | null {
  const parts = leafKey.split("/").filter(Boolean);
  if (parts.length < 3) return null;
  return { repo: parts[0], master: parts[1], leafId: parts[parts.length - 1] };
}

function sessionTitle(session: OpenSession, leafName?: string): string {
  const parts = [leafName ? `${session.label} · ${leafName}` : session.label];
  const declaredRole = session.seatRole ?? session.spawnRole;
  if (declaredRole) parts.push(`role: ${declaredRole}`);
  if (session.lifecycleId) parts.push(`lifecycle: ${session.lifecycleId}`);
  const segments = session.leafKey ? leafKeySegments(session.leafKey) : null;
  if (segments) {
    parts.push(`master: ${segments.master}`);
    parts.push(`leaf: ${segments.leafId}`);
  }
  if (session.turnState) parts.push(`turn: ${session.turnState}`);
  if (session.status && session.status !== "running") parts.push(`status: ${session.status}`);
  if (session.landedReason) parts.push(`landed: ${session.landedReason}`);
  if (session.landedAt) parts.push(`at: ${session.landedAt}`);
  if (session.landedEdge) parts.push(`edge: ${session.landedEdge}`);
  if (session.spawnedBySession) parts.push(`spawned by: ${session.spawnedBySession}`);
  return parts.join(" · ");
}

export function SessionList({
  sessions,
  activeId,
  onSelect,
  onTerminate,
  onCleanupLanded,
  leafNameFor,
  grouped,
}: {
  sessions: OpenSession[];
  activeId: string | null;
  onSelect: (id: string) => void;
  onTerminate: (id: string) => void;
  onCleanupLanded?: (sessions: OpenSession[]) => void;
  /** Resolve a bound session's leaf name (task-doc title, fallback leaf id) for the "who works on what" label. */
  leafNameFor?: (leafKey: string) => string;
  /** The G1 command-tree model (L14). Absent or group-less ⇒ today's flat list, unchanged. */
  grouped?: GroupedSessions;
}) {
  // Collapse state, keyed by group key — UI-local by design (L14: no persistence this leaf).
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({});
  const [collapsedManagers, setCollapsedManagers] = useState<Record<string, boolean>>({});
  const stopRowSelection = (event: MouseEvent<HTMLButtonElement> | PointerEvent<HTMLButtonElement>) => {
    event.stopPropagation();
  };

  const renderRow = (session: OpenSession, depth: 0 | 1 = 0, hasChildren = false) => {
    const leafName = session.leafKey
      ? (leafNameFor?.(session.leafKey) ?? session.leafKey)
      : undefined;
    // The full, untruncated identity for hover inspection.
    const fullName = sessionTitle(session, leafName);
    const seatRole = session.seatRole ?? session.spawnRole;
    const knownRole = seatRole && KNOWN_ROLES.has(seatRole)
      ? (seatRole as KnownRole)
      : undefined;
    return (
      <GridListItem
        id={session.id}
        textValue={session.label}
        className={cx(row, depth === 1 ? childIndent : hasChildren ? rowIndent : undefined)}
        data-depth={depth}
        data-testid={`chats-session-${session.id}`}
      >
        {hasChildren ? (
          <button
            type="button"
            className={managerToggle}
            aria-label={`${collapsedManagers[session.id] ? "Expand" : "Collapse"} ${session.label} children`}
            aria-expanded={!collapsedManagers[session.id]}
            onPointerDown={stopRowSelection}
            onClick={(event) => {
              stopRowSelection(event);
              setCollapsedManagers((current) => ({ ...current, [session.id]: !current[session.id] }));
            }}
          >
            {collapsedManagers[session.id] ? "▶" : "▼"}
          </button>
        ) : depth === 1 ? <span className={managerToggle} aria-hidden="true" /> : null}
        {seatRole ? (
          <span
            className={roleChip({ role: knownRole })}
            title={seatRole}
            data-known-role={knownRole ? "true" : "false"}
            data-testid={`chats-session-role-${session.id}`}
          >
            {seatRole}
          </span>
        ) : null}
        <span className={label} title={fullName}>
          {session.label}
          {leafName ? (
            <span data-testid={`chats-session-leaf-${session.id}`}>
              {" · "}
              {leafName}
            </span>
          ) : null}
        </span>
        {session.lifecycleId ? <span className={badge} title={session.lifecycleId}>{session.lifecycleId}</span> : null}
        {session.turnState ? <span className={statusBadge} title={session.turnState}>{session.turnState}</span> : null}
        {session.status && session.status !== "running" ? (
          <span className={statusBadge} title={session.status}>{session.status}</span>
        ) : null}
        <span className={actions}>
          <button
            type="button"
            className={terminateButton}
            aria-label={`Terminate ${session.label}`}
            onPointerDown={stopRowSelection}
            onClick={(event) => {
              stopRowSelection(event);
              onTerminate(session.id);
            }}
          >
            End
          </button>
        </span>
      </GridListItem>
    );
  };

  const gridListFor = (members: OpenSession[], ariaLabel: string): ReactNode => {
    const visible = orderedVisibleMembers(members, collapsedManagers);
    return (
    <GridList
      aria-label={ariaLabel}
      className={list}
      data-overflow-x="hidden"
      selectionMode="single"
      selectedKeys={activeId ? [activeId] : []}
      onSelectionChange={(keys) => {
        const id = [...keys][0];
        if (typeof id === "string") onSelect(id);
      }}
      items={visible.map(({ session }) => session)}
    >
      {(session) => {
        const item = visible.find(({ session: candidate }) => candidate.id === session.id);
        return renderRow(session, item?.depth ?? 0, item?.hasChildren ?? false);
      }}
    </GridList>
    );
  };

  // No grouping model still uses the same complete forest renderer; this keeps flat and grouped
  // paths consistent when a session catalog temporarily lacks task-document grouping data.
  if (!grouped || grouped.groups.length === 0) {
    return gridListFor(sessions, "Open sessions");
  }

  // Explicit user toggles win; otherwise a default-collapsed group (the landed archive) still
  // opens when it holds the ACTIVE session — the active chat must never be hidden by a default.
  const isCollapsed = (group: SessionGroup) =>
    collapsed[group.key] ??
    (group.defaultCollapsed && !group.sessions.some((member) => member.id === activeId));
  return (
    <div className={tree} data-testid="chats-session-tree">
      {grouped.groups.map((group) => {
        const open = !isCollapsed(group);
        return (
          <section
            key={group.key}
            className={cx(groupBox, group.nested && groupNested)}
            data-testid={`chats-group-${group.key}`}
            data-nested={group.nested || undefined}
          >
            <div className={groupHeaderRow} data-open={open || undefined}>
              <button
                type="button"
                className={groupHeader}
                aria-expanded={open}
                data-testid={`chats-group-toggle-${group.key}`}
                onClick={() =>
                  setCollapsed((current) => ({ ...current, [group.key]: open }))
                }
              >
                <span className={groupChevron} data-expanded={open} aria-hidden="true">
                  ▶
                </span>
                {group.tier ? <RankBadge tier={group.tier} size="sm" /> : null}
                <span className={groupName} title={group.label}>
                  {group.label}
                </span>
                <span className={groupCount}>{group.countLabel}</span>
              </button>
              {group.kind === "landed" && onCleanupLanded ? (
                <button
                  type="button"
                  className={cleanupButton}
                  aria-label="Close landed archive"
                  title="Close landed archive"
                  data-testid="chats-group-cleanup-landed"
                  onClick={() => onCleanupLanded(group.sessions)}
                >
                  Close
                </button>
              ) : null}
            </div>
            {open ? (
              <div className={groupRows}>{gridListFor(group.sessions, `Open sessions — ${group.label}`)}</div>
            ) : null}
          </section>
        );
      })}
      {grouped.ungrouped.length > 0
        ? gridListFor(grouped.ungrouped, "Open sessions")
        : null}
    </div>
  );
}

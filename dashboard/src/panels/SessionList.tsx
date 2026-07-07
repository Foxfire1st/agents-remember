import { useState, type MouseEvent, type PointerEvent, type ReactNode } from "react";
import { GridList, GridListItem } from "react-aria-components";

import { css, cva, cx } from "../../styled-system/css";
import type { GroupedSessions, SessionGroup } from "../data/sessionGroups";
import type { OpenSession } from "../data/sessions";
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
// L14 adds the G1 COMMAND TREE: when a `grouped` model is supplied (data/sessionGroups), sessions
// render inside collapsible groups — the command deck (gold insignia) on top, one group per claimed
// master (purple insignia + 22px indent when commanded), landed work in one collapsed archive —
// while unattached sessions keep today's flat placement below. Collapse state is UI-local (no
// persistence); each group is its own GridList so arrow-nav stays within the group.
const list = css({
  display: "grid",
  gap: "0.3rem",
  alignContent: "start",
  listStyle: "none",
  outline: "none",
  overflowY: "auto",
  minHeight: "0",
});
const row = css({
  display: "flex",
  alignItems: "center",
  gap: "0.3rem",
  width: "100%",
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
  flexShrink: 0,
  fontSize: "0.64rem",
  color: "cyan",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "grid",
  borderRadius: "2px",
  paddingInline: "0.25rem",
});
const statusBadge = css({
  flexShrink: 0,
  fontSize: "0.64rem",
  color: "muted",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "grid",
  borderRadius: "2px",
  paddingInline: "0.25rem",
});
// The spawn-role chip (L14, sketch vocabulary): who this chat IS in the run. Colour keys the l-01
// seat — command seats glow gold/purple to match their insignia; the worker/reviewer lanes reuse the
// existing progress/attention hues. Unknown roles fall through to the muted base (never throw).
const roleChip = cva({
  base: {
    flexShrink: 0,
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
      manager: {
        color: "purple",
        borderColor: "color-mix(in oklch, token(colors.purple) 45%, transparent)",
      },
      worker: { color: "cyan" },
      curator: { color: "cyan" },
      reviewer: { color: "amber" },
    },
  },
});
const ROLE_VALUES = [
  "architect",
  "orchestrator",
  "strategist",
  "manager",
  "worker",
  "curator",
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
const groupNested = css({ marginLeft: "22px" }); // the shared indent grammar (L14 sketch)
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
  borderTopWidth: "1px",
  borderTopStyle: "solid",
  borderTopColor: "grid",
  padding: "0.3rem",
});

export function SessionList({
  sessions,
  activeId,
  onSelect,
  onTerminate,
  leafNameFor,
  grouped,
}: {
  sessions: OpenSession[];
  activeId: string | null;
  onSelect: (id: string) => void;
  onTerminate: (id: string) => void;
  /** Resolve a bound session's leaf name (task-doc title, fallback leaf id) for the "who works on what" label. */
  leafNameFor?: (leafKey: string) => string;
  /** The G1 command-tree model (L14). Absent or group-less ⇒ today's flat list, unchanged. */
  grouped?: GroupedSessions;
}) {
  // Collapse state, keyed by group key — UI-local by design (L14: no persistence this leaf).
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({});
  const stopRowSelection = (event: MouseEvent<HTMLButtonElement> | PointerEvent<HTMLButtonElement>) => {
    event.stopPropagation();
  };

  const renderRow = (session: OpenSession) => {
    const leafName = session.leafKey
      ? (leafNameFor?.(session.leafKey) ?? session.leafKey)
      : undefined;
    // The full, untruncated name for the hover title (the label, plus its bound leaf) — the row
    // text-overflow-ellipses, so the title is how a long name stays readable (fix 4).
    const fullName = leafName ? `${session.label} · ${leafName}` : session.label;
    const knownRole = session.spawnRole && KNOWN_ROLES.has(session.spawnRole)
      ? (session.spawnRole as KnownRole)
      : undefined;
    return (
      <GridListItem
        id={session.id}
        textValue={session.label}
        className={row}
        data-testid={`chats-session-${session.id}`}
      >
        {session.spawnRole ? (
          <span
            className={roleChip({ role: knownRole })}
            data-known-role={knownRole ? "true" : "false"}
            data-testid={`chats-session-role-${session.id}`}
          >
            {session.spawnRole}
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
        {session.lifecycleId ? <span className={badge}>{session.lifecycleId}</span> : null}
        {session.status && session.status !== "running" ? (
          <span className={statusBadge}>{session.status}</span>
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

  const gridListFor = (members: OpenSession[], ariaLabel: string): ReactNode => (
    <GridList
      aria-label={ariaLabel}
      className={list}
      selectionMode="single"
      selectedKeys={activeId ? [activeId] : []}
      onSelectionChange={(keys) => {
        const id = [...keys][0];
        if (typeof id === "string") onSelect(id);
      }}
      items={members}
    >
      {renderRow}
    </GridList>
  );

  // No grouping model (the rail) or nothing grouped ⇒ the pre-L14 flat list, byte-identical.
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

import type { MouseEvent, PointerEvent } from "react";
import { GridList, GridListItem } from "react-aria-components";

import { css } from "../../styled-system/css";
import type { OpenSession } from "../data/sessions";

// The session switcher (slice 6e-2c): the open terminal/chat sessions as a left-rail list, replacing
// the old horizontal tab strip. A React Aria GridList — not a ListBox — because each row carries a
// focusable End button: ListBox rows are single focus stops, so a nested button would be keyboard-
// unreachable, while GridList gives arrow-nav between rows AND keyboard access to the row action.
// Single selection IS the active session (selectedKeys ↔ onSelect, mirroring LifecycleList); the look
// is Panda's `_selected`/`_focusVisible` state conditions (coding-guidelines: React Aria owns
// behavior, Panda owns looks), and the row's selected colour cascades to the label so selection state
// is never re-derived in JSX.
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

export function SessionList({
  sessions,
  activeId,
  onSelect,
  onTerminate,
}: {
  sessions: OpenSession[];
  activeId: string | null;
  onSelect: (id: string) => void;
  onTerminate: (id: string) => void;
}) {
  const stopRowSelection = (event: MouseEvent<HTMLButtonElement> | PointerEvent<HTMLButtonElement>) => {
    event.stopPropagation();
  };

  return (
    <GridList
      aria-label="Open sessions"
      className={list}
      selectionMode="single"
      selectedKeys={activeId ? [activeId] : []}
      onSelectionChange={(keys) => {
        const id = [...keys][0];
        if (typeof id === "string") onSelect(id);
      }}
      items={sessions}
    >
      {(session) => (
        <GridListItem
          id={session.id}
          textValue={session.label}
          className={row}
          data-testid={`chats-session-${session.id}`}
        >
          <span className={label}>{session.label}</span>
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
      )}
    </GridList>
  );
}

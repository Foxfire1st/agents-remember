import { Button, GridList, GridListItem } from "react-aria-components";

import { css } from "../../styled-system/css";
import type { OpenSession } from "../data/sessions";

// The session switcher (slice 6e-2c): the open terminal/chat sessions as a left-rail list, replacing
// the old horizontal tab strip. A React Aria GridList — not a ListBox — because each row carries
// focusable action buttons: ListBox rows are single focus stops, so nested buttons would be keyboard-
// unreachable, while GridList gives arrow-nav between rows AND keyboard access to the per-row actions.
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
const actionButton = css({
  font: "inherit",
  fontSize: "0.62rem",
  color: "muted",
  background: "transparent",
  border: "none",
  cursor: "pointer",
  paddingInline: "0.3rem",
  minWidth: "1.6rem",
  _hover: { color: "alarm" },
  _focusVisible: { outline: "1px solid token(colors.amber)", outlineOffset: "1px" },
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
  onDetach,
  onTerminate,
}: {
  sessions: OpenSession[];
  activeId: string | null;
  onSelect: (id: string) => void;
  onDetach: (id: string) => void;
  onTerminate: (id: string) => void;
}) {
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
            <Button
              className={actionButton}
              aria-label={`Detach ${session.label}`}
              onPress={() => onDetach(session.id)}
            >
              ✕
            </Button>
            <Button
              className={terminateButton}
              aria-label={`Terminate ${session.label}`}
              onPress={() => onTerminate(session.id)}
            >
              End
            </Button>
          </span>
        </GridListItem>
      )}
    </GridList>
  );
}

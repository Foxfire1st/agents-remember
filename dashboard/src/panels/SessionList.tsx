import { Button, GridList, GridListItem } from "react-aria-components";

import { css } from "../../styled-system/css";
import type { OpenSession } from "../data/sessions";

// The session switcher (slice 6e-2c): the open terminal/chat sessions as a left-rail list, replacing
// the old horizontal tab strip. A React Aria GridList — not a ListBox — because each row carries a
// focusable close ✕: ListBox rows are single focus stops, so a nested button would be keyboard-
// unreachable, while GridList gives arrow-nav between rows AND keyboard access to the per-row close.
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
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
  paddingBlock: "0.25rem",
});
const close = css({
  font: "inherit",
  fontSize: "0.7rem",
  color: "muted",
  background: "transparent",
  border: "none",
  cursor: "pointer",
  paddingInline: "0.3rem",
  _hover: { color: "alarm" },
  _focusVisible: { outline: "1px solid token(colors.amber)", outlineOffset: "1px" },
});

export function SessionList({
  sessions,
  activeId,
  onSelect,
  onClose,
}: {
  sessions: OpenSession[];
  activeId: string | null;
  onSelect: (id: string) => void;
  onClose: (id: string) => void;
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
          <Button
            className={close}
            aria-label={`Close ${session.label}`}
            onPress={() => onClose(session.id)}
          >
            ✕
          </Button>
        </GridListItem>
      )}
    </GridList>
  );
}

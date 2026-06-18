import { lazy, Suspense, useState } from "react";

import { css, cva } from "../../styled-system/css";
import { openTerminalSession } from "../data/terminal";

// xterm.js is heavy and probes the canvas on import, so the terminal is code-split and only pulled
// in when a session is open (keeps it out of the cockpit's initial bundle + out of the jsdom module
// graph for render tests that never open a terminal).
const Terminal = lazy(() => import("./Terminal").then((module) => ({ default: module.Terminal })));

// The Chats view (slice 6e): the visible Mode B2 surface. The **"＋ Terminal"** control asks the
// server to spawn + own a session (a shell at the workspace root, slice 6e-2a) and then the xterm
// terminal attaches over the 6d WebSocket — the dashboard owns the session it created. Each open
// session is a closable tab. Per-harness launch buttons (Claude Code / Codex / Pi.dev) land in 6e-2b.

interface OpenSession {
  id: string;
  label: string;
}

const wrap = css({
  display: "flex",
  flexDirection: "column",
  flex: "1",
  minHeight: "0",
  minWidth: "0",
  gap: "0.5rem",
});
const strip = css({
  display: "flex",
  alignItems: "center",
  gap: "0.4rem",
  flexShrink: 0,
  flexWrap: "wrap",
  paddingBottom: "0.3rem",
  borderBottomWidth: "1px",
  borderBottomStyle: "solid",
  borderBottomColor: "grid",
});
const newButton = css({
  font: "inherit",
  fontSize: "0.74rem",
  letterSpacing: "0.04em",
  paddingInline: "0.55rem",
  paddingBlock: "0.15rem",
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
const tab = cva({
  base: {
    display: "inline-flex",
    alignItems: "center",
    gap: "0.1rem",
    borderRadius: "2px",
    borderWidth: "1px",
    borderStyle: "solid",
    paddingLeft: "0.5rem",
  },
  variants: {
    active: { true: { borderColor: "amber" }, false: { borderColor: "grid" } },
  },
});
const tabLabel = cva({
  base: {
    font: "inherit",
    fontSize: "0.74rem",
    maxWidth: "16ch",
    overflow: "hidden",
    textOverflow: "ellipsis",
    whiteSpace: "nowrap",
    paddingBlock: "0.15rem",
    background: "transparent",
    border: "none",
    cursor: "pointer",
  },
  variants: {
    active: { true: { color: "amber" }, false: { color: "muted" } },
  },
});
const closeButton = css({
  font: "inherit",
  fontSize: "0.7rem",
  color: "muted",
  background: "transparent",
  border: "none",
  cursor: "pointer",
  paddingInline: "0.3rem",
  _hover: { color: "alarm" },
});
const empty = css({
  display: "flex",
  flex: "1",
  alignItems: "center",
  justifyContent: "center",
  color: "muted",
  fontSize: "0.82rem",
});

export function Chats() {
  const [sessions, setSessions] = useState<OpenSession[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [count, setCount] = useState(0);

  const newTerminal = async () => {
    const id = crypto.randomUUID();
    const ordinal = count + 1;
    setCount(ordinal);
    // Best-effort: the dev bench has no backend, but its mock socket renders the terminal anyway.
    await openTerminalSession(id, "terminal");
    setSessions((prev) => [...prev, { id, label: `Terminal ${ordinal}` }]);
    setActiveId(id);
  };

  const closeSession = (id: string) => {
    setSessions((prev) => prev.filter((session) => session.id !== id));
    setActiveId((current) => (current === id ? null : current));
  };

  return (
    <section className={wrap} data-testid="chats">
      <header className={strip}>
        <button
          type="button"
          className={newButton}
          onClick={newTerminal}
          data-testid="chats-new-terminal"
        >
          ＋ Terminal
        </button>
        {sessions.map((session) => (
          <span key={session.id} className={tab({ active: session.id === activeId })}>
            <button
              type="button"
              className={tabLabel({ active: session.id === activeId })}
              onClick={() => setActiveId(session.id)}
              data-testid={`chats-tab-${session.id}`}
            >
              {session.label}
            </button>
            <button
              type="button"
              className={closeButton}
              onClick={() => closeSession(session.id)}
              aria-label={`Close ${session.label}`}
            >
              ✕
            </button>
          </span>
        ))}
      </header>
      {activeId ? (
        <Suspense fallback={<div className={empty}>Opening terminal…</div>}>
          <Terminal key={activeId} sessionId={activeId} />
        </Suspense>
      ) : (
        <div className={empty}>＋ Terminal opens a shell the dashboard owns, at the workspace root.</div>
      )}
    </section>
  );
}

import { lazy, Suspense, useState } from "react";

import { css, cva } from "../../styled-system/css";
import { useDashboard } from "../data/store";

// xterm.js is heavy and probes the canvas on import, so the terminal is code-split and only pulled
// in when a session is actually opened (keeps it out of the cockpit's initial bundle + out of the
// jsdom module graph for render tests that never open a terminal).
const Terminal = lazy(() => import("./Terminal").then((module) => ({ default: module.Terminal })));

// The Chats view (slice 6e): the visible Mode B2 surface — a full-bleed xterm.js terminal per
// lifecycle session. A slim tab strip lists the lifecycles; the selected id is the terminal session
// id the 6d WebSocket bridge attaches to. The real per-lifecycle launch lands in 6e-2; until then a
// session must be opened out-of-band (or the dev bench supplies a mock socket via context).

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
const stripLabel = css({
  fontSize: "0.74rem",
  letterSpacing: "0.14em",
  color: "amber",
  marginRight: "0.4rem",
});
const tab = cva({
  base: {
    font: "inherit",
    fontSize: "0.74rem",
    maxWidth: "22ch",
    overflow: "hidden",
    textOverflow: "ellipsis",
    whiteSpace: "nowrap",
    paddingInline: "0.5rem",
    paddingBlock: "0.15rem",
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
    active: {
      true: { color: "amber", borderColor: "amber" },
      false: { color: "muted", borderColor: "grid" },
    },
  },
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
  const lifecycles = useDashboard((s) => s.lifecycles);
  const sessions = Object.values(lifecycles).sort((a, b) => a.id.localeCompare(b.id));
  const [active, setActive] = useState<string | null>(null);

  return (
    <section className={wrap} data-testid="chats">
      <header className={strip}>
        <span className={stripLabel}>TERMINALS</span>
        {sessions.length === 0 ? (
          <span className={css({ color: "muted", fontSize: "0.78rem" })}>No sessions.</span>
        ) : (
          sessions.map((session) => (
            <button
              key={session.id}
              type="button"
              className={tab({ active: session.id === active })}
              onClick={() => setActive(session.id)}
              title={`${session.id}${session.repoId ? ` · ${session.repoId}` : ""} · ${session.phase}`}
              data-testid={`chats-tab-${session.id}`}
            >
              {session.id}
            </button>
          ))
        )}
      </header>
      {active ? (
        <Suspense fallback={<div className={empty}>Opening terminal…</div>}>
          <Terminal key={active} sessionId={active} />
        </Suspense>
      ) : (
        <div className={empty}>Select a lifecycle to open its terminal.</div>
      )}
    </section>
  );
}

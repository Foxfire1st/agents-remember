import { lazy, Suspense, useEffect, useState } from "react";

import { css } from "../../styled-system/css";
import {
  createSession,
  registerConnection,
  sendToSession,
  sessionStore,
  useSessions,
} from "../data/sessions";
import { bracketedPaste, fetchHarnesses, sanitizeForInjection, type HarnessInfo } from "../data/terminal";
import { SessionComposer } from "./SessionComposer";
import { SessionList } from "./SessionList";

// xterm.js is heavy and probes the canvas on import, so the terminal is code-split and only pulled
// in when a session is open (keeps it out of the cockpit's initial bundle + out of the jsdom module
// graph for render tests that never open a terminal).
const Terminal = lazy(() => import("./Terminal").then((module) => ({ default: module.Terminal })));

// The Chats view (slice 6e): the visible Mode B2 surface. The **"＋ Terminal"** control asks the
// server to spawn + own a session (a shell at the workspace root, slice 6e-2a) and then the xterm
// terminal attaches over the 6d WebSocket — the dashboard owns the session it created. Per-harness
// launch buttons (slice 6e-2b) sit beside ＋ Terminal — one per *detected* harness (Claude Code /
// Codex / Pi.dev). Open sessions live in a left-rail switcher (slice 6e-2c, `SessionList`); the
// context composer (slice 6e-3) injects text into the active session's stdin. The session registry
// lives in the `data/sessions` store (6e hardening). A live terminal survives two kinds of switch
// without being re-created empty: a cockpit *view* switch (because `Cockpit` keeps <Chats> mounted,
// hidden via CSS) and a *session-tab* switch (because every open session's terminal stays mounted
// here, the inactive ones hidden via CSS — switching tabs only flips `display`, never unmounts).

// Placeholder monograms (swap for real brand glyphs later) — distinct two-letter marks so Claude
// Code and Codex don't both collapse to "C".
const MONOGRAM: Record<string, string> = { claude: "Cl", codex: "Co", pi: "Pi" };

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
// ＋ Terminal and every harness button share one golden look (slice 6e-2c: the muted/grey harness
// buttons read as disabled — give them ＋ Terminal's amber border + text).
const launchButton = css({
  display: "inline-flex",
  alignItems: "center",
  gap: "0.32rem",
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
const harnessIcon = css({ flexShrink: 0, display: "block" });
const body = css({ display: "flex", flex: "1", minHeight: "0", gap: "0.5rem" });
const sidebar = css({
  display: "flex",
  flexDirection: "column",
  flexShrink: 0,
  width: "13rem",
  minHeight: "0",
  borderRightWidth: "1px",
  borderRightStyle: "solid",
  borderRightColor: "grid",
  paddingRight: "0.4rem",
});
const terminalArea = css({
  display: "flex",
  flexDirection: "column",
  flex: "1",
  minWidth: "0",
  minHeight: "0",
  gap: "0.4rem",
});
// One layer per open session. The active layer is shown (display:flex via inline style), the rest are
// display:none but stay MOUNTED, so each xterm instance + its WebSocket survive a tab switch. Terminal
// skips fitting a 0×0 hidden layer and re-fits via its ResizeObserver when the layer is shown again.
const terminalLayer = css({
  flexDirection: "column",
  flex: "1",
  minWidth: "0",
  minHeight: "0",
});
const empty = css({
  display: "flex",
  flex: "1",
  alignItems: "center",
  justifyContent: "center",
  textAlign: "center",
  color: "muted",
  fontSize: "0.82rem",
});

/** Placeholder harness glyph: a rounded box with a monogram, `currentColor` so it tracks the button. */
function HarnessIcon({ id }: { id: string }) {
  return (
    <svg viewBox="0 0 20 20" width="14" height="14" aria-hidden="true" className={harnessIcon}>
      <rect x="1" y="1" width="18" height="18" rx="4" fill="none" stroke="currentColor" strokeWidth="1.5" />
      <text
        x="10"
        y="14"
        textAnchor="middle"
        fontSize="9"
        fontWeight="600"
        fill="currentColor"
        fontFamily="inherit"
      >
        {MONOGRAM[id] ?? (id[0] ?? "?").toUpperCase()}
      </text>
    </svg>
  );
}

export function Chats() {
  // The session registry lives in the store (shared, testable state); <Chats> is kept mounted across
  // view switches (hidden via CSS in `Cockpit`), so the live terminal + this connection ref persist.
  const sessions = useSessions((state) => state.sessions);
  const activeId = useSessions((state) => state.activeId);
  const [harnesses, setHarnesses] = useState<HarnessInfo[]>([]);

  // Detection-driven: the server reports which supported harnesses are installed; a button appears
  // only for detected ones. `[]` (no backend / failure) just leaves ＋ Terminal alone.
  useEffect(() => {
    let active = true;
    void fetchHarnesses().then((list) => {
      if (active) setHarnesses(list);
    });
    return () => {
      active = false;
    };
  }, []);

  const startSession = (label: string, kind: "terminal" | "harness", harness?: string) =>
    createSession(label, kind, harness);

  return (
    <section className={wrap} data-testid="chats">
      <header className={strip}>
        <button
          type="button"
          className={launchButton}
          onClick={() => void startSession("Terminal", "terminal")}
          data-testid="chats-new-terminal"
        >
          ＋ Terminal
        </button>
        {harnesses
          .filter((harness) => harness.detected)
          .map((harness) => (
            <button
              key={harness.id}
              type="button"
              className={launchButton}
              onClick={() => void startSession(harness.name, "harness", harness.id)}
              data-testid={`chats-new-harness-${harness.id}`}
            >
              <HarnessIcon id={harness.id} />
              <span>{harness.name}</span>
            </button>
          ))}
      </header>
      <div className={body}>
        {sessions.length > 0 && (
          <aside className={sidebar}>
            <SessionList
              sessions={sessions}
              activeId={activeId}
              onSelect={(id) => sessionStore.getState().setActive(id)}
              onClose={(id) => sessionStore.getState().close(id)}
            />
          </aside>
        )}
        <div className={terminalArea}>
          {activeId ? (
            <>
              {sessions.map((session) => (
                <div
                  key={session.id}
                  className={terminalLayer}
                  style={{ display: session.id === activeId ? "flex" : "none" }}
                  aria-hidden={session.id !== activeId}
                  data-testid={`chats-terminal-layer-${session.id}`}
                >
                  <Suspense fallback={<div className={empty}>Opening terminal…</div>}>
                    <Terminal
                      sessionId={session.id}
                      onConnection={(conn) => registerConnection(session.id, conn)}
                    />
                  </Suspense>
                </div>
              ))}
              <SessionComposer
                onSend={(text) => sendToSession(activeId, bracketedPaste(sanitizeForInjection(text)))}
              />
            </>
          ) : (
            <div className={empty}>
              ＋ Terminal opens a shell the dashboard owns; harness buttons launch a supported agent —
              both at the workspace root.
            </div>
          )}
        </div>
      </div>
    </section>
  );
}

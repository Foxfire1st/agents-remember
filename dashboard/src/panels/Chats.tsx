import { lazy, Suspense, useEffect, useState } from "react";

import { css } from "../../styled-system/css";
import {
  createSession,
  fromTerminalSessionInfo,
  notifySessionCatalogChanged,
  registerConnection,
  sendToSession,
  sessionStore,
  subscribeSessionCatalogChanges,
  useSessions,
} from "../data/sessions";
import {
  attachSessionToLeaf,
  bracketedPaste,
  cleanupLandedTerminalSessions,
  fetchHarnesses,
  fetchTerminalSessionsOrNull,
  sanitizeForInjection,
  terminateTerminalSession,
  type HarnessInfo,
} from "../data/terminal";
import { groupSessions } from "../data/sessionGroups";
import { useDashboard } from "../data/store";
import { buildTaskTree, leafIdFromKey, leafTitleForKey } from "../data/taskIdentity";
import type { TaskDocNode } from "../types/projection";
import { EmptyStateBackdrop } from "./EmptyStateBackdrop";
import { LeafAttachPicker } from "./LeafAttachPicker";
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
// lives in the `data/sessions` store (6e hardening). Cockpit view switches keep <Chats> mounted. After
// a browser refresh, only the restored active row attaches immediately; each other row mounts on first
// selection and then stays mounted while hidden so tab switches keep the xterm buffer intact.

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
const attachBadge = css({
  fontSize: "0.7rem",
  color: "muted",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "grid",
  borderRadius: "2px",
  paddingInline: "0.45rem",
  paddingBlock: "0.12rem",
});
const attachError = css({
  fontSize: "0.7rem",
  color: "alarm",
  paddingInline: "0.2rem",
});
const harnessIcon = css({ flexShrink: 0, display: "block" });
const body = css({ display: "flex", flex: "1", minHeight: "0", gap: "0.5rem" });
const sidebar = css({
  display: "flex",
  flexDirection: "column",
  flexShrink: 0,
  width: "16rem",
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
// One layer per session that has been selected in this page. The active layer is visible; previously
// visited layers stay mounted but hidden so xterm buffers survive tab switches. Restored sessions that
// have not been selected yet stay unmounted until they are visible, avoiding hidden 0x0 hydration.
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
const statusPanel = css({
  display: "flex",
  flex: "1",
  alignItems: "center",
  justifyContent: "center",
  color: "muted",
  fontSize: "0.82rem",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "grid",
  background: "bg",
});

const LAST_ACTIVE_SESSION_KEY = "ar-dashboard:last-active-chat-session";
const CATALOG_REFRESH_INTERVAL_MS = 2500;

function isInspectableSession(status: string | undefined): boolean {
  return (status ?? "running") === "running" || status === "landed";
}

function readLastActiveSessionId(): string | null {
  try {
    return window.localStorage.getItem(LAST_ACTIVE_SESSION_KEY);
  } catch {
    return null;
  }
}

function writeLastActiveSessionId(id: string | null): void {
  try {
    if (id) window.localStorage.setItem(LAST_ACTIVE_SESSION_KEY, id);
    else window.localStorage.removeItem(LAST_ACTIVE_SESSION_KEY);
  } catch {
    // localStorage can be unavailable in private contexts; it is only a UI preference.
  }
}

async function hydrateTerminalSessionsFromCatalog(
  allowEmpty: boolean,
  excludeSessionIds: ReadonlySet<string> = new Set(),
): Promise<void> {
  const list = await fetchTerminalSessionsOrNull();
  if (list === null || (list.length === 0 && !allowEmpty)) return;
  const sessions = list
    .filter((session) => !excludeSessionIds.has(session.id))
    .map(fromTerminalSessionInfo);
  sessionStore.getState().hydrate(sessions, readLastActiveSessionId());
}

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

export function Chats({
  selectedLifecycleId,
  selectedLeafKey,
  taskDocuments = [],
  contextMaster,
}: {
  selectedLifecycleId?: string;
  selectedLeafKey?: string;
  taskDocuments?: TaskDocNode[];
  contextMaster?: string;
}) {
  // The session registry lives in the store (shared, testable state); <Chats> is kept mounted across
  // view switches (hidden via CSS in `Cockpit`), so the live terminal + this connection ref persist.
  const sessions = useSessions((state) => state.sessions);
  const activeId = useSessions((state) => state.activeId);
  const activeSession = sessions.find((session) => session.id === activeId);
  const activeSessionIsRunning = (activeSession?.status ?? "running") === "running";
  const [mountedSessionIds, setMountedSessionIds] = useState<Set<string>>(() => new Set());
  const [harnesses, setHarnesses] = useState<HarnessInfo[]>([]);
  // The attached-leaf name resolver for the session list ("who works on what"): the bound leaf's task-doc
  // title, falling back to the leaf id.
  const leafNameFor = (leafKey: string): string =>
    leafTitleForKey(taskDocuments, leafKey) ?? leafIdFromKey(leafKey);
  // A transient note for a refused leaf attach (the server's 409 leaf-taken).
  const [leafAttachError, setLeafAttachError] = useState<string | null>(null);
  const [landedCleanupNote, setLandedCleanupNote] = useState<string | null>(null);

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

  useEffect(() => {
    let active = true;
    void fetchTerminalSessionsOrNull().then((list) => {
      if (!active || list === null || list.length === 0) return;
      sessionStore.getState().hydrate(list.map(fromTerminalSessionInfo), readLastActiveSessionId());
    });
    return () => {
      active = false;
    };
  }, []);

  useEffect(
    () =>
      subscribeSessionCatalogChanges((reason, sessionId) => {
        if (reason === "terminate" && sessionId) {
          sessionStore.getState().setStatus(sessionId, "terminated");
          sessionStore.getState().close(sessionId);
          void hydrateTerminalSessionsFromCatalog(true, new Set([sessionId]));
          return;
        }
        void hydrateTerminalSessionsFromCatalog(true);
      }),
    [],
  );

  useEffect(() => {
    const interval = window.setInterval(() => {
      void hydrateTerminalSessionsFromCatalog(false);
    }, CATALOG_REFRESH_INTERVAL_MS);
    return () => window.clearInterval(interval);
  }, []);

  useEffect(() => {
    writeLastActiveSessionId(activeId);
  }, [activeId]);

  useEffect(() => {
    setMountedSessionIds((current) => {
      const sessionIds = new Set(sessions.map((session) => session.id));
      const next = new Set([...current].filter((id) => sessionIds.has(id)));
      if (activeSession && isInspectableSession(activeSession.status)) {
        next.add(activeSession.id);
      }
      if (next.size === current.size && [...next].every((id) => current.has(id))) return current;
      return next;
    });
  }, [activeSession, sessions]);

  const startSession = (label: string, kind: "terminal" | "harness", harness?: string) =>
    selectedLifecycleId
      ? createSession(label, kind, harness, selectedLifecycleId)
      : createSession(label, kind, harness);

  const attachActive = () => {
    if (!activeSession || !activeSessionIsRunning || activeSession.lifecycleId || !selectedLifecycleId) return;
    sessionStore.getState().setLifecycle(activeSession.id, selectedLifecycleId);
  };

  // Attach or move the active session to ANY chosen leaf via the server (the uniqueness arbiter) — NOT
  // just the leaf you happen to be viewing. On success bind the leaf locally + broadcast the catalog
  // change; on 409 the leaf already has a same-role running session, so surface a note instead of binding.
  const attachActiveLeaf = async (leafKey: string) => {
    if (!activeSession || !activeSessionIsRunning || !leafKey || activeSession.leafKey === leafKey) return;
    setLeafAttachError(null);
    const result = await attachSessionToLeaf(activeSession.id, leafKey);
    if (result === "ok") {
      sessionStore.getState().applyLeafAssignment(activeSession.id, leafKey);
      notifySessionCatalogChanged("leaf", activeSession.id);
    } else if (result === "leaf-taken") {
      setLeafAttachError("leaf already has a chat");
    } else {
      setLeafAttachError("could not attach to leaf");
    }
  };

  // The master→…→leaf tree the attach picker drills (nested masters supported). The picker opens
  // pre-drilled to the in-context master — the explicit `contextMaster`, else the viewed leaf's master
  // (`selectedLeafKey`'s middle segment) — so the relevant series surfaces first.
  const leafTree = buildTaskTree(taskDocuments);
  const pickerContextMaster =
    contextMaster ?? (selectedLeafKey ? selectedLeafKey.split("/").filter(Boolean)[1] : undefined);

  const terminateSession = async (id: string) => {
    if (await terminateTerminalSession(id)) {
      sessionStore.getState().setStatus(id, "terminated");
      sessionStore.getState().close(id);
      notifySessionCatalogChanged("terminate", id);
    }
  };

  const cleanupLandedSessions = async (members: { id: string }[]) => {
    setLandedCleanupNote(null);
    const result = await cleanupLandedTerminalSessions(members.map((member) => member.id));
    if (!result) {
      setLandedCleanupNote("cleanup failed");
      return;
    }
    for (const id of result.closedSessions) {
      sessionStore.getState().setStatus(id, "terminated");
      sessionStore.getState().close(id);
    }
    notifySessionCatalogChanged("terminate");
    void hydrateTerminalSessionsFromCatalog(true, new Set(result.closedSessions));
    setLandedCleanupNote(`${result.closed} closed · ${result.skipped} skipped`);
  };

  // The G1 command tree (L14): group the sidebar by claim + spawn-role provenance. Enclosures come
  // from the projection store (live-vs-landed truth); with no orchestration task and no leaf claims
  // this derives zero groups and the SessionList renders today's flat list unchanged.
  const enclosureById = useDashboard((state) => state.enclosures);
  const grouped = groupSessions({
    sessions,
    taskDocuments,
    enclosures: Object.values(enclosureById),
  });

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
        {selectedLifecycleId && activeSession?.lifecycleId === selectedLifecycleId ? (
          <span className={attachBadge}>task {selectedLifecycleId}</span>
        ) : selectedLifecycleId && activeSessionIsRunning && activeSession && !activeSession.lifecycleId ? (
          <button
            type="button"
            className={launchButton}
            onClick={attachActive}
            data-testid="chats-attach-lifecycle"
          >
            Attach {selectedLifecycleId}
          </button>
        ) : activeSession?.lifecycleId ? (
          <span className={attachBadge}>task {activeSession.lifecycleId}</span>
        ) : null}
        {activeSession?.leafKey ? (
          <span className={attachBadge} data-testid="chats-leaf-badge">
            leaf {leafNameFor(activeSession.leafKey)}
          </span>
        ) : null}
        {activeSession && activeSessionIsRunning && leafTree.length > 0 ? (
          <LeafAttachPicker
            tree={leafTree}
            contextMaster={pickerContextMaster}
            onPick={(leafKey) => void attachActiveLeaf(leafKey)}
            testId="chats-attach-leaf-picker"
            label={activeSession.leafKey ? "Move leaf" : "Attach to leaf"}
            align="left"
          />
        ) : null}
        {leafAttachError ? (
          <span className={attachError} data-testid="chats-leaf-attach-error">
            {leafAttachError}
          </span>
        ) : null}
        {landedCleanupNote ? (
          <span className={attachBadge} data-testid="chats-landed-cleanup-status">
            {landedCleanupNote}
          </span>
        ) : null}
      </header>
      <div className={body}>
        {sessions.length > 0 && (
          <aside className={sidebar}>
            <SessionList
              sessions={sessions}
              activeId={activeId}
              onSelect={(id) => sessionStore.getState().setActive(id)}
              onTerminate={(id) => void terminateSession(id)}
              onCleanupLanded={(members) => void cleanupLandedSessions(members)}
              leafNameFor={leafNameFor}
              grouped={grouped}
            />
          </aside>
        )}
        <div className={terminalArea}>
          {activeSession ? (
            <>
              {sessions
                .filter((session) => session.id === activeSession.id || mountedSessionIds.has(session.id))
                .map((session) => {
                  const visible = session.id === activeSession.id;
                  return (
                    <div
                      key={session.id}
                      className={terminalLayer}
                      style={{ display: visible ? "flex" : "none" }}
                      aria-hidden={!visible}
                      data-testid={`chats-terminal-layer-${session.id}`}
                    >
                      {isInspectableSession(session.status) ? (
                        <Suspense fallback={<div className={empty}>Opening terminal…</div>}>
                          <Terminal
                            sessionId={session.id}
                            readOnly={session.status === "landed"}
                            onConnection={(conn) => registerConnection(session.id, conn)}
                          />
                        </Suspense>
                      ) : (
                        <div className={statusPanel} data-testid={`chats-session-status-${session.id}`}>
                          session {session.status}
                        </div>
                      )}
                    </div>
                  );
                })}
              {activeSessionIsRunning ? (
                <SessionComposer
                  onSend={(text) =>
                    sendToSession(activeSession.id, bracketedPaste(sanitizeForInjection(text)))
                  }
                />
              ) : null}
            </>
          ) : (
            <EmptyStateBackdrop src="/assets/sc2-adjutant-boomerang.mp4">
              ＋ Terminal opens a shell the dashboard owns; harness buttons launch a supported agent —
              both at the workspace root.
            </EmptyStateBackdrop>
          )}
        </div>
      </div>
    </section>
  );
}

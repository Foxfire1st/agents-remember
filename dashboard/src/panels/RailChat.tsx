import { lazy, Suspense, useEffect, useState } from "react";

import { css } from "../../styled-system/css";
import {
  attachSeatRole,
  createSession,
  notifySessionCatalogChanged,
  pasteDraftToSession,
  registerConnection,
  sendToSession,
  sessionRole,
  sessionSeatRole,
  sessionStore,
  useSessions,
  type OpenSession,
  type SessionRole,
} from "../data/sessions";
import {
  attachSessionToLeaf,
  bracketedPaste,
  fetchHarnesses,
  sanitizeForInjection,
  terminateTerminalSession,
  type HarnessInfo,
} from "../data/terminal";
import { buildTaskTree, leafIdFromKey, qualifiedLeafKey } from "../data/taskIdentity";
import type { EngineProcessNode, TaskDocNode, TaskStepNode } from "../types/projection";
import { LeafAttachPicker } from "./LeafAttachPicker";
import { SessionComposer } from "./SessionComposer";

// The single-instance right-rail chat viewer (slice L5 S2/S3, fixes 2-4): the rail toggles between the
// Event River and THIS surface. It is anchored on the durable QUALIFIED LEAF ID (`leafKey`), not the
// enclosure, so it resolves with no live worktree and after finalize.
//
// Create-from-anywhere (L5 fix): a chat is NEVER gated on a leaf. When a leaf is being viewed the rail
// shows that leaf's chat + (optional) terminal; when NO leaf is viewed it shows the latest UNATTACHED
// (free) chat/terminal and still lets you start one. A free chat carries an "Attach to leaf ▾" picker so
// it can be moved onto ANY projected leaf afterwards (the chat then "moves to that leaf"). The focused
// agent seat and optional plain terminal render their current binding roles; the full multi-role fleet
// remains available in the session rail. Each pane reuses the same `Terminal` +
// `SessionComposer` + the shared connection registry as the Chats page (one xterm/WebSocket per session);
// the Chats-page row and this rail surface the same session because the registry is shared.

const Terminal = lazy(() => import("./Terminal").then((module) => ({ default: module.Terminal })));

const wrap = css({
  display: "flex",
  flexDirection: "column",
  flex: "1",
  minHeight: "0",
  minWidth: "0",
  gap: "0.4rem",
});
const heading = css({
  display: "flex",
  alignItems: "center",
  gap: "0.4rem",
  flexShrink: 0,
  minWidth: "0",
  fontSize: "0.7rem",
  letterSpacing: "0.06em",
  color: "muted",
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
});
const terminalArea = css({
  display: "flex",
  flexDirection: "column",
  flex: "1",
  minWidth: "0",
  minHeight: "0",
  gap: "0.4rem",
});
// One split pane (chat or terminal): a header row + the terminal + its composer, stacked and sharing
// the available height with the other pane (each `flex:1`) so two panes split the rail vertically.
const pane = css({
  display: "flex",
  flexDirection: "column",
  flex: "1",
  minWidth: "0",
  minHeight: "0",
  gap: "0.3rem",
});
const paneHeader = css({
  display: "flex",
  alignItems: "center",
  gap: "0.4rem",
  flexShrink: 0,
  minWidth: "0",
});
const paneTitle = css({
  flex: "1",
  minWidth: "0",
  fontSize: "0.68rem",
  letterSpacing: "0.05em",
  color: "muted",
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
});
const terminalLayer = css({
  display: "flex",
  flexDirection: "column",
  flex: "1",
  minWidth: "0",
  minHeight: "0",
});
const empty = css({
  display: "flex",
  flex: "1",
  flexDirection: "column",
  alignItems: "center",
  justifyContent: "center",
  textAlign: "center",
  gap: "0.6rem",
  color: "muted",
  fontSize: "0.78rem",
  paddingInline: "0.6rem",
});
const startRow = css({
  display: "flex",
  flexWrap: "wrap",
  justifyContent: "center",
  gap: "0.4rem",
});
// A thin affordance bar for the missing slot (start a chat / open a terminal / attach to a leaf) when the
// other slot already shows a pane, so the split can be completed without leaving the rail.
const slotBar = css({
  display: "flex",
  flexShrink: 0,
  flexWrap: "wrap",
  alignItems: "center",
  gap: "0.4rem",
});
const startButton = css({
  display: "inline-flex",
  alignItems: "center",
  gap: "0.3rem",
  font: "inherit",
  fontSize: "0.72rem",
  letterSpacing: "0.04em",
  paddingInline: "0.6rem",
  paddingBlock: "0.22rem",
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
const attachError = css({
  fontSize: "0.68rem",
  color: "alarm",
  paddingInline: "0.2rem",
  flexShrink: 0,
});
const contextNote = css({
  fontSize: "0.68rem",
  color: "amber",
  paddingInline: "0.2rem",
  flexShrink: 0,
});
const terminateButton = css({
  flexShrink: 0,
  font: "inherit",
  fontSize: "0.62rem",
  letterSpacing: "0.04em",
  color: "alarm",
  background: "transparent",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "grid",
  borderRadius: "2px",
  paddingInline: "0.35rem",
  paddingBlock: "0.05rem",
  cursor: "pointer",
  _hover: { borderColor: "alarm" },
  _focusVisible: { outline: "1px solid token(colors.amber)", outlineOffset: "1px" },
});

function isRunning(session: OpenSession): boolean {
  return (session.status ?? "running") === "running";
}

function stepLine(step: TaskStepNode): string {
  return `- [${step.status}] ${step.id ? `${step.id} -- ` : ""}${step.title}`;
}

function findLeafProcess(
  leafKey: string,
  doc: TaskDocNode,
  engineProcesses: EngineProcessNode[],
): EngineProcessNode | undefined {
  const leafId = leafIdFromKey(leafKey).toLowerCase();
  return engineProcesses.find(
    (node) =>
      (doc.lifecycleId && node.lifecycleId === doc.lifecycleId) ||
      node.leafId.toLowerCase() === leafId,
  );
}

function buildLeafContextPackage({
  leafKey,
  taskDocuments,
  engineProcesses,
}: {
  leafKey: string;
  taskDocuments: TaskDocNode[];
  engineProcesses: EngineProcessNode[];
}): string | null {
  const doc = taskDocuments.find((item) => qualifiedLeafKey(item) === leafKey);
  if (!doc) return null;
  const process = findLeafProcess(leafKey, doc, engineProcesses);
  const lines = [
    "Leaf context",
    "",
    `Task: ${doc.id} -- ${doc.title}`,
    `Status: ${doc.status}`,
    `Leaf key: ${leafKey}`,
    `Task document: ${doc.docPath}`,
    doc.lifecycleId ? `Lifecycle: ${doc.lifecycleId}` : null,
    process?.worktreeGroup ? `Worktree group: ${process.worktreeGroup}` : null,
    process?.codeWorktree.path ? `Code worktree: ${process.codeWorktree.path}` : null,
    process?.memoryWorktree?.path ? `Memory worktree: ${process.memoryWorktree.path}` : null,
    "",
    "Objective",
    doc.objective || "(none projected)",
    "",
    "Requirements",
    ...(doc.requirements.length > 0
      ? doc.requirements.map((item) => `- ${item}`)
      : ["- (none projected)"]),
    "",
    "Top-level steps",
    ...(doc.steps.length > 0 ? doc.steps.map(stepLine) : ["- (none projected)"]),
    "",
    "Instruction",
    "Attach yourself to this lifecycle/leaf before working. Use this task document as the scope anchor.",
  ];
  return lines.filter((line): line is string => line !== null).join("\n");
}

export function RailChat({
  leafKey,
  selectedLifecycleId,
  taskDocuments = [],
  engineProcesses = [],
  contextMaster,
}: {
  leafKey?: string;
  selectedLifecycleId?: string;
  taskDocuments?: TaskDocNode[];
  engineProcesses?: EngineProcessNode[];
  contextMaster?: string;
}) {
  const sessions = useSessions((state) => state.sessions);
  const [harnesses, setHarnesses] = useState<HarnessInfo[]>([]);
  // A transient note for a refused leaf attach (the server's 409 leaf-taken).
  const [leafAttachError, setLeafAttachError] = useState<string | null>(null);
  const [leafContextNote, setLeafContextNote] = useState<string | null>(null);

  // Detection-driven: the server reports which supported harnesses are installed; "start chat" offers
  // one button per detected harness (Claude Code / Codex / Pi.dev). `[]` (no backend) leaves only the
  // "open terminal" affordance — the same posture as the Chats page.
  useEffect(() => {
    let active = true;
    void fetchHarnesses().then((list) => {
      if (active) setHarnesses(list);
    });
    return () => {
      active = false;
    };
  }, []);

  // The session shown in each role: when a leaf is being viewed, that leaf's session; otherwise the most
  // recent UNATTACHED (free) session of that role — so a chat created off-leaf still surfaces here and can
  // be attached to a leaf afterwards (create-from-anywhere). Most-recent (reverse) so a just-created chat
  // shows immediately.
  const matches = (session: OpenSession, role: SessionRole): boolean =>
    isRunning(session) &&
    sessionRole(session) === role &&
    (leafKey ? session.leafKey === leafKey : !session.leafKey);
  const currentSession = (role: SessionRole): OpenSession | undefined =>
    [...sessions].reverse().find((session) => matches(session, role));
  const chatSession = currentSession("chat");
  const terminalSession = currentSession("terminal");
  // A free chat (no leaf) is the one that can be moved onto a leaf via the picker.
  const freeChat = chatSession && !chatSession.leafKey ? chatSession : undefined;

  // Mounted-but-hidden keep-alive (mirror the Chats page): once a chat/terminal has been surfaced here,
  // keep it mounted while hidden so switching leaves — or attaching a free chat onto a leaf — does not
  // tear down its xterm buffer / live WebSocket.
  const [mountedSessionIds, setMountedSessionIds] = useState<Set<string>>(() => new Set());
  useEffect(() => {
    setMountedSessionIds((current) => {
      const sessionIds = new Set(sessions.map((session) => session.id));
      const next = new Set([...current].filter((id) => sessionIds.has(id)));
      if (chatSession) next.add(chatSession.id);
      if (terminalSession) next.add(terminalSession.id);
      if (next.size === current.size && [...next].every((id) => current.has(id))) return current;
      return next;
    });
  }, [chatSession, terminalSession, sessions]);

  const detected = harnesses.filter((harness) => harness.detected);

  const deliverLeafContext = async (sessionId: string, lk: string) => {
    setLeafContextNote(null);
    const packet = buildLeafContextPackage({ leafKey: lk, taskDocuments, engineProcesses });
    if (!packet) return;
    const status = await pasteDraftToSession(sessionId, packet);
    if (status === "unconfirmed") setLeafContextNote("context delivery unconfirmed");
  };

  // Start is NEVER gated on a leaf: pass the viewed leaf (may be undefined → an unattached/free chat).
  const startChat = (harness: HarnessInfo) => {
    void (async () => {
      const sessionId = await createSession(harness.name, "harness", harness.id, selectedLifecycleId, leafKey);
      if (leafKey) await deliverLeafContext(sessionId, leafKey);
    })();
  };
  const openTerminal = () => {
    void createSession("Terminal", "terminal", undefined, selectedLifecycleId, leafKey);
  };
  const terminate = async (id: string) => {
    if (await terminateTerminalSession(id)) {
      sessionStore.getState().setStatus(id, "terminated");
      sessionStore.getState().close(id);
      notifySessionCatalogChanged("terminate", id);
    }
  };

  // The master→…→leaf tree the attach picker drills (nested masters supported). The server is the
  // uniqueness arbiter; a 409 surfaces a note instead of a bind.
  const leafTree = buildTaskTree(taskDocuments);
  const attachChatToLeaf = async (sessionId: string, lk: string, seatRole: string) => {
    if (!lk) return;
    const current = sessionStore.getState().sessions.find((session) => session.id === sessionId);
    if (current?.leafKey === lk) return;
    setLeafAttachError(null);
    const result = await attachSessionToLeaf(sessionId, lk, seatRole);
    if (result === "ok") {
      sessionStore.getState().applyLeafAssignment(sessionId, lk, seatRole);
      notifySessionCatalogChanged("leaf", sessionId);
      await deliverLeafContext(sessionId, lk);
    } else if (result === "leaf-taken") {
      setLeafAttachError(`leaf already has a ${seatRole} seat`);
    } else {
      setLeafAttachError("could not attach to leaf");
    }
  };

  const startChatAffordance =
    detected.length > 0 ? (
      <div className={startRow} data-testid="rail-start-chat">
        {detected.map((harness) => (
          <button
            key={harness.id}
            type="button"
            className={startButton}
            onClick={() => startChat(harness)}
            data-testid={`rail-start-chat-${harness.id}`}
          >
            ＋ {harness.name}
          </button>
        ))}
      </div>
    ) : (
      <span data-testid="rail-no-harness">No agent detected on PATH.</span>
    );
  const openTerminalAffordance = (
    <button
      type="button"
      className={startButton}
      onClick={openTerminal}
      data-testid="rail-open-terminal"
    >
      ＋ Terminal
    </button>
  );
  const visibleIds = new Set(
    [chatSession?.id, terminalSession?.id].filter((id): id is string => Boolean(id)),
  );
  const keepAlive = sessions.filter(
    (session) => mountedSessionIds.has(session.id) && !visibleIds.has(session.id) && isRunning(session),
  );

  return (
    <section className={wrap} data-testid="rail-chat">
      <header className={heading} data-testid="rail-chat-heading">
        {leafKey ? `Chat · ${leafIdFromKey(leafKey)}` : "Chat"}
      </header>
      {leafContextNote ? (
        <span className={contextNote} data-testid="rail-leaf-context-note" role="status">
          {leafContextNote}
        </span>
      ) : null}
      <div className={terminalArea}>
        {!chatSession && !terminalSession ? (
          <div className={empty} data-testid="rail-chat-empty">
            <span>
              {leafKey
                ? "No chat is attached to this leaf yet."
                : "Start a chat anywhere — attach it to a leaf any time."}
            </span>
            {startChatAffordance}
            {openTerminalAffordance}
          </div>
        ) : (
          <>
            {chatSession && leafTree.length > 0 ? (
              <div className={slotBar} data-testid="rail-attach-row">
                {freeChat ? <span>Free chat —</span> : null}
                <LeafAttachPicker
                  tree={leafTree}
                  contextMaster={contextMaster}
                  onPick={(lk, seatRole) => void attachChatToLeaf(chatSession.id, lk, seatRole)}
                  testId="rail-attach-leaf-picker"
                  label={chatSession.leafKey ? "Move leaf" : "Attach to leaf"}
                  align="right"
                  seatRole={attachSeatRole(chatSession)}
                />
                {leafAttachError ? (
                  <span className={attachError} data-testid="rail-leaf-attach-error">
                    {leafAttachError}
                  </span>
                ) : null}
              </div>
            ) : null}
            {chatSession ? (
              <Pane role="chat" session={chatSession} onTerminate={terminate} />
            ) : (
              <div className={slotBar}>{startChatAffordance}</div>
            )}
            {terminalSession ? (
              <Pane role="terminal" session={terminalSession} onTerminate={terminate} />
            ) : (
              <div className={slotBar}>{openTerminalAffordance}</div>
            )}
            {keepAlive.map((session) => (
              <div
                key={session.id}
                style={{ display: "none" }}
                aria-hidden
                data-testid={`rail-chat-keepalive-${session.id}`}
              >
                <Suspense fallback={null}>
                  <Terminal
                    sessionId={session.id}
                    onConnection={(conn) => registerConnection(session.id, conn)}
                  />
                </Suspense>
              </div>
            ))}
          </>
        )}
      </div>
    </section>
  );
}

// One pane (chat or terminal): a truncating header with a hover-revealed full name (fix 4) and a
// terminate control (fix 3), the live terminal, and its composer.
function Pane({
  role,
  session,
  onTerminate,
}: {
  role: SessionRole;
  session: OpenSession;
  onTerminate: (id: string) => void;
}) {
  const seatRole = sessionSeatRole(session);
  return (
    <div className={pane} data-testid={`rail-pane-${role}`}>
      <div className={paneHeader}>
        <span className={paneTitle} title={`${seatRole} · ${session.label}`}>
          {seatRole} · {session.label}
        </span>
        <button
          type="button"
          className={terminateButton}
          onClick={() => onTerminate(session.id)}
          aria-label={`Terminate ${session.label}`}
          data-testid={`rail-terminate-${role}`}
        >
          End
        </button>
      </div>
      <div className={terminalLayer}>
        <Suspense fallback={<div className={empty}>Opening {role}…</div>}>
          <Terminal
            sessionId={session.id}
            onConnection={(conn) => registerConnection(session.id, conn)}
          />
        </Suspense>
      </div>
      <SessionComposer
        onSend={(text) => sendToSession(session.id, bracketedPaste(sanitizeForInjection(text)))}
      />
    </div>
  );
}

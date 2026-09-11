import { lazy, memo, Suspense, useEffect, useState } from 'react';

import { css } from '../../styled-system/css';
import {
  attachSeatRole,
  createSession,
  notifySessionCatalogChanged,
  registerConnection,
  sessionSeatRole,
  sessionStore,
  terminalOpenFailureMessage,
  type OpenSession,
  type SessionRole,
} from '../data/sessions';
import {
  ROLE_LABELS,
  useRailChatSessions,
  type SprintRole,
  type TaskChatRole,
} from '../data/railChatSelection';
import { reviewerContextLabel } from '../data/reviewerContext';
import { submitSessionText, waitForSubmissionReady } from '../data/submitClient';
import {
  attachSessionToTask,
  fetchHarnesses,
  terminateTerminalSession,
  type HarnessInfo,
} from '../data/terminal';
import {
  buildTaskTree,
  leafIdFromKey,
  qualifiedLeafKey,
  sameTaskDocumentRef,
  taskDocumentRefForDoc,
  type TaskTreeNode,
} from '../data/taskIdentity';
import type { EngineProcessNode, TaskDocNode, TaskStepNode } from '../types/projection';
import type { TaskDocumentRef } from '../types/terminalCatalog';
import { LeafAttachPicker } from './LeafAttachPicker';
import { SessionComposer } from './SessionComposer';

// The single-instance right-rail chat viewer: the rail toggles between the
// Event River and THIS surface. It is anchored on the durable QUALIFIED LEAF ID (`leafKey`), not the
// enclosure, so it resolves with no live worktree and after finalize.
//
// Create-from-anywhere: a chat is NEVER gated on a leaf. When a leaf is being viewed the rail
// shows that leaf's chat + (optional) terminal; when NO leaf is viewed it shows the latest UNATTACHED
// (free) chat/terminal and still lets you start one. A free chat carries an "Attach to leaf ▾" picker so
// it can be moved onto ANY projected leaf afterwards (the chat then "moves to that leaf"). The focused
// agent seat and optional plain terminal render their current binding roles; the full multi-role fleet
// remains available in the session rail. Each pane reuses the same `Terminal` +
// `SessionComposer` + the shared connection registry as the Chats cockpit (one xterm/WebSocket per
// session); both surfaces resolve the same catalog identity.

const Terminal = lazy(() => import('./Terminal').then((module) => ({ default: module.Terminal })));

const wrap = css({
  display: 'flex',
  flexDirection: 'column',
  flex: '1',
  minHeight: '0',
  minWidth: '0',
  gap: '0.4rem',
});
const heading = css({
  display: 'flex',
  alignItems: 'center',
  gap: '0.4rem',
  flexShrink: 0,
  minWidth: '0',
  fontSize: '0.7rem',
  letterSpacing: '0.06em',
  color: 'muted',
  overflow: 'hidden',
  textOverflow: 'ellipsis',
  whiteSpace: 'nowrap',
});
const terminalArea = css({
  display: 'flex',
  flexDirection: 'column',
  flex: '1',
  minWidth: '0',
  minHeight: '0',
  gap: '0.4rem',
});
// One split pane (chat or terminal): a header row + the terminal + its composer, stacked and sharing
// the available height with the other pane (each `flex:1`) so two panes split the rail vertically.
const pane = css({
  display: 'flex',
  flexDirection: 'column',
  flex: '1',
  minWidth: '0',
  minHeight: '0',
  gap: '0.3rem',
});
const paneHeader = css({
  display: 'flex',
  alignItems: 'center',
  gap: '0.4rem',
  flexShrink: 0,
  minWidth: '0',
});
const paneTitle = css({
  flex: '1',
  minWidth: '0',
  fontSize: '0.68rem',
  letterSpacing: '0.05em',
  color: 'muted',
  overflow: 'hidden',
  textOverflow: 'ellipsis',
  whiteSpace: 'nowrap',
});
const terminalLayer = css({
  display: 'flex',
  flexDirection: 'column',
  flex: '1',
  minWidth: '0',
  minHeight: '0',
});
const empty = css({
  display: 'flex',
  flex: '1',
  flexDirection: 'column',
  alignItems: 'center',
  justifyContent: 'center',
  textAlign: 'center',
  gap: '0.6rem',
  color: 'muted',
  fontSize: '0.78rem',
  paddingInline: '0.6rem',
});
const startRow = css({
  display: 'flex',
  flexWrap: 'nowrap',
  minWidth: '0',
  overflowX: 'auto',
  gap: '0.4rem',
});
// A thin affordance bar for the missing slot (start a chat / open a terminal / attach to a leaf) when the
// other slot already shows a pane, so the split can be completed without leaving the rail.
const slotBar = css({
  display: 'flex',
  flexShrink: 0,
  flexWrap: 'nowrap',
  minWidth: '0',
  overflow: 'hidden',
  alignItems: 'center',
  gap: '0.4rem',
});
const startButton = css({
  display: 'inline-flex',
  alignItems: 'center',
  gap: '0.3rem',
  font: 'inherit',
  fontSize: '0.72rem',
  letterSpacing: '0.04em',
  paddingInline: '0.6rem',
  paddingBlock: '0.22rem',
  borderRadius: '2px',
  borderWidth: '1px',
  borderStyle: 'solid',
  borderColor: 'amber',
  color: 'amber',
  background: 'transparent',
  cursor: 'pointer',
  flex: 'none',
  whiteSpace: 'nowrap',
  _hover: { background: 'rgba(232, 193, 112, 0.1)' },
  _focusVisible: { outline: '1px solid token(colors.amber)', outlineOffset: '1px' },
});
const roleSwitcher = css({
  display: 'flex',
  alignItems: 'center',
  gap: '0.3rem',
  minWidth: '0',
  overflowX: 'auto',
  flexShrink: 0,
});
const roleButton = css({
  font: 'inherit',
  fontSize: '0.64rem',
  color: 'muted',
  background: 'transparent',
  borderWidth: '1px',
  borderStyle: 'solid',
  borderColor: 'grid',
  borderRadius: '2px',
  paddingInline: '0.35rem',
  flex: 'none',
  whiteSpace: 'nowrap',
  cursor: 'pointer',
  "&[data-selected='true']": { color: 'amber', borderColor: 'amber' },
  _focusVisible: { outline: '1px solid token(colors.amber)', outlineOffset: '1px' },
});
const attachError = css({
  fontSize: '0.68rem',
  color: 'alarm',
  paddingInline: '0.2rem',
  flexShrink: 0,
});
const contextNote = css({
  fontSize: '0.68rem',
  color: 'amber',
  paddingInline: '0.2rem',
  flexShrink: 0,
});
const terminateButton = css({
  flexShrink: 0,
  font: 'inherit',
  fontSize: '0.62rem',
  letterSpacing: '0.04em',
  color: 'alarm',
  background: 'transparent',
  borderWidth: '1px',
  borderStyle: 'solid',
  borderColor: 'grid',
  borderRadius: '2px',
  paddingInline: '0.35rem',
  paddingBlock: '0.05rem',
  cursor: 'pointer',
  _hover: { borderColor: 'alarm' },
  _focusVisible: { outline: '1px solid token(colors.amber)', outlineOffset: '1px' },
});

function isRunning(session: OpenSession): boolean {
  return (session.status ?? 'running') === 'running';
}

function stepLines(step: TaskStepNode): string[] {
  const disposition = step.disposition ? ` -- SKIPPED: ${step.disposition.reason}` : '';
  return [
    `- [${step.status}] ${step.id ? `${step.id} -- ` : ''}${step.title}${disposition}`,
    ...step.substeps.map((substep) => {
      const childDisposition = substep.disposition
        ? ` -- SKIPPED: ${substep.disposition.reason}`
        : '';
      return `  - [${substep.status}] ${substep.id ? `${substep.id} -- ` : ''}${substep.title}${childDisposition}`;
    }),
  ];
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
    'Leaf context',
    '',
    `Task: ${doc.id} -- ${doc.title}`,
    `Status: ${doc.status}`,
    `Leaf key: ${leafKey}`,
    `Task document: ${doc.docPath}`,
    ...leafContextOptionalLines(doc, process),
    '',
    'Objective',
    leafContextObjective(doc),
    '',
    'Requirements',
    ...leafContextBullets(doc.requirements),
    '',
    'Top-level steps',
    ...leafContextStepLines(doc.steps),
    '',
    'Instruction',
    'Attach yourself to this lifecycle/leaf before working. Use this task document as the scope anchor.',
  ];
  return lines.filter((line): line is string => line !== null).join('\n');
}

function leafContextOptionalLines(
  doc: TaskDocNode,
  process: EngineProcessNode | undefined,
): Array<string | null> {
  return [
    doc.lifecycleId ? `Lifecycle: ${doc.lifecycleId}` : null,
    process?.worktreeGroup ? `Worktree group: ${process.worktreeGroup}` : null,
    process?.codeWorktree.path ? `Code worktree: ${process.codeWorktree.path}` : null,
    process?.memoryWorktree?.path ? `Memory worktree: ${process.memoryWorktree.path}` : null,
  ];
}

function leafContextObjective(doc: TaskDocNode): string {
  return doc.objective || '(none projected)';
}

function leafContextBullets(items: readonly string[]): string[] {
  return items.length > 0 ? items.map((item) => `- ${item}`) : ['- (none projected)'];
}

function leafContextStepLines(steps: TaskDocNode['steps']): string[] {
  return steps.length > 0 ? steps.flatMap(stepLines) : ['- (none projected)'];
}

function useRailChatSubmissions(
  leafKey: string | undefined,
  taskDocumentRef: TaskDocumentRef | undefined,
  selectedLifecycleId: string | undefined,
  taskDocuments: TaskDocNode[],
  engineProcesses: EngineProcessNode[],
): {
  leafContextNote: string | null;
  sessionOpenError: string | null;
  startChat: (harness: HarnessInfo, role?: string) => void;
  openTerminal: () => void;
  deliverLeafContext: (sessionId: string, lk: string) => Promise<void>;
} {
  const [leafContextNote, setLeafContextNote] = useState<string | null>(null);
  const [sessionOpenError, setSessionOpenError] = useState<string | null>(null);
  const deliverLeafContext = async (sessionId: string, lk: string) => {
    setLeafContextNote(null);
    const packet = buildLeafContextPackage({ leafKey: lk, taskDocuments, engineProcesses });
    if (!packet) return;
    const gate = await waitForSubmissionReady(sessionId);
    if (!gate.ready) {
      setLeafContextNote(gate.reason ?? 'context submit is unavailable');
      return;
    }
    const outcome = await submitSessionText(sessionId, packet, {
      source: 'leaf-context',
      clearDraftOnAccept: false,
    });
    if (outcome.status === 'blocked') {
      setLeafContextNote(outcome.reason);
      return;
    }
    if (outcome.status === 'empty') return;
    const { record } = outcome;
    if (record.phase === 'accepted') setLeafContextNote('leaf context accepted');
    else if (record.phase === 'queued') setLeafContextNote('leaf context queued · yours');
    else {
      setLeafContextNote(record.detail ?? `leaf context submit ${record.phase}`);
    }
  };
  const startChat = (harness: HarnessInfo, role?: string) => {
    void (async () => {
      setSessionOpenError(null);
      const result = await createSession(
        harness.name,
        'harness',
        harness.id,
        selectedLifecycleId,
        taskDocumentRef,
        role,
      );
      if (result.outcome === 'failed') {
        setSessionOpenError(terminalOpenFailureMessage(result));
        return;
      }
      if (leafKey) await deliverLeafContext(result.session.id, leafKey);
    })();
  };
  const openTerminal = () => {
    void (async () => {
      setSessionOpenError(null);
      const result = await createSession(
        'Terminal',
        'terminal',
        undefined,
        selectedLifecycleId,
        taskDocumentRef,
      );
      if (result.outcome === 'failed') {
        setSessionOpenError(terminalOpenFailureMessage(result));
      }
    })();
  };
  return { leafContextNote, sessionOpenError, startChat, openTerminal, deliverLeafContext };
}

function useRailChatAttach(
  taskDocuments: TaskDocNode[],
  onAttached: (sessionId: string, lk: string) => Promise<void>,
): {
  leafAttachError: string | null;
  leafTree: TaskTreeNode[];
  attachChatToLeaf: (sessionId: string, lk: string, seatRole: string) => Promise<void>;
} {
  const [leafAttachError, setLeafAttachError] = useState<string | null>(null);
  const leafTree = buildTaskTree(taskDocuments);
  const attachChatToLeaf = async (sessionId: string, lk: string, seatRole: string) => {
    if (!lk) return;
    const doc = taskDocuments.find((candidate) => qualifiedLeafKey(candidate) === lk);
    const taskDocumentRef = doc ? taskDocumentRefForDoc(doc) : undefined;
    if (!taskDocumentRef) {
      setLeafAttachError('leaf has no canonical task-document reference');
      return;
    }
    const current = sessionStore.getState().sessions.find((session) => session.id === sessionId);
    if (sameTaskDocumentRef(current?.taskDocumentRef, taskDocumentRef)) return;
    setLeafAttachError(null);
    const result = await attachSessionToTask(sessionId, taskDocumentRef, seatRole);
    if (result === 'ok') {
      sessionStore.getState().applyTaskAssignment(sessionId, taskDocumentRef, seatRole);
      notifySessionCatalogChanged('task', sessionId);
      await onAttached(sessionId, lk);
    } else if (result === 'seat-taken') {
      setLeafAttachError(`task document already has a ${seatRole} seat`);
    } else {
      setLeafAttachError('could not attach to leaf');
    }
  };
  return { leafAttachError, leafTree, attachChatToLeaf };
}

interface RailChatProps {
  leafKey?: string;
  taskDocumentRef?: TaskDocumentRef;
  selectedLifecycleId?: string;
  taskDocuments?: TaskDocNode[];
  engineProcesses?: EngineProcessNode[];
  contextMaster?: string;
}

function useDetectedHarnesses(): HarnessInfo[] {
  const [harnesses, setHarnesses] = useState<HarnessInfo[]>([]);
  useEffect(() => {
    let active = true;
    void fetchHarnesses().then((list) => {
      if (active) setHarnesses(list);
    });
    return () => {
      active = false;
    };
  }, []);
  return harnesses.filter((harness) => harness.detected);
}

function sprintRoleStarter(
  detected: HarnessInfo[],
  selectRole: (role: SprintRole) => void,
  startChat: (harness: HarnessInfo, role?: string) => void,
): (role: SprintRole) => void {
  return (role) => {
    const harness = detected[0];
    if (!harness) return;
    selectRole(role);
    startChat(harness, role);
  };
}

async function terminateRailSession(id: string): Promise<void> {
  if (!(await terminateTerminalSession(id))) return;
  sessionStore.getState().setStatus(id, 'terminated');
  sessionStore.getState().close(id);
  notifySessionCatalogChanged('terminate', id);
}

function RailChatImpl({
  leafKey,
  taskDocumentRef,
  selectedLifecycleId,
  taskDocuments = [],
  engineProcesses = [],
  contextMaster,
}: RailChatProps) {
  const [selectedTaskRole, setSelectedTaskRole] = useState<TaskChatRole | undefined>();
  useEffect(
    () => setSelectedTaskRole(undefined),
    [taskDocumentRef?.repository, taskDocumentRef?.path],
  );
  const {
    sessions,
    chatSession,
    terminalSession,
    freeChat,
    mountedSessionIds,
    altitude,
    sprintSeats,
    masterSeats,
    missingSprintRoles,
  } = useRailChatSessions(taskDocumentRef, taskDocuments, selectedTaskRole);
  const { leafContextNote, sessionOpenError, startChat, openTerminal, deliverLeafContext } =
    useRailChatSubmissions(
      leafKey,
      taskDocumentRef,
      selectedLifecycleId,
      taskDocuments,
      engineProcesses,
    );
  const { leafAttachError, leafTree, attachChatToLeaf } = useRailChatAttach(
    taskDocuments,
    (sessionId, lk) => deliverLeafContext(sessionId, lk),
  );
  const detected = useDetectedHarnesses();
  const startSprintRole = sprintRoleStarter(detected, setSelectedTaskRole, startChat);
  return (
    <RailChatBody
      leafKey={leafKey}
      taskDocumentRef={taskDocumentRef}
      altitude={altitude}
      sprintSeats={sprintSeats}
      masterSeats={masterSeats}
      missingSprintRoles={missingSprintRoles}
      selectedTaskRole={
        (chatSession ? sessionSeatRole(chatSession) : selectedTaskRole) as TaskChatRole | undefined
      }
      contextMaster={contextMaster}
      sessions={sessions}
      chatSession={chatSession}
      terminalSession={terminalSession}
      freeChat={freeChat}
      mountedSessionIds={mountedSessionIds}
      detected={detected}
      leafContextNote={leafContextNote}
      sessionOpenError={sessionOpenError}
      leafAttachError={leafAttachError}
      leafTree={leafTree}
      onStartChat={startChat}
      onSelectTaskRole={setSelectedTaskRole}
      onStartSprintRole={startSprintRole}
      onOpenTerminal={openTerminal}
      onTerminate={terminateRailSession}
      onAttach={attachChatToLeaf}
    />
  );
}

interface RailChatBodyProps {
  leafKey: string | undefined;
  taskDocumentRef: TaskDocumentRef | undefined;
  altitude: 'sprint' | 'master' | 'leaf' | undefined;
  sprintSeats: OpenSession[];
  masterSeats: OpenSession[];
  missingSprintRoles: SprintRole[];
  selectedTaskRole: TaskChatRole | undefined;
  contextMaster: string | undefined;
  sessions: OpenSession[];
  chatSession: OpenSession | undefined;
  terminalSession: OpenSession | undefined;
  freeChat: OpenSession | undefined;
  mountedSessionIds: Set<string>;
  detected: HarnessInfo[];
  leafContextNote: string | null;
  sessionOpenError: string | null;
  leafAttachError: string | null;
  leafTree: TaskTreeNode[];
  onStartChat: (harness: HarnessInfo, role?: string) => void;
  onSelectTaskRole: (role: TaskChatRole) => void;
  onStartSprintRole: (role: SprintRole) => void;
  onOpenTerminal: () => void;
  onTerminate: (id: string) => void;
  onAttach: (sessionId: string, lk: string, seatRole: string) => Promise<void>;
}

function RailChatBody({
  leafKey,
  taskDocumentRef,
  altitude,
  sprintSeats,
  masterSeats,
  missingSprintRoles,
  selectedTaskRole,
  contextMaster,
  sessions,
  chatSession,
  terminalSession,
  freeChat,
  mountedSessionIds,
  detected,
  leafContextNote,
  sessionOpenError,
  leafAttachError,
  leafTree,
  onStartChat,
  onSelectTaskRole,
  onStartSprintRole,
  onOpenTerminal,
  onTerminate,
  onAttach,
}: RailChatBodyProps) {
  return (
    <section className={wrap} data-testid="rail-chat">
      <RailChatNotes
        leafKey={leafKey}
        taskDocumentRef={taskDocumentRef}
        leafContextNote={leafContextNote}
        sessionOpenError={sessionOpenError}
      />
      {altitude === 'sprint' || altitude === 'master' ? (
        <TaskRoleControls
          altitude={altitude}
          existing={altitude === 'sprint' ? sprintSeats : masterSeats}
          missing={altitude === 'sprint' ? missingSprintRoles : []}
          selected={selectedTaskRole}
          canCreate={detected.length > 0}
          onSelect={onSelectTaskRole}
          onCreate={onStartSprintRole}
        />
      ) : null}
      <div className={terminalArea}>
        {!chatSession && !terminalSession ? (
          <RailChatEmpty
            taskDocumentRef={taskDocumentRef}
            altitude={altitude}
            detected={detected}
            onStartChat={onStartChat}
            onOpenTerminal={onOpenTerminal}
          />
        ) : (
          <RailChatOccupied
            sessions={sessions}
            mountedSessionIds={mountedSessionIds}
            chatSession={chatSession}
            terminalSession={terminalSession}
            freeChat={freeChat}
            detected={detected}
            leafTree={leafTree}
            contextMaster={contextMaster}
            leafAttachError={leafAttachError}
            onStartChat={onStartChat}
            onOpenTerminal={taskDocumentRef ? undefined : onOpenTerminal}
            onTerminate={onTerminate}
            onAttach={onAttach}
          />
        )}
      </div>
    </section>
  );
}

function RailChatNotes({
  leafKey,
  taskDocumentRef,
  leafContextNote,
  sessionOpenError,
}: {
  leafKey: string | undefined;
  taskDocumentRef: TaskDocumentRef | undefined;
  leafContextNote: string | null;
  sessionOpenError: string | null;
}) {
  return (
    <>
      <header className={heading} data-testid="rail-chat-heading">
        {leafKey
          ? `Chat · ${leafIdFromKey(leafKey)}`
          : taskDocumentRef
            ? `Chat · ${taskDocumentRef.path.split('/').at(-2) ?? taskDocumentRef.path}`
            : 'Chat'}
      </header>
      {leafContextNote ? (
        <span className={contextNote} data-testid="rail-leaf-context-note" role="status">
          {leafContextNote}
        </span>
      ) : null}
      {sessionOpenError ? (
        <span className={attachError} data-testid="rail-session-open-error" role="alert">
          {sessionOpenError}
        </span>
      ) : null}
    </>
  );
}

function TaskRoleControls({
  altitude,
  existing,
  missing,
  selected,
  canCreate,
  onSelect,
  onCreate,
}: {
  altitude: 'sprint' | 'master';
  existing: OpenSession[];
  missing: SprintRole[];
  selected: TaskChatRole | undefined;
  canCreate: boolean;
  onSelect: (role: TaskChatRole) => void;
  onCreate: (role: SprintRole) => void;
}) {
  return (
    <div className={roleSwitcher} data-testid={`rail-${altitude}-role-controls`}>
      {existing.map((session) => {
        const role = sessionSeatRole(session) as TaskChatRole;
        return (
          <button
            key={role}
            type="button"
            className={roleButton}
            data-selected={selected === role ? 'true' : undefined}
            title={`${ROLE_LABELS[role]} · ${session.label}`}
            onClick={() => onSelect(role)}
          >
            {ROLE_LABELS[role]}
          </button>
        );
      })}
      {missing.map((role) => (
        <button
          key={role}
          type="button"
          className={roleButton}
          disabled={!canCreate}
          title={canCreate ? `Create ${ROLE_LABELS[role]} chat` : 'No agent harness detected'}
          onClick={() => onCreate(role)}
          data-testid={`rail-create-sprint-role-${role}`}
        >
          + {ROLE_LABELS[role]}
        </button>
      ))}
    </div>
  );
}

function RailChatOccupied({
  sessions,
  mountedSessionIds,
  chatSession,
  terminalSession,
  freeChat,
  detected,
  leafTree,
  contextMaster,
  leafAttachError,
  onStartChat,
  onOpenTerminal,
  onTerminate,
  onAttach,
}: {
  sessions: OpenSession[];
  mountedSessionIds: Set<string>;
  chatSession: OpenSession | undefined;
  terminalSession: OpenSession | undefined;
  freeChat: OpenSession | undefined;
  detected: HarnessInfo[];
  leafTree: TaskTreeNode[];
  contextMaster: string | undefined;
  leafAttachError: string | null;
  onStartChat: (harness: HarnessInfo, role?: string) => void;
  onOpenTerminal: (() => void) | undefined;
  onTerminate: (id: string) => void;
  onAttach: (sessionId: string, lk: string, seatRole: string) => Promise<void>;
}) {
  return (
    <>
      {freeChat && chatSession && leafTree.length > 0 ? (
        <AttachRow
          chatSession={chatSession}
          freeChat={freeChat}
          leafTree={leafTree}
          contextMaster={contextMaster}
          leafAttachError={leafAttachError}
          onAttach={onAttach}
        />
      ) : null}
      <RailChatSlots
        chatSession={chatSession}
        terminalSession={terminalSession}
        detected={detected}
        onStartChat={onStartChat}
        onOpenTerminal={onOpenTerminal}
        onTerminate={onTerminate}
      />
      <KeepAlivePanes
        sessions={sessions}
        mountedSessionIds={mountedSessionIds}
        chatSession={chatSession}
        terminalSession={terminalSession}
      />
    </>
  );
}

function RailChatEmpty({
  taskDocumentRef,
  altitude,
  detected,
  onStartChat,
  onOpenTerminal,
}: {
  taskDocumentRef: TaskDocumentRef | undefined;
  altitude: 'sprint' | 'master' | 'leaf' | undefined;
  detected: HarnessInfo[];
  onStartChat: (harness: HarnessInfo, role?: string) => void;
  onOpenTerminal: () => void;
}) {
  return (
    <div className={empty} data-testid="rail-chat-empty">
      <span>
        {altitude === 'sprint'
          ? 'No sprint role chat exists yet.'
          : altitude === 'master'
            ? 'No manager chat occupies this master yet.'
            : altitude === 'leaf'
              ? 'No worker, reviewer, or curator chat occupies this leaf yet.'
              : 'Start a chat anywhere — attach it to a task any time.'}
      </span>
      {!taskDocumentRef ? (
        <>
          <StartChatAffordance detected={detected} onStartChat={onStartChat} />
          <TerminalAffordance onOpen={onOpenTerminal} />
        </>
      ) : null}
    </div>
  );
}

function RailChatSlots({
  chatSession,
  terminalSession,
  detected,
  onStartChat,
  onOpenTerminal,
  onTerminate,
}: {
  chatSession: OpenSession | undefined;
  terminalSession: OpenSession | undefined;
  detected: HarnessInfo[];
  onStartChat: (harness: HarnessInfo, role?: string) => void;
  onOpenTerminal: (() => void) | undefined;
  onTerminate: (id: string) => void;
}) {
  return (
    <>
      {chatSession ? (
        <Pane paneRole="chat" session={chatSession} onTerminate={onTerminate} />
      ) : (
        <div className={slotBar}>
          <StartChatAffordance detected={detected} onStartChat={onStartChat} />
        </div>
      )}
      {terminalSession ? (
        <Pane paneRole="terminal" session={terminalSession} onTerminate={onTerminate} />
      ) : onOpenTerminal ? (
        <div className={slotBar}>
          <TerminalAffordance onOpen={onOpenTerminal} />
        </div>
      ) : null}
    </>
  );
}

function StartChatAffordance({
  detected,
  onStartChat,
}: {
  detected: HarnessInfo[];
  onStartChat: (harness: HarnessInfo, role?: string) => void;
}) {
  if (detected.length === 0) {
    return <span data-testid="rail-no-harness">No agent detected on PATH.</span>;
  }
  return (
    <div className={startRow} data-testid="rail-start-chat">
      {detected.map((harness) => (
        <button
          key={harness.id}
          type="button"
          className={startButton}
          onClick={() => onStartChat(harness)}
          data-testid={`rail-start-chat-${harness.id}`}
        >
          ＋ {harness.name}
        </button>
      ))}
    </div>
  );
}

function TerminalAffordance({ onOpen }: { onOpen: () => void }) {
  return (
    <button type="button" className={startButton} onClick={onOpen} data-testid="rail-open-terminal">
      ＋ Terminal
    </button>
  );
}

function AttachRow({
  chatSession,
  freeChat,
  leafTree,
  contextMaster,
  leafAttachError,
  onAttach,
}: {
  chatSession: OpenSession;
  freeChat: OpenSession | undefined;
  leafTree: TaskTreeNode[];
  contextMaster: string | undefined;
  leafAttachError: string | null;
  onAttach: (sessionId: string, lk: string, seatRole: string) => Promise<void>;
}) {
  return (
    <div className={slotBar} data-testid="rail-attach-row">
      {freeChat ? <span>Free chat —</span> : null}
      <LeafAttachPicker
        tree={leafTree}
        contextMaster={contextMaster}
        onPick={(lk, seatRole) => void onAttach(chatSession.id, lk, seatRole)}
        testId="rail-attach-leaf-picker"
        label={chatSession.taskDocumentRef ? 'Move task' : 'Attach to task'}
        align="right"
        seatRole={attachSeatRole(chatSession)}
      />
      {leafAttachError ? (
        <span className={attachError} data-testid="rail-leaf-attach-error">
          {leafAttachError}
        </span>
      ) : null}
    </div>
  );
}

function KeepAlivePanes({
  sessions,
  mountedSessionIds,
  chatSession,
  terminalSession,
}: {
  sessions: OpenSession[];
  mountedSessionIds: Set<string>;
  chatSession: OpenSession | undefined;
  terminalSession: OpenSession | undefined;
}) {
  const visibleIds = new Set(
    [chatSession?.id, terminalSession?.id].filter((id): id is string => Boolean(id)),
  );
  const keepAlive = sessions.filter(
    (session) =>
      mountedSessionIds.has(session.id) && !visibleIds.has(session.id) && isRunning(session),
  );
  return (
    <>
      {keepAlive.map((session) => (
        <div
          key={session.id}
          style={{ display: 'none' }}
          aria-hidden
          data-testid={`rail-chat-keepalive-${session.id}`}
        >
          <Suspense fallback={null}>
            <Terminal
              sessionId={session.id}
              ariaLabel={`terminal: ${session.label}`}
              onConnection={(conn) => registerConnection(session.id, conn)}
            />
          </Suspense>
        </div>
      ))}
    </>
  );
}

// Memoized (tab-switch CPU): a persistent rail panel — the shell re-renders on every view
// switch with unchanged props, and the memo gate skips this subtree then; the chat's own store
// subscriptions still drive its updates.
export const RailChat = memo(RailChatImpl);

// One pane (chat or terminal): a truncating header with a hover-revealed full name and a
// terminate control, the live terminal, and its composer.
function Pane({
  paneRole,
  session,
  onTerminate,
}: {
  paneRole: SessionRole;
  session: OpenSession;
  onTerminate: (id: string) => void;
}) {
  const seatRole = sessionSeatRole(session);
  const contextRole = seatRole === 'reviewer' ? reviewerContextLabel(session) : seatRole;
  return (
    <div className={pane} data-testid={`rail-pane-${paneRole}`}>
      <div className={paneHeader}>
        <span className={paneTitle} title={`${contextRole} · ${session.label}`}>
          {contextRole} · {session.label}
        </span>
        <button
          type="button"
          className={terminateButton}
          onClick={() => onTerminate(session.id)}
          aria-label={`Terminate ${session.label}`}
          data-testid={`rail-terminate-${paneRole}`}
        >
          End
        </button>
      </div>
      <div className={terminalLayer}>
        <Suspense fallback={<div className={empty}>Opening {paneRole}…</div>}>
          <Terminal
            sessionId={session.id}
            ariaLabel={`terminal: ${session.label}`}
            onConnection={(conn) => registerConnection(session.id, conn)}
          />
        </Suspense>
      </div>
      <SessionComposer session={session} />
    </div>
  );
}

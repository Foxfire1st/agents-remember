import {
  memo,
  useEffect,
  useRef,
  useState,
  type MutableRefObject,
  type RefObject,
  type ReactNode,
} from 'react';
import {
  Button,
  Dialog,
  Popover,
  TextArea,
  TextField,
  ToggleButton,
  ToggleButtonGroup,
} from 'react-aria-components';

import { css, cx } from '../../styled-system/css';
import {
  useSelectionCapture,
  type SelectionContext as HighlightSelection,
} from '../data/selection';
import {
  createSession,
  findSessionForTask,
  sessionStore,
  terminalOpenFailureMessage,
  useSessions,
  type OpenSession,
} from '../data/sessions';
import { useDashboard } from '../data/store';
import { qualifiedLeafKey, taskDocumentRefForDoc } from '../data/taskIdentity';
import type { TaskDocNode } from '../types/projection';
import {
  keepWaitingForSubmit,
  releaseSubmitDraft,
  retryRouteFailure,
  submitSessionText,
  waitForSubmissionReady,
} from '../data/submitClient';
import type { SubmitRecord } from '../data/submitMachine';
import { fetchHarnesses, type HarnessInfo } from '../data/terminal';

// Selection-to-chat uses the SAME reliable native-control submit path as every composer. A
// deliberate pill/Send click is the action boundary; nothing is sent on selection alone. Targets
// are native-control harness sessions (or detected harnesses that can create one), never a plain
// terminal. Images/attachments remain UA-10 and are stated as unavailable instead of being
// smuggled through PTY paste or filesystem-path conventions.

const popover = css({ maxWidth: 'min(32rem, 94vw)' });
const dialog = css({
  display: 'flex',
  flexDirection: 'column',
  gap: '0.45rem',
  padding: '0.55rem',
  background: 'bgPanel',
  borderWidth: '1px',
  borderStyle: 'solid',
  borderColor: 'amber',
  borderRadius: '4px',
  boxShadow: '0 6px 24px rgba(0,0,0,0.5)',
  outline: 'none',
});
const dialogPill = css({
  padding: '0.2rem',
  borderRadius: '999px',
  borderColor: 'grid',
});
const dialogComposer = css({ width: 'min(27rem, 92vw)' });
const addButton = css({
  display: 'inline-flex',
  alignItems: 'center',
  gap: '0.4rem',
  font: 'inherit',
  fontSize: '0.74rem',
  letterSpacing: '0.02em',
  paddingInline: '0.7rem',
  paddingBlock: '0.32rem',
  borderRadius: '999px',
  border: 'none',
  background: 'transparent',
  color: 'text',
  cursor: 'pointer',
  _hover: { color: 'amber' },
  _active: { transform: 'scale(0.97)' },
  _focusVisible: {
    outline: '1px solid token(colors.amber)',
    outlineOffset: '1px',
  },
  _disabled: { opacity: 0.55, cursor: 'default', transform: 'none' },
});
const chatIcon = css({ flexShrink: 0, display: 'block' });
const preview = css({
  margin: '0',
  maxHeight: '6.5rem',
  overflowY: 'auto',
  whiteSpace: 'pre-wrap',
  overflowWrap: 'anywhere',
  font: 'inherit',
  fontSize: '0.72rem',
  color: 'muted',
  paddingInline: '0.4rem',
  paddingBlock: '0.3rem',
  background: 'bg',
  borderLeftWidth: '2px',
  borderLeftStyle: 'solid',
  borderLeftColor: 'amber',
});
const targetRow = css({
  display: 'flex',
  alignItems: 'baseline',
  gap: '0.4rem',
  flexWrap: 'wrap',
});
const targetLabel = css({
  fontSize: '0.68rem',
  letterSpacing: '0.04em',
  color: 'muted',
  flexShrink: 0,
});
const toggleGroup = css({ display: 'flex', gap: '0.25rem', flexWrap: 'wrap' });
const toggle = css({
  font: 'inherit',
  fontSize: '0.7rem',
  paddingInline: '0.45rem',
  paddingBlock: '0.15rem',
  borderRadius: '2px',
  borderWidth: '1px',
  borderStyle: 'solid',
  borderColor: 'grid',
  color: 'muted',
  background: 'transparent',
  cursor: 'pointer',
  _selected: { borderColor: 'amber', color: 'amber' },
  _focusVisible: {
    outline: '1px solid token(colors.amber)',
    outlineOffset: '1px',
  },
});
const field = css({ display: 'flex' });
const area = css({
  font: 'inherit',
  fontSize: '0.82rem',
  lineHeight: '1.4',
  color: 'inherit',
  width: '100%',
  resize: 'vertical',
  minHeight: '5rem',
  maxHeight: '20rem',
  paddingInline: '0.55rem',
  paddingBlock: '0.45rem',
  background: 'bg',
  borderWidth: '1px',
  borderStyle: 'solid',
  borderColor: 'grid',
  borderRadius: '2px',
  _focusVisible: {
    outline: '1px solid token(colors.amber)',
    outlineOffset: '1px',
  },
});
const sendButton = css({
  font: 'inherit',
  fontSize: '0.74rem',
  letterSpacing: '0.04em',
  alignSelf: 'flex-end',
  paddingInline: '0.9rem',
  paddingBlock: '0.32rem',
  borderRadius: '2px',
  borderWidth: '1px',
  borderStyle: 'solid',
  borderColor: 'amber',
  color: 'amber',
  background: 'transparent',
  cursor: 'pointer',
  _hover: { background: 'rgba(232, 193, 112, 0.1)' },
  _active: { transform: 'scale(0.97)' },
  _focusVisible: {
    outline: '1px solid token(colors.amber)',
    outlineOffset: '1px',
  },
  _disabled: { opacity: 0.6, cursor: 'default', transform: 'none' },
});
const statusNote = css({
  fontSize: '0.68rem',
  color: 'amber',
  alignSelf: 'flex-end',
  overflowWrap: 'anywhere',
});
const statusActions = css({
  display: 'flex',
  justifyContent: 'flex-end',
  gap: '0.3rem',
  flexWrap: 'wrap',
});
const secondaryButton = css({
  font: 'inherit',
  fontSize: '0.66rem',
  color: 'muted',
  borderWidth: '1px',
  borderStyle: 'solid',
  borderColor: 'grid',
  borderRadius: '2px',
  background: 'transparent',
  cursor: 'pointer',
  paddingInline: '0.4rem',
  _hover: { color: 'amber', borderColor: 'amber' },
});
const scopeNote = css({ fontSize: '0.66rem', color: 'muted' });

function ChatIcon() {
  return (
    <svg viewBox="0 0 16 16" width="13" height="13" aria-hidden="true" className={chatIcon}>
      <path
        d="M2 3.2h12v7.2H6.4L3.6 13V10.4H2z"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.3"
        strokeLinejoin="round"
      />
    </svg>
  );
}

type Target =
  | { key: string; kind: 'session'; id: string; label: string }
  | {
      key: string;
      kind: 'create';
      harnessId: string;
      prefix: string;
      label: string;
    };

type HighlightStatus =
  | { phase: 'sending'; detail: string }
  | { phase: 'error'; detail: string }
  | { phase: 'endgame'; detail: string; requestId: string }
  | null;

function buildContextPackage(selectionText: string, note?: string): string {
  const parts = note && note.length > 0 ? [note, ''] : [];
  parts.push('--- from the dashboard ---', selectionText);
  return parts.join('\n');
}

function successful(record: SubmitRecord): boolean {
  return record.phase === 'accepted' || record.phase === 'queued';
}

function useHighlightComposerState(): {
  selection: HighlightSelection | null;
  sessions: OpenSession[];
  activeId: string | null;
  harnesses: HarnessInfo[];
  mode: 'pill' | 'composer';
  message: string;
  targetKey: string | null;
  status: HighlightStatus;
  anchorRef: RefObject<HTMLSpanElement | null>;
  sendingRef: MutableRefObject<boolean>;
  deliveryRef: MutableRefObject<{ id: string } | null>;
  lastRecordRef: MutableRefObject<SubmitRecord | null>;
  messageInputRef: RefObject<HTMLTextAreaElement | null>;
  clear: () => void;
  setMode: (mode: 'pill' | 'composer') => void;
  setMessage: (message: string) => void;
  setTargetKey: (key: string | null) => void;
  setStatus: (status: HighlightStatus) => void;
} {
  const { selection, clear } = useSelectionCapture();
  const sessions = useSessions((state) => state.sessions);
  const activeId = useSessions((state) => state.activeId);
  const [harnesses, setHarnesses] = useState<HarnessInfo[]>([]);
  const [mode, setMode] = useState<'pill' | 'composer'>('pill');
  const [message, setMessage] = useState('');
  const [targetKey, setTargetKey] = useState<string | null>(null);
  const [status, setStatus] = useState<HighlightStatus>(null);
  const anchorRef = useRef<HTMLSpanElement>(null);
  const sendingRef = useRef(false);
  const deliveryRef = useRef<{ id: string } | null>(null);
  const lastRecordRef = useRef<SubmitRecord | null>(null);
  const messageInputRef = useRef<HTMLTextAreaElement | null>(null);

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
    setMode('pill');
    setMessage('');
    setStatus(null);
    sendingRef.current = false;
    deliveryRef.current = null;
    lastRecordRef.current = null;
  }, [selection]);

  useEffect(() => {
    if (mode === 'composer') messageInputRef.current?.focus();
  }, [mode]);

  return {
    selection,
    sessions,
    activeId,
    harnesses,
    mode,
    message,
    targetKey,
    status,
    anchorRef,
    sendingRef,
    deliveryRef,
    lastRecordRef,
    messageInputRef,
    clear,
    setMode,
    setMessage,
    setTargetKey,
    setStatus,
  };
}

function useHighlightTargets({
  selection,
  sessions,
  activeId,
  harnesses,
  targetKey,
  selectedLifecycleId,
  leafChatActive,
  viewedLeafKey,
  taskDocuments,
}: {
  selection: HighlightSelection | null;
  sessions: OpenSession[];
  activeId: string | null;
  harnesses: HarnessInfo[];
  targetKey: string | null;
  selectedLifecycleId: string | undefined;
  leafChatActive: boolean;
  viewedLeafKey: string | undefined;
  taskDocuments: TaskDocNode[];
}): {
  directLeafChat: OpenSession | undefined;
  targets: Target[];
  selectedKey: string | null;
  selected: Target | null;
} {
  if (!selection) {
    return { directLeafChat: undefined, targets: [], selectedKey: null, selected: null };
  }
  const directLeafChat = directLeafChatFor(selection, leafChatActive, viewedLeafKey, taskDocuments);
  const routedSessions = routedSessionsFor(sessions, selectedLifecycleId);
  const targets: Target[] = [...sessionTargets(routedSessions), ...createTargets(harnesses)];
  const defaultKey = highlightDefaultKey(activeId, routedSessions, targets);
  const selectedKey = targets.find((target) => target.key === targetKey) ? targetKey : defaultKey;
  const selected = targets.find((target) => target.key === selectedKey) ?? null;
  return { directLeafChat, targets, selectedKey, selected };
}

function directLeafChatFor(
  selection: HighlightSelection | null,
  leafChatActive: boolean,
  viewedLeafKey: string | undefined,
  taskDocuments: TaskDocNode[],
): OpenSession | undefined {
  if (!selection || !leafChatActive || !viewedLeafKey || selection.leafKey !== viewedLeafKey)
    return undefined;
  const doc = taskDocuments.find((candidate) => qualifiedLeafKey(candidate) === viewedLeafKey);
  if (!doc) return undefined;
  const taskDocumentRef = taskDocumentRefForDoc(doc);
  if (!taskDocumentRef) return undefined;
  return runningHarnessSession(findSessionForTask(taskDocumentRef, 'chat'));
}

function runningHarnessSession(session: OpenSession | undefined): OpenSession | undefined {
  if (session?.kind !== 'harness') return undefined;
  return (session.status ?? 'running') === 'running' ? session : undefined;
}

function routedSessionsFor(
  sessions: OpenSession[],
  selectedLifecycleId: string | undefined,
): OpenSession[] {
  return (
    selectedLifecycleId
      ? sessions.filter((session) => session.lifecycleId === selectedLifecycleId)
      : sessions
  ).filter((session) => session.kind === 'harness' && (session.status ?? 'running') === 'running');
}

function sessionTargets(sessions: OpenSession[]): Target[] {
  return sessions.map((session): Target => ({
    key: `s:${session.id}`,
    kind: 'session',
    id: session.id,
    label: session.label,
  }));
}

function createTargets(harnesses: HarnessInfo[]): Target[] {
  return harnesses
    .filter((harness) => harness.detected)
    .map((harness): Target => ({
      key: `c:${harness.id}`,
      kind: 'create',
      harnessId: harness.id,
      prefix: harness.name,
      label: `＋ ${harness.name}`,
    }));
}

function highlightDefaultKey(
  activeId: string | null,
  routedSessions: OpenSession[],
  targets: Target[],
): string | null {
  return (
    (activeId && routedSessions.some((session) => session.id === activeId) && `s:${activeId}`) ||
    (routedSessions[0] && `s:${routedSessions[0].id}`) ||
    targets.find((target) => target.kind === 'create')?.key ||
    null
  );
}

function useHighlightSettle({
  onSent,
  deliveryRef,
  lastRecordRef,
  sendingRef,
  setStatus,
  setMode,
  clear,
}: {
  onSent?: (sessionId: string) => void;
  deliveryRef: MutableRefObject<{ id: string } | null>;
  lastRecordRef: MutableRefObject<SubmitRecord | null>;
  sendingRef: MutableRefObject<boolean>;
  setStatus: (status: HighlightStatus) => void;
  setMode: (mode: 'pill' | 'composer') => void;
  clear: () => void;
}): {
  dismiss: () => void;
  showRecord: (record: SubmitRecord, notice?: string) => boolean;
} {
  const dismiss = () => {
    clear();
    setMode('pill');
  };
  const finish = () => {
    const sessionId = deliveryRef.current?.id;
    deliveryRef.current = null;
    lastRecordRef.current = null;
    sendingRef.current = false;
    dismiss();
    if (sessionId) {
      sessionStore.getState().setActive(sessionId);
      onSent?.(sessionId);
    }
  };
  const showRecord = (record: SubmitRecord, notice?: string): boolean => {
    lastRecordRef.current = record;
    if (successful(record)) {
      if (notice) {
        setStatus({
          phase: 'error',
          detail: `${notice} · the original message was accepted`,
        });
        return true;
      }
      finish();
      return true;
    }
    if (record.phase === 'endgame') {
      setStatus({
        phase: 'endgame',
        requestId: record.requestId,
        detail: notice ?? record.detail ?? 'still unresolved',
      });
    } else {
      setStatus({
        phase: 'error',
        detail: notice
          ? `${notice} · ${record.detail ?? record.phase}`
          : (record.detail ?? `submit ${record.phase}`),
      });
    }
    return false;
  };
  return { dismiss, showRecord };
}

function submitHighlightPayload(
  id: string,
  payload: string,
  showRecord: (record: SubmitRecord, notice?: string) => boolean,
  setStatus: (status: HighlightStatus) => void,
  lastRecordRef: MutableRefObject<SubmitRecord | null>,
): Promise<boolean> {
  const prior = lastRecordRef.current;
  if (prior?.phase === 'route-error' && prior.requestId) {
    return retryRouteFailure(id, prior.requestId, payload).then((retried) => {
      if (retried.record) return showRecord(retried.record, retried.notice);
      setStatus({
        phase: 'error',
        detail: 'the same-id retry is no longer available',
      });
      return false;
    });
  }
  return submitSessionText(id, payload, {
    source: 'highlight',
    clearDraftOnAccept: false,
  }).then((outcome) => {
    if (outcome.status === 'blocked') {
      setStatus({ phase: 'error', detail: outcome.reason });
      return false;
    }
    if (outcome.status === 'empty') {
      setStatus({ phase: 'error', detail: 'the context package is empty' });
      return false;
    }
    return showRecord(outcome.record);
  });
}

function useHighlightReconcile({
  lastRecordRef,
  deliveryRef,
  sendingRef,
  setStatus,
  showRecord,
}: {
  lastRecordRef: MutableRefObject<SubmitRecord | null>;
  deliveryRef: MutableRefObject<{ id: string } | null>;
  sendingRef: MutableRefObject<boolean>;
  setStatus: (status: HighlightStatus) => void;
  showRecord: (record: SubmitRecord, notice?: string) => boolean;
}): () => Promise<void> {
  return async () => {
    const record = lastRecordRef.current;
    const context = deliveryRef.current;
    if (!record || record.phase !== 'endgame' || !context || sendingRef.current) return;
    sendingRef.current = true;
    setStatus({ phase: 'sending', detail: 'Reconciling the same requestId…' });
    try {
      const final = await keepWaitingForSubmit(context.id, record.requestId);
      if (final) showRecord(final);
      else setStatus({ phase: 'error', detail: 'this request is no longer waiting' });
    } finally {
      sendingRef.current = false;
    }
  };
}

interface HighlightSubmitProps {
  selection: HighlightSelection | null;
  message: string;
  selected: Target | null;
  selectedLifecycleId: string | undefined;
  deliveryRef: MutableRefObject<{ id: string } | null>;
  lastRecordRef: MutableRefObject<SubmitRecord | null>;
  sendingRef: MutableRefObject<boolean>;
  status: HighlightStatus;
  setStatus: (status: HighlightStatus) => void;
  setMode: (mode: 'pill' | 'composer') => void;
  showRecord: (record: SubmitRecord, notice?: string) => boolean;
}

function useHighlightSubmit(props: HighlightSubmitProps): {
  directSubmit: (targetId: string) => void;
  send: () => Promise<void>;
  keepWaiting: () => Promise<void>;
} {
  const {
    selection,
    message,
    selected,
    selectedLifecycleId,
    deliveryRef,
    lastRecordRef,
    sendingRef,
    status,
    setStatus,
    setMode,
    showRecord,
  } = props;
  const submitTo = (id: string, payload: string) =>
    submitHighlightPayload(id, payload, showRecord, setStatus, lastRecordRef);
  const directSubmit = (targetId: string) => {
    if (!selection || sendingRef.current) return;
    sendingRef.current = true;
    deliveryRef.current = { id: targetId };
    setStatus({ phase: 'sending', detail: 'Sending…' });
    void submitTo(targetId, buildContextPackage(selection?.text ?? '')).then((sent) => {
      sendingRef.current = false;
      if (!sent) setMode('composer');
    });
  };
  const send = async () => {
    if (!selection) return;
    if ((!selected && !deliveryRef.current) || sendingRef.current || status?.phase === 'endgame') {
      return;
    }
    sendingRef.current = true;
    setStatus({ phase: 'sending', detail: 'Sending…' });
    try {
      let context = deliveryRef.current;
      if (!context) {
        if (!selected) return;
        const id = await openHighlightSession(
          selected,
          selectedLifecycleId,
          setStatus,
          deliveryRef,
        );
        if (id === null) return;
        context = { id };
        deliveryRef.current = context;
      }
      await submitTo(context.id, buildContextPackage(selection.text, message));
    } finally {
      sendingRef.current = false;
    }
  };
  const keepWaiting = useHighlightReconcile({
    lastRecordRef,
    deliveryRef,
    sendingRef,
    setStatus,
    showRecord,
  });
  return { directSubmit, send, keepWaiting };
}

function useHighlightFormHandlers({
  status,
  setStatus,
  setTargetKey,
  deliveryRef,
  lastRecordRef,
}: {
  status: HighlightStatus;
  setStatus: (status: HighlightStatus) => void;
  setTargetKey: (key: string) => void;
  deliveryRef: MutableRefObject<{ id: string } | null>;
  lastRecordRef: MutableRefObject<SubmitRecord | null>;
}) {
  const selectTarget = (key: string) => {
    setTargetKey(key);
    deliveryRef.current = null;
    lastRecordRef.current = null;
    setStatus(null);
  };
  const copyFailed = (detail: string) => {
    if (status?.phase === 'endgame') setStatus({ ...status, detail });
  };
  const release = () => {
    const context = deliveryRef.current;
    if (!context) return;
    releaseSubmitDraft(context.id, status?.phase === 'endgame' ? status.requestId : '');
    lastRecordRef.current = null;
    setStatus({
      phase: 'error',
      detail: 'unresolved request released; a new Send will use a new requestId',
    });
  };
  return { selectTarget, copyFailed, release };
}

async function openHighlightSession(
  selected: Target,
  selectedLifecycleId: string | undefined,
  setStatus: (status: HighlightStatus) => void,
  deliveryRef: MutableRefObject<{ id: string } | null>,
): Promise<string | null> {
  if (selected.kind === 'session') return selected.id;
  const result = selectedLifecycleId
    ? await createSession(selected.prefix, 'harness', selected.harnessId, selectedLifecycleId)
    : await createSession(selected.prefix, 'harness', selected.harnessId);
  if (result.outcome === 'failed') {
    setStatus({
      phase: 'error',
      detail: terminalOpenFailureMessage(result),
    });
    return null;
  }
  const id = result.session.id;
  const gate = await waitForSubmissionReady(id);
  if (!gate.ready) {
    setStatus({
      phase: 'error',
      detail: gate.reason ?? 'native control did not become ready',
    });
    deliveryRef.current = { id };
    return null;
  }
  return id;
}

function HighlightComposerImpl({
  selectedLifecycleId,
  viewedLeafKey,
  leafChatActive = false,
  onSent,
}: {
  selectedLifecycleId?: string;
  viewedLeafKey?: string;
  leafChatActive?: boolean;
  onSent?: (sessionId: string) => void;
}) {
  const state = useHighlightComposerState();
  const taskDocuments = useDashboard((dashboard) => dashboard.analytics?.taskDocuments ?? []);
  const { directLeafChat, targets, selectedKey, selected } = useHighlightTargets({
    selection: state.selection,
    sessions: state.sessions,
    activeId: state.activeId,
    harnesses: state.harnesses,
    targetKey: state.targetKey,
    selectedLifecycleId,
    leafChatActive,
    viewedLeafKey,
    taskDocuments,
  });
  const settle = useHighlightSettle({
    onSent,
    deliveryRef: state.deliveryRef,
    lastRecordRef: state.lastRecordRef,
    sendingRef: state.sendingRef,
    setStatus: state.setStatus,
    setMode: state.setMode,
    clear: state.clear,
  });
  const actions = useHighlightSubmit({
    selection: state.selection,
    message: state.message,
    selected,
    selectedLifecycleId,
    deliveryRef: state.deliveryRef,
    lastRecordRef: state.lastRecordRef,
    sendingRef: state.sendingRef,
    status: state.status,
    setStatus: state.setStatus,
    setMode: state.setMode,
    showRecord: settle.showRecord,
  });
  const formHandlers = useHighlightFormHandlers({
    status: state.status,
    setStatus: state.setStatus,
    setTargetKey: state.setTargetKey,
    deliveryRef: state.deliveryRef,
    lastRecordRef: state.lastRecordRef,
  });

  if (!state.selection) return null;
  const selection = state.selection;

  return (
    <HighlightComposerView
      state={state}
      selection={selection}
      directLeafChat={directLeafChat}
      targets={targets}
      selectedKey={selectedKey}
      selected={selected}
      settle={settle}
      actions={actions}
      formHandlers={formHandlers}
    />
  );
}

function HighlightComposerView({
  state,
  selection,
  directLeafChat,
  targets,
  selectedKey,
  selected,
  settle,
  actions,
  formHandlers,
}: {
  state: ReturnType<typeof useHighlightComposerState>;
  selection: HighlightSelection;
  directLeafChat: OpenSession | undefined;
  targets: Target[];
  selectedKey: string | null;
  selected: Target | null;
  settle: ReturnType<typeof useHighlightSettle>;
  actions: ReturnType<typeof useHighlightSubmit>;
  formHandlers: ReturnType<typeof useHighlightFormHandlers>;
}) {
  const { mode, status, message, setMessage, messageInputRef } = state;

  return (
    <>
      <span
        ref={state.anchorRef}
        aria-hidden="true"
        style={{
          position: 'fixed',
          left: selection.rect.left,
          top: selection.rect.top,
          width: selection.rect.width,
          height: selection.rect.height,
          pointerEvents: 'none',
        }}
      />
      <HighlightComposerSurface
        anchorRef={state.anchorRef}
        mode={mode}
        onDismiss={settle.dismiss}
        pill={
          <HighlightPill
            status={status}
            directLeafChat={directLeafChat}
            onDirectSubmit={actions.directSubmit}
            onCompose={() => state.setMode('composer')}
          />
        }
        form={
          <HighlightForm
            selection={selection}
            message={message}
            setMessage={setMessage}
            targets={targets}
            selectedKey={selectedKey}
            status={status}
            messageInputRef={messageInputRef}
            deliveryRef={state.deliveryRef}
            lastRecordRef={state.lastRecordRef}
            selected={selected}
            onSelectTarget={formHandlers.selectTarget}
            onCopyFailed={formHandlers.copyFailed}
            onSend={() => void actions.send()}
            onKeepWaiting={() => void actions.keepWaiting()}
            onRelease={formHandlers.release}
          />
        }
      />
    </>
  );
}

function HighlightPill({
  status,
  directLeafChat,
  onDirectSubmit,
  onCompose,
}: {
  status: HighlightStatus;
  directLeafChat: OpenSession | undefined;
  onDirectSubmit: (targetId: string) => void;
  onCompose: () => void;
}) {
  return (
    <Button
      className={addButton}
      isDisabled={status?.phase === 'sending'}
      onPress={() => (directLeafChat ? onDirectSubmit(directLeafChat.id) : onCompose())}
      data-testid="highlight-add-to-chat"
    >
      <ChatIcon />
      Add to chat
    </Button>
  );
}

function HighlightComposerSurface({
  anchorRef,
  mode,
  onDismiss,
  pill,
  form,
}: {
  anchorRef: RefObject<HTMLSpanElement | null>;
  mode: 'pill' | 'composer';
  onDismiss: () => void;
  pill: ReactNode;
  form: ReactNode;
}) {
  return (
    <Popover
      triggerRef={anchorRef}
      isOpen
      onOpenChange={(isOpen) => {
        if (!isOpen) onDismiss();
      }}
      placement="top"
      offset={8}
      className={popover}
    >
      <Dialog
        aria-label="Send selection to a session"
        className={cx(dialog, mode === 'pill' ? dialogPill : dialogComposer)}
        data-highlight-composer=""
        data-testid="highlight-composer"
      >
        {mode === 'pill' ? pill : form}
      </Dialog>
    </Popover>
  );
}

function HighlightForm({
  selection,
  message,
  setMessage,
  targets,
  selectedKey,
  status,
  messageInputRef,
  deliveryRef,
  lastRecordRef,
  selected,
  onSelectTarget,
  onSend,
  onKeepWaiting,
  onCopyFailed,
  onRelease,
}: {
  selection: HighlightSelection;
  message: string;
  setMessage: (message: string) => void;
  targets: Target[];
  selectedKey: string | null;
  status: HighlightStatus;
  messageInputRef: RefObject<HTMLTextAreaElement | null>;
  deliveryRef: MutableRefObject<{ id: string } | null>;
  lastRecordRef: MutableRefObject<SubmitRecord | null>;
  selected: Target | null;
  onSelectTarget: (key: string) => void;
  onSend: () => void;
  onKeepWaiting: () => void;
  onCopyFailed: (detail: string) => void;
  onRelease: () => void;
}) {
  return (
    <>
      <pre className={preview}>{selection.text}</pre>
      <TargetPicker targets={targets} selectedKey={selectedKey} onSelectTarget={onSelectTarget} />
      {targets.length === 0 ? (
        <span className={statusNote} role="alert">
          no native-control chat is available; raw terminals accept typing only
        </span>
      ) : null}
      <MessageField
        message={message}
        setMessage={setMessage}
        messageInputRef={messageInputRef}
        onSend={onSend}
      />
      <span className={scopeNote}>text only · attachments unavailable</span>
      <HighlightStatusRow status={status} />
      {status?.phase === 'endgame' ? (
        <HighlightEndgameActions
          status={status}
          onKeepWaiting={onKeepWaiting}
          onCopyFailed={onCopyFailed}
          onRelease={onRelease}
        />
      ) : null}
      <HighlightSendButton
        selected={selected}
        deliveryRef={deliveryRef}
        lastRecordRef={lastRecordRef}
        status={status}
        onSend={onSend}
      />
    </>
  );
}

function HighlightSendButton({
  selected,
  deliveryRef,
  lastRecordRef,
  status,
  onSend,
}: {
  selected: Target | null;
  deliveryRef: MutableRefObject<{ id: string } | null>;
  lastRecordRef: MutableRefObject<SubmitRecord | null>;
  status: HighlightStatus;
  onSend: () => void;
}) {
  return (
    <Button
      className={sendButton}
      onPress={onSend}
      isDisabled={
        (!selected && !deliveryRef.current) ||
        status?.phase === 'sending' ||
        status?.phase === 'endgame'
      }
      data-testid="highlight-send"
    >
      {status?.phase === 'sending'
        ? 'Sending…'
        : lastRecordRef.current?.phase === 'route-error'
          ? 'Retry same id'
          : 'Send'}
    </Button>
  );
}

function TargetPicker({
  targets,
  selectedKey,
  onSelectTarget,
}: {
  targets: Target[];
  selectedKey: string | null;
  onSelectTarget: (key: string) => void;
}) {
  return (
    <div className={targetRow}>
      <span className={targetLabel}>Send to</span>
      <ToggleButtonGroup
        className={toggleGroup}
        selectionMode="single"
        selectedKeys={selectedKey ? [selectedKey] : []}
        onSelectionChange={(keys) => {
          const key = [...keys][0];
          if (typeof key === 'string') onSelectTarget(key);
        }}
      >
        {targets.map((target) => (
          <ToggleButton
            key={target.key}
            id={target.key}
            className={toggle}
            data-testid={`highlight-target-${target.key}`}
          >
            {target.label}
          </ToggleButton>
        ))}
      </ToggleButtonGroup>
    </div>
  );
}

function MessageField({
  message,
  setMessage,
  messageInputRef,
  onSend,
}: {
  message: string;
  setMessage: (message: string) => void;
  messageInputRef: RefObject<HTMLTextAreaElement | null>;
  onSend: () => void;
}) {
  return (
    <TextField
      className={field}
      aria-label="Message to send with the selection"
      value={message}
      onChange={setMessage}
    >
      <TextArea
        ref={messageInputRef}
        className={area}
        placeholder="Add a message…  (Ctrl+Enter sends · Enter = newline)"
        onKeyDown={(event) => {
          if (event.key === 'Enter' && event.ctrlKey && !event.nativeEvent.isComposing) {
            event.preventDefault();
            onSend();
          }
        }}
      />
    </TextField>
  );
}

function HighlightStatusRow({ status }: { status: HighlightStatus }) {
  if (!status) return null;
  return (
    <span
      className={statusNote}
      role={status.phase === 'error' || status.phase === 'endgame' ? 'alert' : 'status'}
      data-testid="highlight-status"
    >
      {status.detail}
    </span>
  );
}

function HighlightEndgameActions({
  status,
  onKeepWaiting,
  onCopyFailed,
  onRelease,
}: {
  status: Extract<HighlightStatus, { phase: 'endgame' }>;
  onKeepWaiting: () => void;
  onCopyFailed: (detail: string) => void;
  onRelease: () => void;
}) {
  return (
    <div className={statusActions}>
      <button type="button" className={secondaryButton} onClick={onKeepWaiting}>
        keep waiting
      </button>
      <button
        type="button"
        className={secondaryButton}
        onClick={() => {
          void navigator.clipboard
            .writeText(status.requestId)
            .catch(() => onCopyFailed('could not copy requestId'));
        }}
      >
        copy requestId
      </button>
      <button type="button" className={secondaryButton} onClick={onRelease}>
        release draft
      </button>
    </div>
  );
}

export const HighlightComposer = memo(HighlightComposerImpl);

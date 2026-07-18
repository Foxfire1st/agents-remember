import { useEffect, useRef, useState } from "react";
import {
  Button,
  Dialog,
  Popover,
  TextArea,
  TextField,
  ToggleButton,
  ToggleButtonGroup,
} from "react-aria-components";

import { css, cx } from "../../styled-system/css";
import { useSelectionCapture } from "../data/selection";
import {
  createSession,
  findSessionForLeaf,
  sessionStore,
  terminalOpenFailureMessage,
  useSessions,
} from "../data/sessions";
import {
  keepWaitingForSubmit,
  releaseSubmitDraft,
  retryRouteFailure,
  submitSessionText,
  waitForSubmissionReady,
} from "../data/submitClient";
import type { SubmitRecord } from "../data/submitMachine";
import { fetchHarnesses, type HarnessInfo } from "../data/terminal";

// Selection-to-chat uses the SAME reliable native-control submit path as every composer. A
// deliberate pill/Send click is the action boundary; nothing is sent on selection alone. Targets
// are native-control harness sessions (or detected harnesses that can create one), never a plain
// terminal. Images/attachments remain UA-10 and are stated as unavailable instead of being
// smuggled through PTY paste or filesystem-path conventions.

const popover = css({ maxWidth: "min(32rem, 94vw)" });
const dialog = css({
  display: "flex",
  flexDirection: "column",
  gap: "0.45rem",
  padding: "0.55rem",
  background: "bgPanel",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "amber",
  borderRadius: "4px",
  boxShadow: "0 6px 24px rgba(0,0,0,0.5)",
  outline: "none",
});
const dialogPill = css({
  padding: "0.2rem",
  borderRadius: "999px",
  borderColor: "grid",
});
const dialogComposer = css({ width: "min(27rem, 92vw)" });
const addButton = css({
  display: "inline-flex",
  alignItems: "center",
  gap: "0.4rem",
  font: "inherit",
  fontSize: "0.74rem",
  letterSpacing: "0.02em",
  paddingInline: "0.7rem",
  paddingBlock: "0.32rem",
  borderRadius: "999px",
  border: "none",
  background: "transparent",
  color: "text",
  cursor: "pointer",
  _hover: { color: "amber" },
  _active: { transform: "scale(0.97)" },
  _focusVisible: {
    outline: "1px solid token(colors.amber)",
    outlineOffset: "1px",
  },
  _disabled: { opacity: 0.55, cursor: "default", transform: "none" },
});
const chatIcon = css({ flexShrink: 0, display: "block" });
const preview = css({
  margin: "0",
  maxHeight: "6.5rem",
  overflowY: "auto",
  whiteSpace: "pre-wrap",
  overflowWrap: "anywhere",
  font: "inherit",
  fontSize: "0.72rem",
  color: "muted",
  paddingInline: "0.4rem",
  paddingBlock: "0.3rem",
  background: "bg",
  borderLeftWidth: "2px",
  borderLeftStyle: "solid",
  borderLeftColor: "amber",
});
const targetRow = css({
  display: "flex",
  alignItems: "baseline",
  gap: "0.4rem",
  flexWrap: "wrap",
});
const targetLabel = css({
  fontSize: "0.68rem",
  letterSpacing: "0.04em",
  color: "muted",
  flexShrink: 0,
});
const toggleGroup = css({ display: "flex", gap: "0.25rem", flexWrap: "wrap" });
const toggle = css({
  font: "inherit",
  fontSize: "0.7rem",
  paddingInline: "0.45rem",
  paddingBlock: "0.15rem",
  borderRadius: "2px",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "grid",
  color: "muted",
  background: "transparent",
  cursor: "pointer",
  _selected: { borderColor: "amber", color: "amber" },
  _focusVisible: {
    outline: "1px solid token(colors.amber)",
    outlineOffset: "1px",
  },
});
const field = css({ display: "flex" });
const area = css({
  font: "inherit",
  fontSize: "0.82rem",
  lineHeight: "1.4",
  color: "inherit",
  width: "100%",
  resize: "vertical",
  minHeight: "5rem",
  maxHeight: "20rem",
  paddingInline: "0.55rem",
  paddingBlock: "0.45rem",
  background: "bg",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "grid",
  borderRadius: "2px",
  _focusVisible: {
    outline: "1px solid token(colors.amber)",
    outlineOffset: "1px",
  },
});
const sendButton = css({
  font: "inherit",
  fontSize: "0.74rem",
  letterSpacing: "0.04em",
  alignSelf: "flex-end",
  paddingInline: "0.9rem",
  paddingBlock: "0.32rem",
  borderRadius: "2px",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "amber",
  color: "amber",
  background: "transparent",
  cursor: "pointer",
  _hover: { background: "rgba(232, 193, 112, 0.1)" },
  _active: { transform: "scale(0.97)" },
  _focusVisible: {
    outline: "1px solid token(colors.amber)",
    outlineOffset: "1px",
  },
  _disabled: { opacity: 0.6, cursor: "default", transform: "none" },
});
const statusNote = css({
  fontSize: "0.68rem",
  color: "amber",
  alignSelf: "flex-end",
  overflowWrap: "anywhere",
});
const statusActions = css({
  display: "flex",
  justifyContent: "flex-end",
  gap: "0.3rem",
  flexWrap: "wrap",
});
const secondaryButton = css({
  font: "inherit",
  fontSize: "0.66rem",
  color: "muted",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "grid",
  borderRadius: "2px",
  background: "transparent",
  cursor: "pointer",
  paddingInline: "0.4rem",
  _hover: { color: "amber", borderColor: "amber" },
});
const scopeNote = css({ fontSize: "0.66rem", color: "muted" });

function ChatIcon() {
  return (
    <svg
      viewBox="0 0 16 16"
      width="13"
      height="13"
      aria-hidden="true"
      className={chatIcon}
    >
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
  | { key: string; kind: "session"; id: string; label: string }
  | {
      key: string;
      kind: "create";
      harnessId: string;
      prefix: string;
      label: string;
    };

type HighlightStatus =
  | { phase: "sending"; detail: string }
  | { phase: "error"; detail: string }
  | { phase: "endgame"; detail: string; requestId: string }
  | null;

function buildContextPackage(selectionText: string, note?: string): string {
  const parts = note && note.length > 0 ? [note, ""] : [];
  parts.push("--- from the dashboard ---", selectionText);
  return parts.join("\n");
}

function successful(record: SubmitRecord): boolean {
  return record.phase === "accepted" || record.phase === "queued";
}

export function HighlightComposer({
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
  const { selection, clear } = useSelectionCapture();
  const sessions = useSessions((state) => state.sessions);
  const activeId = useSessions((state) => state.activeId);
  const [harnesses, setHarnesses] = useState<HarnessInfo[]>([]);
  const [mode, setMode] = useState<"pill" | "composer">("pill");
  const [message, setMessage] = useState("");
  const [targetKey, setTargetKey] = useState<string | null>(null);
  const [status, setStatus] = useState<HighlightStatus>(null);
  const anchorRef = useRef<HTMLSpanElement>(null);
  const sendingRef = useRef(false);
  const deliveryRef = useRef<{ id: string } | null>(null);
  const lastRecordRef = useRef<SubmitRecord | null>(null);

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
    setMode("pill");
    setMessage("");
    setStatus(null);
    sendingRef.current = false;
    deliveryRef.current = null;
    lastRecordRef.current = null;
  }, [selection]);

  const foundDirectLeafChat =
    selection &&
    leafChatActive &&
    viewedLeafKey &&
    selection.leafKey === viewedLeafKey
      ? findSessionForLeaf(viewedLeafKey, "chat")
      : undefined;
  const directLeafChat =
    foundDirectLeafChat?.kind === "harness" &&
    (foundDirectLeafChat.status ?? "running") === "running"
      ? foundDirectLeafChat
      : undefined;

  if (!selection) return null;

  const routedSessions = (
    selectedLifecycleId
      ? sessions.filter(
          (session) => session.lifecycleId === selectedLifecycleId,
        )
      : sessions
  ).filter(
    (session) =>
      session.kind === "harness" && (session.status ?? "running") === "running",
  );
  const targets: Target[] = [
    ...routedSessions.map(
      (session): Target => ({
        key: `s:${session.id}`,
        kind: "session",
        id: session.id,
        label: session.label,
      }),
    ),
    ...harnesses
      .filter((harness) => harness.detected)
      .map(
        (harness): Target => ({
          key: `c:${harness.id}`,
          kind: "create",
          harnessId: harness.id,
          prefix: harness.name,
          label: `＋ ${harness.name}`,
        }),
      ),
  ];
  const defaultKey =
    (activeId &&
      routedSessions.some((session) => session.id === activeId) &&
      `s:${activeId}`) ||
    (routedSessions[0] && `s:${routedSessions[0].id}`) ||
    targets.find((target) => target.kind === "create")?.key ||
    null;
  const selectedKey = targets.find((target) => target.key === targetKey)
    ? targetKey
    : defaultKey;
  const selected = targets.find((target) => target.key === selectedKey) ?? null;

  const dismiss = () => {
    clear();
    setMode("pill");
  };

  const finish = () => {
    const sessionId = deliveryRef.current?.id;
    deliveryRef.current = null;
    lastRecordRef.current = null;
    sendingRef.current = false;
    dismiss();
    if (sessionId) {
      // Route ownership moves only at the accepted/queued commit point. Selecting an existing
      // target is provisional; rejected, blocked, route-error, and unresolved endgame attempts
      // must leave the operator's current active chat untouched.
      sessionStore.getState().setActive(sessionId);
      onSent?.(sessionId);
    }
  };

  const showRecord = (record: SubmitRecord, notice?: string): boolean => {
    lastRecordRef.current = record;
    if (successful(record)) {
      if (notice) {
        setStatus({
          phase: "error",
          detail: `${notice} · the original message was accepted`,
        });
        return true;
      }
      finish();
      return true;
    }
    if (record.phase === "endgame") {
      setStatus({
        phase: "endgame",
        requestId: record.requestId,
        detail: notice ?? record.detail ?? "still unresolved",
      });
    } else {
      setStatus({
        phase: "error",
        detail: notice
          ? `${notice} · ${record.detail ?? record.phase}`
          : record.detail ?? `submit ${record.phase}`,
      });
    }
    return false;
  };

  const submitTo = async (id: string, payload: string): Promise<boolean> => {
    const prior = lastRecordRef.current;
    if (prior?.phase === "route-error" && prior.requestId) {
      const retried = await retryRouteFailure(id, prior.requestId, payload);
      if (retried.record) return showRecord(retried.record, retried.notice);
      setStatus({
        phase: "error",
        detail: "the same-id retry is no longer available",
      });
      return false;
    }
    const outcome = await submitSessionText(id, payload, {
      source: "highlight",
      clearDraftOnAccept: false,
    });
    if (outcome.status === "blocked") {
      setStatus({ phase: "error", detail: outcome.reason });
      return false;
    }
    if (outcome.status === "empty") {
      setStatus({ phase: "error", detail: "the context package is empty" });
      return false;
    }
    return showRecord(outcome.record);
  };

  const directSubmit = (targetId: string) => {
    if (sendingRef.current) return;
    sendingRef.current = true;
    deliveryRef.current = { id: targetId };
    setStatus({ phase: "sending", detail: "Sending…" });
    void submitTo(targetId, buildContextPackage(selection.text)).then(
      (sent) => {
        sendingRef.current = false;
        if (!sent) setMode("composer");
      },
    );
  };

  const send = async () => {
    if (
      (!selected && !deliveryRef.current) ||
      sendingRef.current ||
      status?.phase === "endgame"
    ) {
      return;
    }
    sendingRef.current = true;
    setStatus({ phase: "sending", detail: "Sending…" });
    try {
      let context = deliveryRef.current;
      if (!context) {
        let id: string;
        if (!selected) return;
        if (selected.kind === "session") {
          id = selected.id;
        } else {
          const result = selectedLifecycleId
            ? await createSession(
                selected.prefix,
                "harness",
                selected.harnessId,
                selectedLifecycleId,
              )
            : await createSession(
                selected.prefix,
                "harness",
                selected.harnessId,
              );
          if (result.outcome === "failed") {
            setStatus({
              phase: "error",
              detail: terminalOpenFailureMessage(result),
            });
            return;
          }
          id = result.session.id;
          const gate = await waitForSubmissionReady(id);
          if (!gate.ready) {
            setStatus({
              phase: "error",
              detail: gate.reason ?? "native control did not become ready",
            });
            deliveryRef.current = { id };
            return;
          }
        }
        context = { id };
        deliveryRef.current = context;
      }
      await submitTo(context.id, buildContextPackage(selection.text, message));
    } finally {
      sendingRef.current = false;
    }
  };

  const keepWaiting = async () => {
    const record = lastRecordRef.current;
    const context = deliveryRef.current;
    if (!record || record.phase !== "endgame" || !context || sendingRef.current)
      return;
    sendingRef.current = true;
    setStatus({ phase: "sending", detail: "Reconciling the same requestId…" });
    try {
      const final = await keepWaitingForSubmit(context.id, record.requestId);
      if (final) showRecord(final);
      else
        setStatus({
          phase: "error",
          detail: "this request is no longer waiting",
        });
    } finally {
      sendingRef.current = false;
    }
  };

  return (
    <>
      <span
        ref={anchorRef}
        aria-hidden="true"
        style={{
          position: "fixed",
          left: selection.rect.left,
          top: selection.rect.top,
          width: selection.rect.width,
          height: selection.rect.height,
          pointerEvents: "none",
        }}
      />
      <Popover
        triggerRef={anchorRef}
        isOpen
        onOpenChange={(isOpen) => {
          if (!isOpen) dismiss();
        }}
        placement="top"
        offset={8}
        className={popover}
      >
        <Dialog
          aria-label="Send selection to a session"
          className={cx(dialog, mode === "pill" ? dialogPill : dialogComposer)}
          data-highlight-composer=""
          data-testid="highlight-composer"
        >
          {mode === "pill" ? (
            <Button
              className={addButton}
              isDisabled={status?.phase === "sending"}
              onPress={() =>
                directLeafChat
                  ? directSubmit(directLeafChat.id)
                  : setMode("composer")
              }
              data-testid="highlight-add-to-chat"
            >
              <ChatIcon />
              Add to chat
            </Button>
          ) : (
            <>
              <pre className={preview}>{selection.text}</pre>
              <div className={targetRow}>
                <span className={targetLabel}>Send to</span>
                <ToggleButtonGroup
                  className={toggleGroup}
                  selectionMode="single"
                  selectedKeys={selectedKey ? [selectedKey] : []}
                  onSelectionChange={(keys) => {
                    const key = [...keys][0];
                    if (typeof key === "string") {
                      setTargetKey(key);
                      deliveryRef.current = null;
                      lastRecordRef.current = null;
                      setStatus(null);
                    }
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
              {targets.length === 0 ? (
                <span className={statusNote} role="alert">
                  no native-control chat is available; raw terminals accept
                  typing only
                </span>
              ) : null}
              <TextField
                className={field}
                aria-label="Message to send with the selection"
                value={message}
                onChange={setMessage}
                autoFocus
              >
                <TextArea
                  className={area}
                  placeholder="Add a message…  (Ctrl+Enter sends · Enter = newline)"
                  onKeyDown={(event) => {
                    if (
                      event.key === "Enter" &&
                      event.ctrlKey &&
                      !event.nativeEvent.isComposing
                    ) {
                      event.preventDefault();
                      void send();
                    }
                  }}
                />
              </TextField>
              <span className={scopeNote}>
                text only · attachments unavailable
              </span>
              {status ? (
                <span
                  className={statusNote}
                  role={
                    status.phase === "error" || status.phase === "endgame"
                      ? "alert"
                      : "status"
                  }
                  data-testid="highlight-status"
                >
                  {status.detail}
                </span>
              ) : null}
              {status?.phase === "endgame" ? (
                <div className={statusActions}>
                  <button
                    type="button"
                    className={secondaryButton}
                    onClick={() => void keepWaiting()}
                  >
                    keep waiting
                  </button>
                  <button
                    type="button"
                    className={secondaryButton}
                    onClick={() => {
                      void navigator.clipboard
                        .writeText(status.requestId)
                        .catch(() =>
                          setStatus({
                            ...status,
                            detail: "could not copy requestId",
                          }),
                        );
                    }}
                  >
                    copy requestId
                  </button>
                  <button
                    type="button"
                    className={secondaryButton}
                    onClick={() => {
                      const context = deliveryRef.current;
                      if (!context) return;
                      releaseSubmitDraft(context.id, status.requestId);
                      lastRecordRef.current = null;
                      setStatus({
                        phase: "error",
                        detail:
                          "unresolved request released; a new Send will use a new requestId",
                      });
                    }}
                  >
                    release draft
                  </button>
                </div>
              ) : null}
              <Button
                className={sendButton}
                onPress={() => void send()}
                isDisabled={
                  (!selected && !deliveryRef.current) ||
                  status?.phase === "sending" ||
                  status?.phase === "endgame"
                }
                data-testid="highlight-send"
              >
                {status?.phase === "sending"
                  ? "Sending…"
                  : lastRecordRef.current?.phase === "route-error"
                    ? "Retry same id"
                    : "Send"}
              </Button>
            </>
          )}
        </Dialog>
      </Popover>
    </>
  );
}

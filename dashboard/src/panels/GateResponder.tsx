import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type KeyboardEvent as ReactKeyboardEvent,
  type PointerEvent as ReactPointerEvent,
} from "react";
import { Button, Dialog, TextArea, TextField } from "react-aria-components";

import { css, cx } from "../../styled-system/css";
import { postGateDecision } from "../data/actions";
import { postOperatorInbox } from "../data/operatorInbox";
import {
  deliverToSession,
  findSessionForLifecycle,
  sessionStore,
  useSessions,
  type DeliveryStatus,
} from "../data/sessions";
import type { GateNode } from "../types/projection";
import {
  askQuestion,
  diagnosticText,
  packageResponse,
  requestText,
  statusText,
  type GateResponseStatus,
} from "./GateResponderText";

export { isWorktreeGateKind } from "./GateResponderText";

type ResponseMode = "no";
type GateDecisionVerb = "approve" | "reject" | "cancel";

const MIN_REQUEST_HEIGHT = 480;
const REQUEST_HEIGHT_MARGIN = 24;
const KEYBOARD_RESIZE_STEP = 40;

const shell = css({
  display: "grid",
  gap: "0.45rem",
  margin: "0.5rem 0",
  padding: "0.5rem 0.6rem",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "amber",
  borderRadius: "3px",
  background: "oklch(0.82 0.16 75 / 0.08)",
});
const compactShell = css({
  margin: "0",
  padding: "0",
  borderColor: "transparent",
  background: "transparent",
});
const top = css({ display: "flex", alignItems: "baseline", justifyContent: "space-between", gap: "0.5rem" });
const title = css({ minWidth: "0", fontSize: "0.78rem" });
const titleKind = css({ color: "amber" });
const respondButton = css({
  font: "inherit",
  fontSize: "0.72rem",
  paddingInline: "0.55rem",
  paddingBlock: "0.16rem",
  borderRadius: "2px",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "cyan",
  color: "cyan",
  background: "transparent",
  cursor: "pointer",
  _hover: { background: "oklch(0.7 0.1 200 / 0.12)" },
  _focusVisible: { outline: "1px solid token(colors.amber)", outlineOffset: "1px" },
  _disabled: { opacity: 0.6, cursor: "default" },
});
const panel = css({
  display: "grid",
  gap: "0.45rem",
  padding: "0.5rem",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "grid",
  borderRadius: "3px",
  background: "bgPanel",
  outline: "none",
});
const request = css({
  margin: "0",
  height: "var(--gate-request-height)",
  maxHeight: "var(--gate-request-max-height)",
  overflowY: "auto",
  whiteSpace: "pre-wrap",
  overflowWrap: "anywhere",
  font: "inherit",
  fontSize: "0.72rem",
  color: "muted",
  paddingInline: "0.45rem",
  paddingBlock: "0.35rem",
  background: "bg",
  borderLeftWidth: "2px",
  borderLeftStyle: "solid",
  borderLeftColor: "amber",
});
const resizeHandle = css({
  height: "0.55rem",
  cursor: "row-resize",
  borderTopWidth: "1px",
  borderBottomWidth: "1px",
  borderTopStyle: "solid",
  borderBottomStyle: "solid",
  borderColor: "grid",
  background:
    "linear-gradient(to bottom, transparent 0, transparent 2px, token(colors.grid) 2px, token(colors.grid) 3px, transparent 3px)",
  _focusVisible: { outline: "1px solid token(colors.amber)", outlineOffset: "1px" },
});
const diagnostics = css({ color: "muted", fontSize: "0.7rem" });
const diagnosticPre = css({
  marginBlockStart: "0.35rem",
  maxHeight: "12rem",
  overflow: "auto",
  whiteSpace: "pre-wrap",
  overflowWrap: "anywhere",
  padding: "0.35rem",
  background: "bg",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "grid",
});
const route = css({
  display: "flex",
  alignItems: "center",
  justifyContent: "space-between",
  gap: "0.5rem",
  color: "muted",
  fontSize: "0.72rem",
});
const choices = css({ display: "flex", gap: "0.3rem", flexWrap: "wrap" });
const field = css({ display: "grid", gap: "0.3rem" });
const fieldLabel = css({ color: "muted", fontSize: "0.7rem" });
const area = css({
  font: "inherit",
  fontSize: "0.78rem",
  lineHeight: "1.4",
  color: "inherit",
  width: "100%",
  resize: "vertical",
  minHeight: "4.2rem",
  maxHeight: "16rem",
  paddingInline: "0.5rem",
  paddingBlock: "0.4rem",
  background: "bg",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "grid",
  borderRadius: "2px",
  _focusVisible: { outline: "1px solid token(colors.amber)", outlineOffset: "1px" },
});
const footer = css({ display: "flex", alignItems: "center", justifyContent: "space-between", gap: "0.5rem" });
const statusNote = css({ color: "amber", fontSize: "0.7rem" });

function clamp(value: number, min: number, max: number): number {
  return Math.min(Math.max(value, min), max);
}

export function GateResponder({
  lifecycleId,
  gateNode,
  ask,
  compact = false,
  testId = "gate-responder",
}: {
  lifecycleId: string;
  gateNode?: GateNode;
  ask?: Record<string, unknown>;
  compact?: boolean;
  testId?: string;
}) {
  const sessions = useSessions((state) => state.sessions);
  const activeId = useSessions((state) => state.activeId);
  const attachedSession = sessions.find((session) => session.lifecycleId === lifecycleId);
  const activeSession = activeId ? sessions.find((session) => session.id === activeId) : undefined;
  const canAttachActive = Boolean(activeSession && !activeSession.lifecycleId);
  const fullRequest = useMemo(() => requestText(gateNode, ask), [gateNode, ask]);
  const diagnosticsText = useMemo(() => diagnosticText(gateNode, ask), [gateNode, ask]);
  const [open, setOpen] = useState(false);
  const [mode, setMode] = useState<ResponseMode | null>(null);
  const [draft, setDraft] = useState("");
  const [status, setStatus] = useState<GateResponseStatus>("idle");
  const [recordedDecision, setRecordedDecision] = useState(false);
  const [requestHeight, setRequestHeight] = useState(MIN_REQUEST_HEIGHT);
  const [contentHeight, setContentHeight] = useState(MIN_REQUEST_HEIGHT);
  const busyRef = useRef(false);
  const requestRef = useRef<HTMLPreElement | null>(null);
  const maxRequestHeight = Math.max(MIN_REQUEST_HEIGHT, contentHeight + REQUEST_HEIGHT_MARGIN);
  const maxRequestHeightRef = useRef(maxRequestHeight);
  maxRequestHeightRef.current = maxRequestHeight;
  const boundedRequestHeight = clamp(requestHeight, MIN_REQUEST_HEIGHT, maxRequestHeight);
  const busy = status === "recording" || status === "sending";

  useEffect(() => {
    if (!open) return;
    const element = requestRef.current;
    if (!element) return;
    const measure = () => setContentHeight(element.scrollHeight);
    measure();
    const frame = window.requestAnimationFrame(measure);
    return () => window.cancelAnimationFrame(frame);
  }, [fullRequest, open]);

  useEffect(() => {
    setRequestHeight((value) => clamp(value, MIN_REQUEST_HEIGHT, maxRequestHeight));
  }, [maxRequestHeight]);

  const label = gateNode ? `${gateNode.kind} · ${gateNode.state}` : (askQuestion(ask) ?? "awaiting input");

  const resetAndClose = () => {
    setOpen(false);
    setMode(null);
    setDraft("");
    setStatus("idle");
  };

  const notifyAgent = async (response: string): Promise<boolean> => {
    const text = response.trim();
    if (!text) return false;
    const target = findSessionForLifecycle(lifecycleId);
    setStatus("sending");
    const packaged = packageResponse(lifecycleId, gateNode, ask, text);
    if (target) {
      const result: DeliveryStatus = await deliverToSession(target.id, packaged);
      setStatus(result === "delivered" ? "delivered" : "unconfirmed");
      return result === "delivered";
    }
    const result = await postOperatorInbox({
      lifecycleId,
      gateId: gateNode?.id,
      ask: fullRequest,
      response: text,
    });
    setStatus(result === "posted" ? "inbox" : "inbox-error");
    return result === "posted";
  };

  const recordDecision = async (verb: GateDecisionVerb, note?: string): Promise<boolean> => {
    if (!gateNode) return true;
    setStatus("recording");
    const result = await postGateDecision(lifecycleId, verb, { gateId: gateNode.id, note });
    if (result === "recorded") {
      setRecordedDecision(true);
      return true;
    }
    setRecordedDecision(false);
    setStatus(result === "stale-gate" || result === "no-open-gate" ? result : "decision-error");
    return false;
  };

  const runAction = async (action: () => Promise<void>) => {
    if (busyRef.current) return;
    busyRef.current = true;
    try {
      await action();
    } finally {
      busyRef.current = false;
    }
  };

  const approve = () => void runAction(async () => {
    setMode(null);
    setRecordedDecision(false);
    const recorded = await recordDecision("approve");
    if (recorded) {
      const notified = await notifyAgent("Approved by developer in dashboard.");
      if (notified) resetAndClose();
    }
  });

  const reject = () => void runAction(async () => {
    const reason = draft.trim();
    if (!reason) return;
    setRecordedDecision(false);
    const recorded = await recordDecision("reject", reason);
    if (recorded) {
      const notified = await notifyAgent(`Rejected by developer in dashboard.\n\nReason:\n${reason}`);
      if (notified) resetAndClose();
    }
  });

  const dismiss = () => void runAction(async () => {
    setMode(null);
    setRecordedDecision(false);
    const recorded = await recordDecision("cancel", "Dismissed by developer in dashboard.");
    if (recorded) resetAndClose();
  });

  const openDraft = (nextMode: ResponseMode) => {
    setMode(nextMode);
    setDraft("");
    setRecordedDecision(false);
    setStatus("idle");
  };

  const attachActive = () => {
    if (!activeSession || activeSession.lifecycleId) return;
    sessionStore.getState().setLifecycle(activeSession.id, lifecycleId);
    setStatus("idle");
  };

  const resizeStyle = {
    "--gate-request-height": `${boundedRequestHeight}px`,
    "--gate-request-max-height": `${maxRequestHeight}px`,
  } as CSSProperties;

  const startResize = (event: ReactPointerEvent<HTMLDivElement>) => {
    event.preventDefault();
    const startY = event.clientY;
    const startHeight = boundedRequestHeight;
    const move = (moveEvent: PointerEvent) => {
      setRequestHeight(clamp(startHeight + moveEvent.clientY - startY, MIN_REQUEST_HEIGHT, maxRequestHeightRef.current));
    };
    const stop = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", stop);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", stop);
  };

  const resizeByKeyboard = (event: ReactKeyboardEvent<HTMLDivElement>) => {
    if (event.key !== "ArrowUp" && event.key !== "ArrowDown" && event.key !== "Home" && event.key !== "End") return;
    event.preventDefault();
    setRequestHeight((value) => {
      if (event.key === "Home") return MIN_REQUEST_HEIGHT;
      if (event.key === "End") return maxRequestHeightRef.current;
      const delta = event.key === "ArrowUp" ? -KEYBOARD_RESIZE_STEP : KEYBOARD_RESIZE_STEP;
      return clamp(value + delta, MIN_REQUEST_HEIGHT, maxRequestHeightRef.current);
    });
  };

  const note = statusText(status, attachedSession?.label, recordedDecision);
  const statusIsError = status === "unconfirmed" || status === "inbox-error" || status === "decision-error" || status === "stale-gate" || status === "no-open-gate";
  const fieldCopy = "Reason for rejection";

  return (
    <div className={cx(shell, compact ? compactShell : undefined)} data-testid={testId}>
      <div className={top}>
        <div className={title}>
          <strong>Gate</strong> · <span className={titleKind}>{label}</span>
        </div>
        <Button
          className={respondButton}
          onPress={() => setOpen((value) => !value)}
          data-testid="gate-respond-open"
        >
          Respond
        </Button>
      </div>
      {open ? (
        <Dialog aria-label="Respond to gate" className={panel} data-testid="gate-respond-dialog">
          <pre
            ref={requestRef}
            className={request}
            style={resizeStyle}
            data-testid="gate-request"
          >
            {fullRequest}
          </pre>
          <div
            className={resizeHandle}
            role="separator"
            aria-label="Resize gate request"
            aria-orientation="horizontal"
            tabIndex={0}
            onPointerDown={startResize}
            onKeyDown={resizeByKeyboard}
            data-testid="gate-request-resize"
          />
          {diagnosticsText ? (
            <details className={diagnostics}>
              <summary>Diagnostics JSON</summary>
              <pre className={diagnosticPre} data-testid="gate-request-diagnostics">
                {diagnosticsText}
              </pre>
            </details>
          ) : null}
          <div className={route}>
            <span data-testid="gate-route">
              {attachedSession ? `To ${attachedSession.label}` : `External inbox for ${lifecycleId}`}
            </span>
            {canAttachActive && activeSession ? (
              <Button
                className={respondButton}
                onPress={attachActive}
                data-testid="gate-respond-attach-active"
              >
                Attach {activeSession.label}
              </Button>
            ) : null}
          </div>
          <div className={choices}>
            <Button
              className={respondButton}
              onPress={approve}
              isDisabled={busy}
              data-testid="gate-respond-yes"
            >
              Yes
            </Button>
            <Button
              className={respondButton}
              onPress={() => openDraft("no")}
              isDisabled={busy}
              data-testid="gate-respond-no"
            >
              No
            </Button>
            <Button
              className={respondButton}
              onPress={dismiss}
              isDisabled={busy || !gateNode}
              data-testid="gate-respond-dismiss"
            >
              Dismiss
            </Button>
          </div>
          {mode ? (
            <TextField
              className={field}
              aria-label={fieldCopy}
              value={draft}
              onChange={setDraft}
              autoFocus
            >
              <span className={fieldLabel}>{fieldCopy}</span>
              <TextArea className={area} data-testid="gate-respond-text" />
            </TextField>
          ) : null}
          <div className={footer}>
            {note ? (
              <span
                className={statusNote}
                role={statusIsError ? "alert" : "status"}
                data-testid="gate-respond-status"
              >
                {note}
              </span>
            ) : (
              <span />
            )}
            {mode ? (
              <Button
                className={respondButton}
                onPress={reject}
                isDisabled={busy || !draft.trim()}
                data-testid="gate-respond-send"
              >
                {busy ? "Sending..." : "Reject"}
              </Button>
            ) : null}
          </div>
        </Dialog>
      ) : null}
    </div>
  );
}

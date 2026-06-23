import { useMemo, useRef, useState } from "react";
import { Button, Dialog, TextArea, TextField } from "react-aria-components";

import { css, cx } from "../../styled-system/css";
import {
  deliverToSession,
  findSessionForLifecycle,
  sessionStore,
  useSessions,
  type DeliveryStatus,
} from "../data/sessions";
import type { GateNode } from "../types/projection";

type ResponseMode = "no" | "chat";
type GateResponseStatus = "idle" | "sending" | "delivered" | "unconfirmed" | "missing";

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
  maxHeight: "10rem",
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

function pretty(value: Record<string, unknown> | undefined): string {
  if (!value || Object.keys(value).length === 0) return "{}";
  return JSON.stringify(value, null, 2);
}

function askQuestion(ask: Record<string, unknown> | undefined): string | null {
  const question = ask?.question;
  return typeof question === "string" && question.trim() ? question : null;
}

function requestText(gateNode: GateNode | undefined, ask: Record<string, unknown> | undefined): string {
  const blocks: string[] = [];
  if (gateNode) {
    blocks.push(
      [
        `Gate: ${gateNode.kind}`,
        `State: ${gateNode.state}`,
        "Packet:",
        pretty(gateNode.packet),
      ].join("\n"),
    );
  }
  if (ask) blocks.push(["Ask:", pretty(ask)].join("\n"));
  return blocks.join("\n\n");
}

function packageResponse(
  lifecycleId: string,
  gateNode: GateNode | undefined,
  ask: Record<string, unknown> | undefined,
  response: string,
): string {
  return [
    `Dashboard response for lifecycle ${lifecycleId}`,
    gateNode ? `Gate: ${gateNode.kind} (${gateNode.state})` : "Gate: ask",
    "",
    response.trim(),
    "",
    "--- gate request ---",
    requestText(gateNode, ask),
  ].join("\n");
}

function statusText(status: GateResponseStatus, label: string | undefined): string {
  if (status === "sending") return "Sending...";
  if (status === "delivered") return label ? `Sent to ${label}.` : "Sent.";
  if (status === "missing") return "No hosted chat is attached.";
  if (status === "unconfirmed") return "Couldn't confirm delivery. Retry?";
  return "";
}

export function isWorktreeGateKind(kind: string): boolean {
  return /closeout|push|integration|cleanup/.test(kind);
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
  const [open, setOpen] = useState(false);
  const [mode, setMode] = useState<ResponseMode | null>(null);
  const [draft, setDraft] = useState("");
  const [status, setStatus] = useState<GateResponseStatus>("idle");
  const sendingRef = useRef(false);

  const label = gateNode ? `${gateNode.kind} · ${gateNode.state}` : (askQuestion(ask) ?? "awaiting input");
  const send = async (response: string) => {
    const text = response.trim();
    if (!text || sendingRef.current) return;
    const target = findSessionForLifecycle(lifecycleId);
    if (!target) {
      setStatus("missing");
      return;
    }
    sendingRef.current = true;
    setStatus("sending");
    try {
      const result: DeliveryStatus = await deliverToSession(
        target.id,
        packageResponse(lifecycleId, gateNode, ask, text),
      );
      setStatus(result === "delivered" ? "delivered" : "unconfirmed");
    } finally {
      sendingRef.current = false;
    }
  };

  const openDraft = (nextMode: ResponseMode) => {
    setMode(nextMode);
    setDraft("");
    setStatus("idle");
  };

  const attachActive = () => {
    if (!activeSession || activeSession.lifecycleId) return;
    sessionStore.getState().setLifecycle(activeSession.id, lifecycleId);
    setStatus("idle");
  };

  const note = statusText(status, attachedSession?.label);

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
          <pre className={request} data-testid="gate-request">
            {fullRequest}
          </pre>
          <div className={route}>
            <span data-testid="gate-route">
              {attachedSession ? `To ${attachedSession.label}` : `No hosted chat attached to ${lifecycleId}`}
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
              onPress={() => void send("Proceed.")}
              isDisabled={!attachedSession || status === "sending"}
              data-testid="gate-respond-yes"
            >
              Yes
            </Button>
            <Button className={respondButton} onPress={() => openDraft("no")} data-testid="gate-respond-no">
              No
            </Button>
            <Button className={respondButton} onPress={() => openDraft("chat")} data-testid="gate-respond-chat">
              Chat
            </Button>
          </div>
          {mode ? (
            <TextField
              className={field}
              aria-label={mode === "no" ? "No response" : "Chat response"}
              value={draft}
              onChange={setDraft}
              autoFocus
            >
              <span className={fieldLabel}>{mode === "no" ? "No response" : "Chat response"}</span>
              <TextArea className={area} data-testid="gate-respond-text" />
            </TextField>
          ) : null}
          <div className={footer}>
            {note ? (
              <span
                className={statusNote}
                role={status === "unconfirmed" || status === "missing" ? "alert" : "status"}
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
                onPress={() => void send(draft)}
                isDisabled={!attachedSession || status === "sending" || !draft.trim()}
                data-testid="gate-respond-send"
              >
                {status === "sending" ? "Sending..." : status === "unconfirmed" ? "Retry" : "Send"}
              </Button>
            ) : null}
          </div>
        </Dialog>
      ) : null}
    </div>
  );
}

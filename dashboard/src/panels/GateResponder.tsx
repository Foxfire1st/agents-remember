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
import { readAdapterDecisionFailure } from "../data/interactionAnswer";
import { postOperatorInbox } from "../data/operatorInbox";
import {
  sessionStore,
  useSessions,
} from "../data/sessions";
import type { GateNode } from "../types/projection";
import {
  askQuestion,
  diagnosticText,
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

// A reopened hosted-interaction gate carries `packet.adapterDecisionFailure` — proof the operator's
// last decision could not be applied. Without this the reopened gate looks identical to a
// never-answered one (M6): the failure notice makes the reopen honest at a glance.
const decisionFailureBox = css({
  display: "grid",
  gap: "0.25rem",
  padding: "0.4rem 0.5rem",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "alarm",
  borderRadius: "2px",
  background: "oklch(0.62 0.19 25 / 0.08)",
  fontSize: "0.72rem",
});
const decisionFailureLead = css({ color: "alarm", fontWeight: 600 });
const decisionFailureNote = css({
  whiteSpace: "pre-wrap",
  overflowWrap: "anywhere",
  color: "ink",
  paddingInline: "0.4rem",
  paddingBlock: "0.3rem",
  background: "bg",
  borderLeftWidth: "2px",
  borderLeftStyle: "solid",
  borderLeftColor: "alarm",
});
const decisionFailureLine = css({ color: "muted" });

function clamp(value: number, min: number, max: number): number {
  return Math.min(Math.max(value, min), max);
}

// The decided gate state, as an operator-facing noun for the failure lead. An unrecognized state
// falls back to the raw word rather than a guess.
const DECISION_NOUNS: Record<string, string> = {
  approved: "approval",
  rejected: "rejection",
  "revision-requested": "revision request",
  cancelled: "dismissal",
};

function decisionNoun(decision: string | undefined): string {
  if (!decision) return "answer";
  return DECISION_NOUNS[decision] ?? `${decision} decision`;
}

// hosted_interactions.py proves exactly two delivery certainties; each maps to honest copy and
// NOTHING is asserted beyond them. "not-sent" = zero bytes reached the harness (safe to redo);
// "unknown" = the harness may already hold the answer (redoing could answer it twice). Any other
// word (a wire we did not author) is shown verbatim, never guessed into one of the two.
function deliveryHonesty(delivery: string): string {
  if (delivery === "not-sent") return "The harness never received it, so deciding again is safe.";
  if (delivery === "unknown") {
    return "The harness may already hold it — deciding again could answer the question twice.";
  }
  return `Delivery state: ${delivery}.`;
}

interface GateResponderProps {
  lifecycleId: string;
  gateNode?: GateNode;
  ask?: Record<string, unknown>;
  compact?: boolean;
  testId?: string;
}

interface GateResponderState {
  lifecycleId: string;
  gateNode: GateNode | undefined;
  open: boolean;
  mode: ResponseMode | null;
  draft: string;
  status: GateResponseStatus;
  recordedDecision: boolean;
  requestHeight: number;
  contentHeight: number;
  busy: boolean;
  maxRequestHeight: number;
  boundedRequestHeight: number;
  fullRequest: string;
  diagnosticsText: string;
  decisionFailure: ReturnType<typeof readAdapterDecisionFailure>;
  attachedSession: ReturnType<typeof sessionStore.getState>["sessions"][number] | undefined;
  activeSession: ReturnType<typeof sessionStore.getState>["sessions"][number] | undefined;
  canAttachActive: boolean;
  label: string;
  note: string | null;
  statusIsError: boolean;
  requestRef: React.RefObject<HTMLPreElement | null>;
  draftInputRef: React.RefObject<HTMLTextAreaElement | null>;
  setOpen: (open: boolean | ((cur: boolean) => boolean)) => void;
  setMode: (mode: ResponseMode | null) => void;
  setDraft: (draft: string) => void;
  setStatus: (status: GateResponseStatus) => void;
  setRecordedDecision: (value: boolean) => void;
  setRequestHeight: (value: number | ((cur: number) => number)) => void;
  setContentHeight: (value: number) => void;
}

function useGateResponderState({
  lifecycleId,
  gateNode,
  ask,
}: GateResponderProps): GateResponderState {
  const sessions = useSessions((state) => state.sessions);
  const activeId = useSessions((state) => state.activeId);
  const fullRequest = useMemo(() => requestText(gateNode, ask), [gateNode, ask]);
  const diagnosticsText = useMemo(() => diagnosticText(gateNode, ask), [gateNode, ask]);
  const decisionFailure = readAdapterDecisionFailure(gateNode?.packet);
  const attachedSession = sessions.find((session) => session.lifecycleId === lifecycleId);
  const activeSession = activeSessionFor(activeId, sessions);
  const canAttachActive = attachable(activeSession);
  const [open, setOpen] = useState(false);
  const [mode, setMode] = useState<ResponseMode | null>(null);
  const [draft, setDraft] = useState("");
  const [status, setStatus] = useState<GateResponseStatus>("idle");
  const [recordedDecision, setRecordedDecision] = useState(false);
  const [requestHeight, setRequestHeight] = useState(MIN_REQUEST_HEIGHT);
  const [contentHeight, setContentHeight] = useState(MIN_REQUEST_HEIGHT);
  const requestRef = useRef<HTMLPreElement | null>(null);
  const draftInputRef = useRef<HTMLTextAreaElement | null>(null);
  const maxRequestHeight = Math.max(MIN_REQUEST_HEIGHT, contentHeight + REQUEST_HEIGHT_MARGIN);
  const boundedRequestHeight = clamp(requestHeight, MIN_REQUEST_HEIGHT, maxRequestHeight);
  const busy = status === "recording" || status === "sending";
  const label = gateNode
    ? `${gateNode.kind} · ${gateNode.state}`
    : (askQuestion(ask) ?? "awaiting input");
  const note = statusText(status, attachedSessionLabel(sessions, lifecycleId), recordedDecision);
  const statusIsError = statusIsErrorWord(status);
  return {
    lifecycleId,
    gateNode,
    open,
    mode,
    draft,
    status,
    recordedDecision,
    requestHeight,
    contentHeight,
    busy,
    maxRequestHeight,
    boundedRequestHeight,
    fullRequest,
    diagnosticsText,
    decisionFailure,
    attachedSession,
    activeSession,
    canAttachActive,
    label,
    note,
    statusIsError,
    requestRef,
    draftInputRef,
    setOpen,
    setMode,
    setDraft,
    setStatus,
    setRecordedDecision,
    setRequestHeight,
    setContentHeight,
  };
}

function attachedSessionLabel(
  sessions: ReturnType<typeof sessionStore.getState>["sessions"],
  lifecycleId: string,
): string | undefined {
  return sessions.find((session) => session.lifecycleId === lifecycleId)?.label;
}

function activeSessionFor(
  activeId: string | null,
  sessions: ReturnType<typeof sessionStore.getState>["sessions"],
): ReturnType<typeof sessionStore.getState>["sessions"][number] | undefined {
  return activeId ? sessions.find((session) => session.id === activeId) : undefined;
}

function attachable(activeSession: ReturnType<typeof sessionStore.getState>["sessions"][number] | undefined): boolean {
  return Boolean(activeSession && !activeSession.lifecycleId);
}

function statusIsErrorWord(status: GateResponseStatus): boolean {
  return (
    status === "unconfirmed" ||
    status === "inbox-error" ||
    status === "decision-error" ||
    status === "stale-gate" ||
    status === "no-open-gate"
  );
}

function useGateResize(
  state: GateResponderState,
): {
  startResize: (event: ReactPointerEvent<HTMLDivElement>) => void;
  resizeByKeyboard: (event: ReactKeyboardEvent<HTMLDivElement>) => void;
} {
  const maxRequestHeightRef = useRef(state.maxRequestHeight);
  maxRequestHeightRef.current = state.maxRequestHeight;

  const startResize = (event: ReactPointerEvent<HTMLDivElement>) => {
    event.preventDefault();
    const startY = event.clientY;
    const startHeight = state.boundedRequestHeight;
    const move = (moveEvent: PointerEvent) => {
      state.setRequestHeight(
        clamp(startHeight + moveEvent.clientY - startY, MIN_REQUEST_HEIGHT, maxRequestHeightRef.current),
      );
    };
    const stop = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", stop);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", stop);
  };

  const resizeByKeyboard = (event: ReactKeyboardEvent<HTMLDivElement>) => {
    if (
      event.key !== "ArrowUp" &&
      event.key !== "ArrowDown" &&
      event.key !== "Home" &&
      event.key !== "End"
    ) {
      return;
    }
    event.preventDefault();
    state.setRequestHeight((value) => {
      if (event.key === "Home") return MIN_REQUEST_HEIGHT;
      if (event.key === "End") return maxRequestHeightRef.current;
      const delta = event.key === "ArrowUp" ? -KEYBOARD_RESIZE_STEP : KEYBOARD_RESIZE_STEP;
      return clamp(value + delta, MIN_REQUEST_HEIGHT, maxRequestHeightRef.current);
    });
  };
  return { startResize, resizeByKeyboard };
}

function useGateDecisionActions(state: GateResponderState, props: GateResponderProps) {
  const { lifecycleId } = props;
  const busyRef = useRef(false);
  const resetAndClose = () => {
    state.setOpen(false);
    state.setMode(null);
    state.setDraft("");
    state.setStatus("idle");
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
  const approve = () =>
    void runAction(async () => {
      state.setMode(null);
      state.setRecordedDecision(false);
      const recorded = await recordGateDecision(state, props, "approve");
      if (recorded) {
        const notified = await notifyAgentResponse(state, props, "Approved by developer in dashboard.");
        if (notified) resetAndClose();
      }
    });
  const reject = () =>
    void runAction(async () => {
      const reason = state.draft.trim();
      if (!reason) return;
      state.setRecordedDecision(false);
      const recorded = await recordGateDecision(state, props, "reject", reason);
      if (recorded) {
        const notified = await notifyAgentResponse(
          state,
          props,
          `Rejected by developer in dashboard.\n\nReason:\n${reason}`,
        );
        if (notified) resetAndClose();
      }
    });
  const dismiss = () =>
    void runAction(async () => {
      state.setMode(null);
      state.setRecordedDecision(false);
      const recorded = await recordGateDecision(
        state,
        props,
        "cancel",
        "Dismissed by developer in dashboard.",
      );
      if (recorded) resetAndClose();
    });
  const openDraft = (nextMode: ResponseMode) => {
    state.setMode(nextMode);
    state.setDraft("");
    state.setRecordedDecision(false);
    state.setStatus("idle");
  };
  const attachActive = () => {
    if (!state.activeSession || state.activeSession.lifecycleId) return;
    sessionStore.getState().setLifecycle(state.activeSession.id, lifecycleId);
    state.setStatus("idle");
  };
  return { approve, reject, dismiss, openDraft, attachActive };
}

async function notifyAgentResponse(
  state: GateResponderState,
  props: GateResponderProps,
  response: string,
): Promise<boolean> {
  const text = response.trim();
  if (!text) return false;
  state.setStatus("sending");
  if (props.gateNode?.packet?.adapterInteraction) {
    // The durable gate decision is the adapter response source. The backend synchronizer returns
    // it to the exact pending interaction; a second terminal/inbox message would duplicate it.
    state.setStatus("delivered");
    return true;
  }
  const result = await postOperatorInbox({
    lifecycleId: props.lifecycleId,
    gateId: props.gateNode?.id,
    ask: state.fullRequest,
    response: text,
  });
  state.setStatus(result === "posted" ? "inbox" : "inbox-error");
  return result === "posted";
}

async function recordGateDecision(
  state: GateResponderState,
  props: GateResponderProps,
  verb: GateDecisionVerb,
  note?: string,
): Promise<boolean> {
  if (!props.gateNode) return true;
  state.setStatus("recording");
  const result = await postGateDecision(props.lifecycleId, verb, {
    gateId: props.gateNode.id,
    note,
  });
  if (result === "recorded") {
    state.setRecordedDecision(true);
    return true;
  }
  state.setRecordedDecision(false);
  state.setStatus(
    result === "stale-gate" || result === "no-open-gate" ? result : "decision-error",
  );
  return false;
}

function GateDecisionFailureBox({
  failure,
}: {
  failure: NonNullable<ReturnType<typeof readAdapterDecisionFailure>>;
}) {
  return (
    <div className={decisionFailureBox} role="alert" data-testid="gate-decision-failure">
      <span className={decisionFailureLead} data-testid="gate-decision-failure-lead">
        Your previous {decisionNoun(failure.decision)} could not be applied.
      </span>
      {failure.decisionNote ? (
        <span className={decisionFailureNote} data-testid="gate-decision-failure-answer">
          {failure.decisionNote}
        </span>
      ) : null}
      <span className={decisionFailureLine} data-testid="gate-decision-failure-delivery">
        {deliveryHonesty(failure.delivery)}
      </span>
      {failure.reason ? (
        <span className={decisionFailureLine} data-testid="gate-decision-failure-reason">
          Reason: {failure.reason}
        </span>
      ) : null}
      <span className={decisionFailureLine}>Use Respond to decide again.</span>
    </div>
  );
}

function GateRequestBlock({
  state,
  resize,
}: {
  state: GateResponderState;
  resize: ReturnType<typeof useGateResize>;
}) {
  const resizeStyle = {
    "--gate-request-height": `${state.boundedRequestHeight}px`,
    "--gate-request-max-height": `${state.maxRequestHeight}px`,
  } as CSSProperties;
  return (
    <>
      <pre
        ref={state.requestRef}
        className={request}
        style={resizeStyle}
        data-testid="gate-request"
      >
        {state.fullRequest}
      </pre>
      <div
        className={resizeHandle}
        role="slider"
        aria-label="Resize gate request"
        aria-orientation="horizontal"
        aria-valuenow={state.boundedRequestHeight}
        aria-valuemin={MIN_REQUEST_HEIGHT}
        aria-valuemax={state.maxRequestHeight}
        tabIndex={0}
        onPointerDown={resize.startResize}
        onKeyDown={resize.resizeByKeyboard}
        data-testid="gate-request-resize"
      />
      {state.diagnosticsText ? (
        <details className={diagnostics}>
          <summary>Diagnostics JSON</summary>
          <pre className={diagnosticPre} data-testid="gate-request-diagnostics">
            {state.diagnosticsText}
          </pre>
        </details>
      ) : null}
    </>
  );
}

function GateRouteRow({
  state,
  onAttachActive,
}: {
  state: GateResponderState;
  onAttachActive: () => void;
}) {
  return (
    <div className={route}>
      <span data-testid="gate-route">
        {state.attachedSession
          ? `To ${state.attachedSession.label}`
          : `External inbox for ${state.lifecycleId}`}
      </span>
      {state.canAttachActive && state.activeSession ? (
        <Button
          className={respondButton}
          onPress={onAttachActive}
          data-testid="gate-respond-attach-active"
        >
          Attach {state.activeSession.label}
        </Button>
      ) : null}
    </div>
  );
}

function GateChoicesRow({
  state,
  actions,
}: {
  state: GateResponderState;
  actions: ReturnType<typeof useGateDecisionActions>;
}) {
  return (
    <div className={choices}>
      <Button
        className={respondButton}
        onPress={actions.approve}
        isDisabled={state.busy}
        data-testid="gate-respond-yes"
      >
        Yes
      </Button>
      <Button
        className={respondButton}
        onPress={() => actions.openDraft("no")}
        isDisabled={state.busy}
        data-testid="gate-respond-no"
      >
        No
      </Button>
      <Button
        className={respondButton}
        onPress={actions.dismiss}
        isDisabled={state.busy || !state.gateNode}
        data-testid="gate-respond-dismiss"
      >
        Dismiss
      </Button>
    </div>
  );
}

function GateDraftField({ state }: { state: GateResponderState }) {
  const fieldCopy = "Reason for rejection";
  if (!state.mode) return null;
  return (
    <TextField
      className={field}
      aria-label={fieldCopy}
      value={state.draft}
      onChange={state.setDraft}
    >
      <span className={fieldLabel}>{fieldCopy}</span>
      <TextArea ref={state.draftInputRef} className={area} data-testid="gate-respond-text" />
    </TextField>
  );
}

function GateFooterRow({
  state,
  onReject,
}: {
  state: GateResponderState;
  onReject: () => void;
}) {
  return (
    <div className={footer}>
      {state.note ? (
        <span
          className={statusNote}
          role={state.statusIsError ? "alert" : "status"}
          data-testid="gate-respond-status"
        >
          {state.note}
        </span>
      ) : (
        <span />
      )}
      {state.mode ? (
        <Button
          className={respondButton}
          onPress={onReject}
          isDisabled={state.busy || !state.draft.trim()}
          data-testid="gate-respond-send"
        >
          {state.busy ? "Sending..." : "Reject"}
        </Button>
      ) : null}
    </div>
  );
}

function GateDialogBody({
  state,
  actions,
  resize,
}: {
  state: GateResponderState;
  actions: ReturnType<typeof useGateDecisionActions>;
  resize: ReturnType<typeof useGateResize>;
}) {
  return (
    <Dialog aria-label="Respond to gate" className={panel} data-testid="gate-respond-dialog">
      <GateRequestBlock state={state} resize={resize} />
      <GateRouteRow state={state} onAttachActive={actions.attachActive} />
      <GateChoicesRow state={state} actions={actions} />
      <GateDraftField state={state} />
      <GateFooterRow state={state} onReject={actions.reject} />
    </Dialog>
  );
}

export function GateResponder(props: GateResponderProps) {
  const { lifecycleId, gateNode, ask, compact = false, testId = "gate-responder" } = props;
  const state = useGateResponderState(props);
  const actions = useGateDecisionActions(state, props);
  const resize = useGateResize(state);
  const {
    open,
    requestRef,
    fullRequest,
    setContentHeight,
    maxRequestHeight,
    setRequestHeight,
    mode,
    draftInputRef,
  } = state;

  useEffect(() => {
    if (!open) return;
    const element = requestRef.current;
    if (!element) return;
    const measure = () => setContentHeight(element.scrollHeight);
    measure();
    const frame = window.requestAnimationFrame(measure);
    return () => window.cancelAnimationFrame(frame);
  }, [fullRequest, open, requestRef, setContentHeight]);

  useEffect(() => {
    setRequestHeight((value) => clamp(value, MIN_REQUEST_HEIGHT, maxRequestHeight));
  }, [maxRequestHeight, setRequestHeight]);

  useEffect(() => {
    if (mode !== null) draftInputRef.current?.focus();
  }, [mode, draftInputRef]);

  void lifecycleId;
  void gateNode;
  void ask;

  return (
    <div className={cx(shell, compact ? compactShell : undefined)} data-testid={testId}>
      <div className={top}>
        <div className={title}>
          <strong>Gate</strong> · <span className={titleKind}>{state.label}</span>
        </div>
        <Button
          className={respondButton}
          onPress={() => state.setOpen((value) => !value)}
          data-testid="gate-respond-open"
        >
          Respond
        </Button>
      </div>
      {state.decisionFailure ? (
        <GateDecisionFailureBox failure={state.decisionFailure} />
      ) : null}
      {state.open ? (
        <GateDialogBody state={state} actions={actions} resize={resize} />
      ) : null}
    </div>
  );
}

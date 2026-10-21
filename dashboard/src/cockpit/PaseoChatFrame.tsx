import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { css } from "../../styled-system/css";
import {
  PaseoFrameControl,
  type PaseoControlState,
  type PaseoFrameView,
} from "./paseoFrameControl";
import {
  parseFrameDescriptor,
  requestWaitText,
  UNAVAILABLE_HEADLINE,
  type AvailablePaseoFrame,
  type PaseoAgentTarget,
  type PaseoFrameDescriptor,
  type PaseoFrameShowing,
  type PaseoOpenRequest,
  type PaseoFrameUnavailableReason,
} from "./paseoFrameModel";

const frameShell = css({
  position: "relative",
  display: "flex",
  flex: "1",
  flexDirection: "column",
  minHeight: "0",
  minWidth: "0",
  background: "var(--bg)",
});
const frameElement = css({
  display: "block",
  flex: "1",
  width: "100%",
  minHeight: "0",
  minWidth: "0",
  border: "0",
  background: "var(--bg)",
});
const frameBody = css({ position: "relative", display: "flex", flex: "1", minHeight: "0", minWidth: "0" });
// Laid over the frame, so the chat under it does not move when the line comes and goes. At the
// bottom edge: the application's header and its controls at the top stay free, and while a
// conversation loads the application shows no input at the bottom.
const frameWaiting = css({
  position: "absolute",
  bottom: "0",
  left: "0",
  right: "0",
  display: "flex",
  alignItems: "center",
  gap: "0.6rem",
  padding: "0.3rem 0.75rem",
  borderTop: "1px solid var(--grid)",
  background: "var(--bg-panel)",
  color: "muted",
  fontFamily: "var(--font-mono)",
  fontSize: "0.72rem",
});
const frameNotice = css({
  display: "flex",
  flexShrink: "0",
  alignItems: "center",
  gap: "0.6rem",
  padding: "0.3rem 0.75rem",
  borderBottom: "1px solid var(--grid)",
  background: "var(--bg-panel)",
  color: "amber",
  fontFamily: "var(--font-mono)",
  fontSize: "0.72rem",
});
const frameMessage = css({
  display: "grid",
  flex: "1",
  placeItems: "center",
  alignContent: "center",
  gap: "0.5rem",
  minHeight: "0",
  padding: "1rem",
  color: "muted",
  fontFamily: "var(--font-mono)",
  fontSize: "0.78rem",
  textAlign: "center",
});
const frameDetail = css({ maxWidth: "44rem", fontSize: "0.68rem", overflowWrap: "anywhere" });
const frameOverlay = css({
  position: "absolute",
  inset: "0",
  display: "grid",
  placeItems: "center",
  background: "var(--bg)",
  color: "muted",
  fontFamily: "var(--font-mono)",
  fontSize: "0.78rem",
});
const frameButton = css({
  paddingInline: "0.55rem",
  paddingBlock: "0.15rem",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "grid",
  borderRadius: "3px",
  background: "transparent",
  color: "muted",
  font: "inherit",
  cursor: "pointer",
  _hover: { color: "ink", borderColor: "amber" },
  _focusVisible: { outline: "1px solid token(colors.amber)", outlineOffset: "1px" },
});

function fetchFrameDescriptor(): Promise<PaseoFrameDescriptor> {
  return fetch("/api/role-launch/frame")
    .then(async (response) => parseFrameDescriptor(await response.json().catch(() => null)))
    .catch(() => parseFrameDescriptor(null));
}

export function FrameUnavailable({
  reason,
  detail,
  onRetry,
}: {
  reason: PaseoFrameUnavailableReason;
  detail: string;
  onRetry: () => void;
}) {
  return (
    <div className={frameShell}>
      <div
        role="status"
        className={frameMessage}
        data-testid="paseo-frame-unavailable"
        data-reason={reason}
      >
        <span>{UNAVAILABLE_HEADLINE[reason]}</span>
        {detail ? <span className={frameDetail}>{detail}</span> : null}
        <button type="button" className={frameButton} onClick={onRetry}>
          Retry
        </button>
      </div>
    </div>
  );
}

/**
 * The embedded Paseo chat of the Chats pane. It asks the backend where this dashboard origin
 * frames the Paseo web UI, mounts that frame once, and lets PaseoFrameControl steer it to the
 * agent of the execution the launcher bar displays. The frame is granted clipboard read and
 * write for the embedded origin only. It stays mounted while the pane is hidden.
 */
interface PaseoChatFrameProps {
  active: boolean;
  /**
   * Start the embedded client before the pane is first shown (the review page's closed chat
   * column, MIK-R89 v2 rule 12, which supersedes MIK-R122 rule 2's "created when the chat is
   * first shown"). Only the descriptor is read, and the first load names no agent while the pane
   * is not shown: the agent is asked for by message once it is. Off by default: every other pane
   * still starts on its first showing.
   */
  preload?: boolean;
  /** Identity of the launcher selection; a change means another execution is being displayed. */
  scope: string;
  target: PaseoAgentTarget | null;
  page?: "chats" | "document";
  /**
   * What the pane's owner still waits for before it can name the chat of the selection. While
   * it is set the frame keeps what it shows, out of reach, under this one line.
   */
  holding?: string | null;
  onAvailability?: (available: boolean) => void;
}

const NOTHING_SHOWN: PaseoFrameShowing = { shown: null, refused: null, landed: null, request: null };

function useFrameController({ scope, target, page }: PaseoChatFrameProps) {
  const [frame, setFrame] = useState<PaseoFrameView | null>(null);
  const [control, setControl] = useState<PaseoControlState>("waiting");
  const [agentProblem, setAgentProblem] = useState<string | null>(null);
  const [showing, setShowing] = useState<PaseoFrameShowing>(NOTHING_SHOWN);
  const [pluginNotice, setPluginNotice] = useState<string | null>(null);
  const frameRef = useRef<HTMLIFrameElement | null>(null);
  const [controller] = useState(
    () =>
      new PaseoFrameControl({
        frameWindow: () => frameRef.current?.contentWindow ?? null,
        setControl,
        setFrame,
        setAgentProblem,
        setShowing,
        setPluginNotice,
        page,
      }),
  );
  const agentId = target?.agentId;
  const workspaceId = target?.workspaceId;
  // Before the browser paints the change: the request is out with the click, and the line the
  // pane shows meanwhile is the request's own from the first frame on.
  useLayoutEffect(() => {
    controller.display(
      scope,
      agentId ? { agentId, ...(workspaceId ? { workspaceId } : {}) } : null,
    );
  }, [controller, scope, agentId, workspaceId]);
  useEffect(() => {
    const onMessage = (event: MessageEvent) => controller.receive(event);
    window.addEventListener("message", onMessage);
    return () => {
      window.removeEventListener("message", onMessage);
      controller.stop();
    };
  }, [controller]);
  return {
    frame,
    control,
    agentProblem,
    pluginNotice,
    wait: frameWait(showing, agentId, page),
    frameRef,
    controller,
  };
}

/** Why the frame is kept out of reach under one line, and whether Retry is offered with it. */
interface FrameWait {
  line: string | null;
  held: boolean;
  late: boolean;
}

const IN_REACH: FrameWait = { line: null, held: false, late: false };

/**
 * The frame is in reach when the application is on the selection's agent: it confirmed the
 * agent as shown, it said it is on the agent's chat while the conversation still loads (the
 * line then stays until the chat is shown), or its document was loaded on the agent's address,
 * which holds no earlier chat. Until then the earlier chat stays visible and takes no input.
 * An agent the frame refused is named by its own notice; on a document page the earlier chat
 * stays out of reach under it.
 */
function frameWait(showing: PaseoFrameShowing, agentId: string | undefined, page: PaseoChatFrameProps["page"]): FrameWait {
  if (!agentId || showing.shown === agentId) return IN_REACH;
  const landed = showing.landed === agentId;
  if (showing.refused === agentId) return { line: null, held: page === "document" && !landed, late: false };
  const request = showing.request?.agentId === agentId ? showing.request : null;
  if (landed && !request) return IN_REACH;
  return requestWait(request, landed);
}

/** The wait for an agent that is asked for: its line, and whether input is let through already. */
function requestWait(request: PaseoOpenRequest | null, landed: boolean): FrameWait {
  return { line: requestWaitText(request), held: !landed && request?.onChat !== true, late: request?.late === true };
}

/**
 * Fetch the embed descriptor on first activation (or the first preload) and each explicit Retry.
 * A frame started while its pane is not shown loads no agent's address.
 */
function useFrameDescriptor(active: boolean, preload: boolean, controller: PaseoFrameControl) {
  const [started, setStarted] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const [descriptor, setDescriptor] = useState<PaseoFrameDescriptor | null>(null);
  // Whether the pane is shown when the descriptor answers, read by the answer's callback.
  const shown = useRef(active);
  useEffect(() => {
    shown.current = active;
    if (active || preload) setStarted(true);
  }, [active, preload]);
  useEffect(() => {
    if (!started) return;
    let cancelled = false;
    void fetchFrameDescriptor().then((next) => {
      if (cancelled) return;
      setDescriptor(next);
      if (next.available) controller.start(next, attempt > 0, !shown.current);
      else controller.stop();
    });
    return () => {
      cancelled = true;
    };
  }, [started, attempt, controller]);
  const retry = () => {
    controller.stop();
    setDescriptor(null);
    setAttempt((current) => current + 1);
  };
  return { started, descriptor, retry };
}

export function PaseoChatFrame(props: PaseoChatFrameProps) {
  const { active, onAvailability } = props;
  const state = useFrameController(props);
  const { started, descriptor, retry } = useFrameDescriptor(active, props.preload === true, state.controller);
  useEffect(() => { onAvailability?.(descriptor?.available === true); }, [descriptor, onAvailability]);
  if (!started) return <div className={frameShell} />;
  if (descriptor && !descriptor.available)
    return (
      <FrameUnavailable reason={descriptor.reason} detail={descriptor.detail} onRetry={retry} />
    );
  if (!descriptor || !state.frame) return <FrameLoading />;
  const wait = props.holding ? { line: props.holding, held: true, late: false } : state.wait;
  return (
    <EmbeddedFrame
      available={descriptor}
      frame={state.frame}
      frameRef={state.frameRef}
      control={state.control}
      agentProblem={state.agentProblem}
      pluginNotice={state.pluginNotice}
      wait={wait}
      onRetry={retry}
    />
  );
}

function FrameLoading() {
  return (
    <div className={frameShell}>
      <div role="status" className={frameMessage}>
        Loading embedded chat…
      </div>
    </div>
  );
}

interface EmbeddedFrameProps {
  available: AvailablePaseoFrame;
  frame: PaseoFrameView;
  frameRef: React.RefObject<HTMLIFrameElement | null>;
  control: PaseoControlState;
  agentProblem: string | null;
  pluginNotice: string | null;
  wait: FrameWait;
  onRetry: () => void;
}

function EmbeddedFrame(props: EmbeddedFrameProps) {
  const {
    available,
    frame,
    frameRef,
    control,
    wait,
  } = props;
  // A chat that goes out of reach while the keyboard is in it gives the keyboard back.
  useEffect(() => {
    if (wait.held && document.activeElement === frameRef.current) frameRef.current?.blur();
  }, [wait.held, frameRef]);
  return (
    <div
      className={frameShell}
      data-testid="paseo-frame"
      data-control={control}
      /* MIK-R122 rule 7: the generation rises by exactly one per document load, so tests
         can read the number of loads. Only load() bumps it; message switches never do. */
      data-generation={frame.generation}
    >
      <FrameNotices {...props} />
      <div className={frameBody}>
        <iframe
          key={frame.generation}
          ref={frameRef}
          title="Role chats"
          src={frame.src}
          referrerPolicy="origin"
          allow={
            "clipboard-read " + available.frameOrigin + "; clipboard-write " + available.frameOrigin
          }
          className={frameElement}
          /* The earlier chat stays visible while another is asked for, and takes
             no pointer and no keyboard until the application is on the selection's own chat. */
          inert={wait.held}
          style={wait.held ? { pointerEvents: "none" } : undefined}
        />
        {wait.line && control !== "waiting" ? (
          <div role="status" className={frameWaiting} data-testid="paseo-frame-waiting">
            <span>{wait.line}</span>
            {wait.late ? (
              <button type="button" className={frameButton} onClick={props.onRetry}>
                Retry
              </button>
            ) : null}
          </div>
        ) : null}
      </div>
      {control === "waiting" ? (
        <div role="status" className={frameOverlay} data-testid="paseo-frame-connecting">
          Connecting embedded chat…
        </div>
      ) : null}
    </div>
  );
}

function FrameNotices({
  available,
  control,
  agentProblem,
  pluginNotice,
  onRetry,
}: EmbeddedFrameProps) {
  return (
    <>
      {control === "unavailable" ? (
        <div role="status" className={frameNotice} data-testid="paseo-frame-control-banner">
          <span>embedded chat control unavailable</span>
          <button type="button" className={frameButton} onClick={onRetry}>
            Retry
          </button>
        </div>
      ) : null}
      {agentProblem ? (
        <div role="status" className={frameNotice} data-testid="paseo-frame-agent-problem">
          {agentProblem}
        </div>
      ) : null}
      {pluginNotice ? (
        <div role="status" className={frameNotice} data-testid="paseo-frame-plugin-notice">
          {pluginNotice}
        </div>
      ) : null}
      {available.projectsWorkspaceDetail ? (
        <div role="status" className={frameNotice} data-testid="paseo-frame-workspace-problem">
          The Projects workspace could not be opened: {available.projectsWorkspaceDetail}
        </div>
      ) : null}
    </>
  );
}

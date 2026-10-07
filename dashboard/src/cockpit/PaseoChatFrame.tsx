import { useEffect, useRef, useState } from "react";
import { css } from "../../styled-system/css";
import {
  PaseoFrameControl,
  type PaseoControlState,
  type PaseoFrameView,
} from "./paseoFrameControl";
import {
  parseFrameDescriptor,
  type AvailablePaseoFrame,
  type PaseoAgentTarget,
  type PaseoFrameDescriptor,
  type PaseoFrameUnavailableReason,
} from "./paseoFrameModel";

const UNAVAILABLE_HEADLINE: Record<PaseoFrameUnavailableReason, string> = {
  "not-configured": "no Paseo runtime configured",
  unreachable: "Paseo daemon unreachable",
  "origin-not-listed": "embedded chat is not configured for this address",
  backend: "the dashboard backend did not answer",
};

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
const frameBody = css({ display: "flex", flex: "1", minHeight: "0", minWidth: "0" });
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

function FrameUnavailable({
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
  /** Identity of the launcher selection; a change means another execution is being displayed. */
  scope: string;
  target: PaseoAgentTarget | null;
}

function useFrameController(scope: string, target: PaseoAgentTarget | null) {
  const [frame, setFrame] = useState<PaseoFrameView | null>(null);
  const [control, setControl] = useState<PaseoControlState>("waiting");
  const [agentProblem, setAgentProblem] = useState<string | null>(null);
  const frameRef = useRef<HTMLIFrameElement | null>(null);
  const [controller] = useState(
    () =>
      new PaseoFrameControl({
        frameWindow: () => frameRef.current?.contentWindow ?? null,
        setControl,
        setFrame,
        setAgentProblem,
      }),
  );
  const agentId = target?.agentId;
  const workspaceId = target?.workspaceId;
  useEffect(() => {
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
    frameRef,
    controller,
  };
}

/** Fetch the embed descriptor on first activation and each explicit Retry. */
function useFrameDescriptor(active: boolean, controller: PaseoFrameControl) {
  const [started, setStarted] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const [descriptor, setDescriptor] = useState<PaseoFrameDescriptor | null>(null);
  useEffect(() => {
    if (active) setStarted(true);
  }, [active]);
  useEffect(() => {
    if (!started) return;
    let cancelled = false;
    void fetchFrameDescriptor().then((next) => {
      if (cancelled) return;
      setDescriptor(next);
      if (next.available) controller.start(next, attempt > 0);
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

export function PaseoChatFrame({
  active,
  scope,
  target,
}: PaseoChatFrameProps) {
  const state = useFrameController(scope, target);
  const { started, descriptor, retry } = useFrameDescriptor(active, state.controller);
  if (!started) return <div className={frameShell} />;
  if (descriptor && !descriptor.available)
    return (
      <FrameUnavailable reason={descriptor.reason} detail={descriptor.detail} onRetry={retry} />
    );
  if (!descriptor || !state.frame) return <FrameLoading />;
  return (
    <EmbeddedFrame
      available={descriptor}
      frame={state.frame}
      frameRef={state.frameRef}
      control={state.control}
      agentProblem={state.agentProblem}
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
  onRetry: () => void;
}

function EmbeddedFrame(props: EmbeddedFrameProps) {
  const {
    available,
    frame,
    frameRef,
    control,
  } = props;
  return (
    <div className={frameShell} data-testid="paseo-frame" data-control={control}>
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
        />
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
      {available.projectsWorkspaceDetail ? (
        <div role="status" className={frameNotice} data-testid="paseo-frame-workspace-problem">
          The Projects workspace could not be opened: {available.projectsWorkspaceDetail}
        </div>
      ) : null}
    </>
  );
}

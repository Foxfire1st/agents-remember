import type { RefObject } from "react";
import { Button } from "react-aria-components";

import {
  serverConfirmedQueued,
  type SubmitRecord,
} from "../data/submitMachine";
import {
  INTERACTION_ANSWERED,
  INTERACTION_ANSWERING,
  INTERACTION_COMPOSER_MODE,
  STOP_TURN_DISABLED_REASON,
} from "./session-cockpit/lifecycleCopy";
import type { ConversationInterrupt } from "./session-cockpit/conversation/useConversationControls";
import {
  dock,
  editorFrame,
  error,
  footer,
  footerLeft,
  recoveryText,
  secondaryButton,
  sendButton,
  status,
  stopButtonDisabled,
  stopButtonEnabled,
} from "./sessionComposerStyles";
import { QueuePreview } from "./session-cockpit/QueuePreview";
import type { OpenSession } from "../data/sessions";
import type {
  useComposerInteraction,
  useComposerStore,
} from "./sessionComposerHooks";

export function ComposerFrame({
  frameRef,
  answerMode,
  editable,
  profile,
}: {
  frameRef: RefObject<HTMLDivElement | null>;
  answerMode: boolean;
  editable: boolean;
  profile: string;
}) {
  return (
    <div
      ref={frameRef}
      className={editorFrame}
      data-kbzone="composer"
      data-answer-mode={answerMode ? "true" : undefined}
      data-disabled={!editable ? "true" : undefined}
      data-composer-profile={profile}
      data-testid="session-composer-editor"
    />
  );
}

export function AnswerModeRow({
  matchingAnswerState,
}: {
  matchingAnswerState:
    | { inflight?: boolean; answeredAt?: number; error?: string }
    | undefined;
}) {
  return (
    <div
      className={status}
      role={matchingAnswerState?.error ? "alert" : "status"}
      data-testid="session-composer-answer-mode"
    >
      <span>{INTERACTION_COMPOSER_MODE}</span>
      {matchingAnswerState?.inflight ? (
        <span>{INTERACTION_ANSWERING}</span>
      ) : null}
      {matchingAnswerState?.answeredAt !== undefined ? (
        <span>{INTERACTION_ANSWERED}</span>
      ) : null}
      {matchingAnswerState?.error ? (
        <span className={error}>{matchingAnswerState.error}</span>
      ) : null}
    </div>
  );
}

export function GateNotice({
  reason,
}: {
  reason: string | undefined;
}) {
  return (
    <div className={status} role="status" data-testid="session-composer-gate">
      {reason}
    </div>
  );
}

function WithdrawalRecovery({
  withdrawalRecovery,
  onRecover,
  onKeep,
}: {
  withdrawalRecovery: { text: string };
  onRecover: () => void;
  onKeep: () => void;
}) {
  return (
    <>
      <span data-testid="withdrawn-recovery">
        newer draft preserved · withdrawn message retained
      </span>
      <span className={recoveryText} data-testid="withdrawn-recovery-text">
        {withdrawalRecovery.text}
      </span>
      <button
        type="button"
        className={secondaryButton}
        data-testid="withdrawn-recovery-replace"
        onClick={onRecover}
      >
        replace current draft with withdrawn text
      </button>
      <button
        type="button"
        className={secondaryButton}
        data-testid="withdrawn-recovery-keep-current"
        onClick={onKeep}
      >
        keep current draft
      </button>
    </>
  );
}

function RouteErrorRow({
  latest,
  onRetry,
}: {
  latest: SubmitRecord;
  onRetry: () => void;
}) {
  return (
    <>
      <span className={error}>{latest.detail ?? "submit route failed"}</span>
      <button
        type="button"
        className={secondaryButton}
        onClick={onRetry}
      >
        retry same id
      </button>
    </>
  );
}

function EndgameRow({
  onKeepWaiting,
  onRelease,
  onCopyRequestId,
}: {
  onKeepWaiting: () => void;
  onRelease: () => void;
  onCopyRequestId: () => void;
}) {
  return (
    <>
      <span className={error}>still unresolved</span>
      <button type="button" className={secondaryButton} onClick={onKeepWaiting}>
        keep waiting
      </button>
      <button type="button" className={secondaryButton} onClick={onCopyRequestId}>
        copy requestId
      </button>
      <button type="button" className={secondaryButton} onClick={onRelease}>
        release draft
      </button>
    </>
  );
}

function StatusSimpleRow({ latest }: { latest: SubmitRecord }) {
  if (latest.phase === "sending") return <span>sending…</span>;
  if (latest.phase === "accepted") {
    return (
      <span>
        delivered ·{" "}
        {latest.clearDraftOnAccept
          ? "draft released"
          : "composer draft unchanged"}
      </span>
    );
  }
  if (latest.phase === "queued") {
    return (
      <span>
        {serverConfirmedQueued(latest) ? "queued · withdrawable" : "queued"} ·{" "}
        {latest.clearDraftOnAccept
          ? "draft released"
          : "composer draft unchanged"}
      </span>
    );
  }
  if (latest.phase === "delivering") return <span>delivering…</span>;
  if (latest.phase === "withdrawn") {
    return (
      <span>
        {latest.recoveryDismissedAt === undefined
          ? "withdrawn before dispatch"
          : "withdrawn before dispatch · current draft kept; withdrawn text dismissed"}
      </span>
    );
  }
  return null;
}

function StatusErrorRow({
  latest,
  onRetry,
  onKeepWaiting,
  onRelease,
  onCopyRequestId,
}: {
  latest: SubmitRecord;
  onRetry: () => void;
  onKeepWaiting: () => void;
  onRelease: () => void;
  onCopyRequestId: () => void;
}) {
  if (latest.phase === "generation-lost") {
    return (
      <span className={error}>
        runner generation changed · retained text was not resent
      </span>
    );
  }
  if (latest.phase === "not-found") {
    return (
      <span className={error}>
        submission not retained by this authority · withdrawal unavailable · draft was not
        restored
      </span>
    );
  }
  if (latest.phase === "ambiguous" || latest.phase === "reconciling") {
    return <span>ambiguous · reconciling the same requestId…</span>;
  }
  if (latest.phase === "rejected" || latest.phase === "unsupported") {
    return <span className={error}>{latest.detail ?? latest.phase}</span>;
  }
  if (latest.phase === "route-error") {
    return <RouteErrorRow latest={latest} onRetry={onRetry} />;
  }
  if (latest.phase === "endgame") {
    return (
      <EndgameRow
        onKeepWaiting={onKeepWaiting}
        onRelease={onRelease}
        onCopyRequestId={onCopyRequestId}
      />
    );
  }
  return null;
}

export function ComposerStatus({
  notice,
  latest,
  withdrawalRecovery,
  onRetry,
  onRecover,
  onKeep,
  onKeepWaiting,
  onRelease,
  onCopyRequestId,
}: {
  notice: string | null;
  latest: SubmitRecord | undefined;
  withdrawalRecovery: { text: string } | undefined;
  onRetry: () => void;
  onRecover: () => void;
  onKeep: () => void;
  onKeepWaiting: () => void;
  onRelease: () => void;
  onCopyRequestId: () => void;
}) {
  if (!latest && !notice && !withdrawalRecovery) return null;
  const alertPhase =
    latest !== undefined &&
    ["rejected", "route-error", "generation-lost", "not-found"].includes(
      latest.phase,
    );
  return (
    <div
      className={status}
      role={alertPhase ? "alert" : undefined}
      data-testid="session-composer-status"
    >
      {notice ? <span>{notice}</span> : null}
      {latest ? <StatusSimpleRow latest={latest} /> : null}
      {withdrawalRecovery ? (
        <WithdrawalRecovery
          withdrawalRecovery={withdrawalRecovery}
          onRecover={onRecover}
          onKeep={onKeep}
        />
      ) : null}
      {latest ? (
        <StatusErrorRow
          latest={latest}
          onRetry={onRetry}
          onKeepWaiting={onKeepWaiting}
          onRelease={onRelease}
          onCopyRequestId={onCopyRequestId}
        />
      ) : null}
    </div>
  );
}

function DisabledStop({ reason }: { reason: string }) {
  return (
    <button
      type="button"
      className={stopButtonDisabled}
      disabled
      title={reason}
      aria-label={`Stop turn — ${reason}`}
      data-disabled-reason={reason}
      data-testid="session-composer-stop"
    >
      ⏹ stop
    </button>
  );
}

function StopControl({
  interrupt,
}: {
  interrupt: ConversationInterrupt | undefined;
}) {
  if (!interrupt?.available || interrupt.onStop === undefined) {
    return <DisabledStop reason={interrupt?.reason ?? STOP_TURN_DISABLED_REASON} />;
  }
  return (
    <button
      type="button"
      className={stopButtonEnabled}
      onClick={interrupt.onStop}
      disabled={interrupt.pending}
      title={
        interrupt.pending
          ? "interrupt requested…"
          : `Stop the current turn · ${interrupt.keyshortcut}`
      }
      aria-label="Stop turn"
      aria-keyshortcuts={interrupt.keyshortcut}
      data-testid="session-composer-stop"
    >
      ⏹ stop
    </button>
  );
}

export function ComposerFooter({
  footerHint,
  capabilityDetail,
  turnWorking,
  interrupt,
  sendDisabled,
  keyLabel,
  answerMode,
  onSend,
}: {
  footerHint: string;
  capabilityDetail: string;
  turnWorking: boolean;
  interrupt: ConversationInterrupt | undefined;
  sendDisabled: boolean;
  keyLabel: string;
  answerMode: boolean;
  onSend: () => void;
}) {
  return (
    <div className={footer}>
      <span
        className={footerLeft}
        title={capabilityDetail}
        data-testid="composer-reliability-note"
      >
        {footerHint}
      </span>
      {/* The stop control sits beside send while a turn works —
          the working line stays a pure status cue. Same welded-evidence gating:
          enabled only with real turn + capability evidence, else the honest reason. */}
      {turnWorking ? <StopControl interrupt={interrupt} /> : null}
      <Button
        className={sendButton}
        isDisabled={sendDisabled}
        onPress={onSend}
        data-testid="session-composer-send"
      >
        {answerMode ? "send answer" : `${keyLabel} send`}
      </Button>
    </div>
  );
}

export interface ComposerViewData {
  footerHint: string;
  capabilityDetail: string;
  keyLabel: string;
  sendDisabled: boolean;
}

export function ComposerView({
  session,
  store,
  interaction,
  notice,
  frameRef,
  view,
  interrupt,
  turnWorking,
  profile,
  onSend,
  onRetry,
  onRecover,
  onKeep,
  onKeepWaiting,
  onRelease,
  onCopyRequestId,
}: {
  session: OpenSession;
  store: ReturnType<typeof useComposerStore>;
  interaction: ReturnType<typeof useComposerInteraction>;
  notice: string | null;
  frameRef: RefObject<HTMLDivElement | null>;
  view: ComposerViewData;
  interrupt: ConversationInterrupt | undefined;
  turnWorking: boolean;
  profile: string;
  onSend: () => void;
  onRetry: () => void;
  onRecover: () => void;
  onKeep: () => void;
  onKeepWaiting: () => void;
  onRelease: () => void;
  onCopyRequestId: () => void;
}) {
  return (
    <div className={dock} data-testid="session-composer">
      <QueuePreview queue={store.confirmedQueue} sessionId={session.id} />
      <ComposerFrame
        frameRef={frameRef}
        answerMode={interaction.answerMode}
        editable={store.gate.editable}
        profile={profile}
      />
      {interaction.answerMode ? (
        <AnswerModeRow matchingAnswerState={interaction.matchingAnswerState} />
      ) : !store.gate.ready ? (
        <GateNotice reason={store.gate.reason} />
      ) : null}
      <ComposerStatus
        notice={notice}
        latest={store.latest}
        withdrawalRecovery={store.withdrawalRecovery}
        onRetry={onRetry}
        onRecover={onRecover}
        onKeep={onKeep}
        onKeepWaiting={onKeepWaiting}
        onRelease={onRelease}
        onCopyRequestId={onCopyRequestId}
      />
      <ComposerFooter
        footerHint={view.footerHint}
        capabilityDetail={view.capabilityDetail}
        turnWorking={turnWorking}
        interrupt={interrupt}
        sendDisabled={view.sendDisabled}
        keyLabel={view.keyLabel}
        answerMode={interaction.answerMode}
        onSend={onSend}
      />
    </div>
  );
}

import type { ActiveConversationProjection } from "../../../data/conversation/reducer";
import {
  loadOlderConversation,
  type ConversationScrollMemory,
} from "../../../data/conversation/store";
import { thinkingPreferenceStore } from "../../../data/conversation/thinkingPreference";
import type {
  ConversationCapabilities,
  ConversationItem,
  ConversationProcessState,
  ConversationRouteError,
  FeatureCapability,
} from "../../../data/conversation/types";
import { AmbientTelemetry } from "./AmbientTelemetry";
import { CapabilityReason } from "./primitives";
import { ConversationReconnect } from "./ConversationReconnect";
import { ConversationTimeline } from "./conversation-timeline/ConversationTimeline";
import { ConversationWelcome } from "./ConversationWelcome";
import {
  agentFocusNote,
  agentHistoryError,
  surface,
  toggle,
  toolbar,
} from "./conversationSurfaceStyles";

export function ProjectionFailedSurface({
  routeError,
  onRetry,
  onShowDiagnostics,
}: {
  routeError: ConversationRouteError | null | undefined;
  onRetry: () => void;
  onShowDiagnostics: () => void;
}) {
  return (
    <div className={surface} data-testid="conversation-surface">
      <ConversationReconnect
        phase={routeError != null ? "projection-failed" : "connecting"}
        reason={routeError?.detail}
        onRetry={onRetry}
        onShowDiagnostics={onShowDiagnostics}
      />
    </div>
  );
}

export function SurfaceToolbar({
  hideThinking,
  onShowDiagnostics,
  sessionId,
  projection,
  visible,
  historyCapability,
}: {
  hideThinking: boolean;
  onShowDiagnostics: () => void;
  sessionId: string;
  projection: ActiveConversationProjection;
  visible: boolean;
  historyCapability: FeatureCapability | null;
}) {
  return (
    <div className={toolbar}>
      <button
        type="button"
        className={toggle}
        aria-pressed={hideThinking}
        onClick={() => thinkingPreferenceStore.getState().toggle()}
        data-testid="thinking-toggle"
      >
        {hideThinking ? "show thinking" : "hide thinking"}
      </button>
      <button
        type="button"
        className={toggle}
        onClick={onShowDiagnostics}
        data-testid="open-terminal-diagnostics"
      >
        terminal diagnostics
      </button>
      <AmbientTelemetry
        sessionId={sessionId}
        epoch={projection.identity.bridgeEpoch}
        statusRevision={projection.status?.revision}
        active={visible}
      />
      {projection.capabilities?.live.completeness.state !== undefined &&
      projection.capabilities.live.completeness.state !== "supported" ? (
        <CapabilityReason
          capability={projection.capabilities.live.completeness}
          label="live"
        />
      ) : null}
      {historyCapability !== null ? (
        <span data-testid="history-completeness-note">
          <CapabilityReason capability={historyCapability} label="history" />
        </span>
      ) : null}
    </div>
  );
}

export function AgentHistoryErrorBanner({
  detail,
  onRetry,
}: {
  detail: string | undefined;
  onRetry?: () => void;
}) {
  if (detail === undefined || onRetry === undefined) return null;
  return (
    <div
      className={agentHistoryError}
      role="status"
      data-testid="conversation-agent-history-error"
    >
      <span>{detail}</span>
      <button
        type="button"
        className={toggle}
        onClick={onRetry}
        data-testid="conversation-agent-history-retry"
      >
        retry child
      </button>
    </div>
  );
}

export function TimelineSection({
  focusedItems,
  totalItems,
  hasOlder,
  stream,
  agentFocus,
  focusedAgentLabel,
  harnessId,
  processState,
  sessionId,
  scrollGeometryActive,
  scrollMemory,
  onScrollMemory,
}: {
  focusedItems: ConversationItem[];
  totalItems: number | undefined;
  hasOlder: boolean;
  stream: ActiveConversationProjection["stream"];
  agentFocus: string | null;
  focusedAgentLabel: string | undefined;
  harnessId: string;
  processState: ConversationProcessState | undefined;
  sessionId: string;
  scrollGeometryActive: boolean;
  scrollMemory: ConversationScrollMemory | undefined;
  onScrollMemory: (memory: ConversationScrollMemory) => void;
}) {
  const busy = stream === "connecting" || stream === "gap";
  const emptyNote =
    focusedItems.length === 0 && stream === "live"
      ? agentFocus === null
        ? (
            <ConversationWelcome harness={harnessId} processState={processState} />
          )
        : (
            <span className={agentFocusNote} data-testid="conversation-agent-empty">
              no evidence from {focusedAgentLabel ?? "this agent"} yet
            </span>
          )
      : undefined;
  return (
    <ConversationTimeline
      items={focusedItems}
      totalItems={agentFocus === null ? totalItems : undefined}
      hasOlder={hasOlder}
      busy={busy}
      emptyNote={emptyNote}
      onLoadOlder={() => void loadOlderConversation(sessionId)}
      visible={scrollGeometryActive}
      restoreScroll={scrollMemory}
      onScrollMemory={onScrollMemory}
      measurementCacheId={sessionId}
    />
  );
}

export function resolveHistoryCapability(
  history: ConversationCapabilities["history"] | undefined,
): FeatureCapability | null {
  if (history === undefined) return null;
  if (history.toolCompleteness.state !== "supported") {
    return history.toolCompleteness;
  }
  if (history.completeness.state !== "supported") return history.completeness;
  return null;
}

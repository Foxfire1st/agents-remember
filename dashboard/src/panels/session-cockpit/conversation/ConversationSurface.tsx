// The active-conversation surface (design §12.1): page/stream state + scroll shell around the one
// role="feed" timeline. It reads the reconstructable store (never a fixture authority), renders the
// honest reconnect/failure states, drives revision-keyed announcers that stay SILENT during
// replay/hydration (§14.2), and exposes the global thinking toggle + a single non-dismissable
// history-completeness note (§10.2). It owns no data/paging/cursor logic — the store/reducer do.

import { useEffect, useMemo, useRef } from "react";

import { css } from "../../../../styled-system/css";
import { announceAssertive, announcePolite } from "../../../data/announcer";
import {
  activeConversationStore,
  loadOlderConversation,
  useActiveConversation,
} from "../../../data/conversation/store";
import { thinkingPreferenceStore, useHideThinking } from "../../../data/conversation/thinkingPreference";
import type { ConversationItem } from "../../../data/conversation/types";
import { AmbientTelemetry } from "./AmbientTelemetry";
import { CapabilityReason } from "./primitives";
import { ConversationReconnect } from "./ConversationReconnect";
import { ConversationTimeline } from "./ConversationTimeline";

const surface = css({ display: "flex", flexDirection: "column", flex: "1", minHeight: "0", minWidth: "0", gap: "0.3rem" });
const toolbar = css({
  display: "flex",
  alignItems: "center",
  gap: "0.5rem",
  flexWrap: "wrap",
  flexShrink: 0,
  fontSize: "0.66rem",
  color: "muted",
});
const toggle = css({
  font: "inherit",
  fontSize: "0.66rem",
  color: "muted",
  background: "transparent",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "grid",
  borderRadius: "2px",
  paddingInline: "0.4rem",
  cursor: "pointer",
  _hover: { color: "amber", borderColor: "amber" },
  _focusVisible: { outline: "1px solid token(colors.amber)", outlineOffset: "1px" },
});
const empty = css({ color: "muted", fontSize: "0.76rem", padding: "0.6rem 0.2rem" });

export function ConversationSurface({
  sessionId,
  onRetry,
  onShowDiagnostics,
}: {
  sessionId: string;
  onRetry: () => void;
  onShowDiagnostics: () => void;
}) {
  const projection = useActiveConversation((state) => state.bySession[sessionId]);
  const routeError = useActiveConversation((state) => state.errorBySession[sessionId]);
  const hideThinking = useHideThinking();
  const lastTurnKey = useRef<string | null>(null);
  const lastProcessKey = useRef<string | null>(null);
  const lastStreamNoteRef = useRef<string | null>(null);

  const items = useMemo<ConversationItem[]>(
    () =>
      projection === undefined
        ? []
        : projection.orderedItemIds.map((id) => projection.itemsById[id]),
    [projection],
  );

  // Announcers key on (state + revision) and are suppressed for non-live delivery (hydration/replay).
  useEffect(() => {
    const status = projection?.status;
    if (status === undefined) return;
    // Hydration/re-page updates the store WITHOUT announcing (§14.5, F21): only a `live`-delivered
    // event may voice a transition. `undefined` (fresh hydration, no event yet) is treated non-live.
    const live = projection?.lastAppliedDelivery === "live";
    const turnKey = `${status.turn.state}:${status.revision}`;
    if (turnKey !== lastTurnKey.current) {
      const previouslyKnown = lastTurnKey.current !== null;
      lastTurnKey.current = turnKey;
      if (live && previouslyKnown) {
        if (status.turn.state === "failed") announceAssertive("turn failed");
        else if (status.turn.state === "ready") announcePolite("response complete");
      }
    }
    const processKey = `${status.process.state}:${status.revision}`;
    if (processKey !== lastProcessKey.current) {
      const previouslyKnown = lastProcessKey.current !== null;
      lastProcessKey.current = processKey;
      if (live && previouslyKnown && status.process.state === "disconnected") {
        announceAssertive("process disconnected");
      }
    }
  }, [projection]);

  // Reconnect/gap outcomes are polite, announced once per phase transition.
  useEffect(() => {
    const phase = projection?.stream;
    if (phase === undefined) return;
    if (phase === lastStreamNoteRef.current) return;
    lastStreamNoteRef.current = phase;
    if (phase === "reconnecting") announcePolite("reconnecting");
    else if (phase === "gap") announcePolite("re-syncing history");
    else if (phase === "live" && lastStreamNoteRef.current !== null) {
      /* going live after a drop is implicit; no utterance needed */
    }
  }, [projection?.stream]);

  if (projection === undefined) {
    // A first-connect page failure has no projection yet; surface the typed reason honestly (F15).
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

  // R11 — the offending history capability (tool details first, then overall completeness) drives a
  // short cue; its full reason lives in the cue's hover disclosure, not an always-visible paragraph.
  const history = projection.capabilities?.history;
  const historyCapability =
    history !== undefined
      ? history.toolCompleteness.state !== "supported"
        ? history.toolCompleteness
        : history.completeness.state !== "supported"
          ? history.completeness
          : null
      : null;

  return (
    <div className={surface} data-testid="conversation-surface">
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
        />
        {projection.capabilities?.live.completeness.state !== undefined &&
        projection.capabilities.live.completeness.state !== "supported" ? (
          <CapabilityReason capability={projection.capabilities.live.completeness} label="live" />
        ) : null}
        {historyCapability !== null ? (
          <span data-testid="history-completeness-note">
            <CapabilityReason capability={historyCapability} label="history" />
          </span>
        ) : null}
      </div>
      <ConversationReconnect
        phase={projection.stream}
        reason={projection.stream === "projection-failed" ? routeError?.detail : undefined}
        onRetry={onRetry}
        onShowDiagnostics={onShowDiagnostics}
      />
      {items.length === 0 && projection.stream === "live" ? (
        <div className={empty} data-testid="conversation-empty">
          No messages yet — send one from the composer below.
        </div>
      ) : (
        <ConversationTimeline
          items={items}
          totalItems={projection.totalItems}
          hasOlder={projection.hasOlder}
          busy={projection.stream === "connecting" || projection.stream === "gap"}
          onLoadOlder={() => void loadOlderConversation(sessionId)}
          onScrollAnchor={(anchor) =>
            activeConversationStore.getState().setScrollAnchor(sessionId, anchor)
          }
        />
      )}
    </div>
  );
}

// The active-conversation surface (design §12.1): page/stream state + scroll shell around the one
// role="feed" timeline. It reads the reconstructable store (never a fixture authority), renders the
// honest reconnect/failure states, drives revision-keyed announcers that stay SILENT during
// replay/hydration (§14.2), and exposes the global thinking toggle + a single non-dismissable
// history-completeness note (§10.2). It owns no data/paging/cursor logic — the store/reducer do.

import { useCallback, useEffect, useMemo, useRef } from "react";

import { css } from "../../../../styled-system/css";
import { announceAssertive, announcePolite } from "../../../data/announcer";
import {
  cycleAgentFocus,
  deriveAgents,
  effectiveAgentFocus,
  filterItemsForFocus,
} from "../../../data/conversation/agents";
import {
  activeConversationStore,
  loadOlderConversation,
  readConversationScroll,
  rememberConversationScroll,
  useActiveConversation,
  type ConversationScrollMemory,
} from "../../../data/conversation/store";
import { thinkingPreferenceStore, useHideThinking } from "../../../data/conversation/thinkingPreference";
import type { ConversationItem } from "../../../data/conversation/types";
import { AgentsArea } from "./AgentsArea";
import { AmbientTelemetry } from "./AmbientTelemetry";
import { CapabilityReason } from "./primitives";
import { ConversationReconnect } from "./ConversationReconnect";
import { ConversationTimeline } from "./ConversationTimeline";
import { ConversationWelcome } from "./ConversationWelcome";

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
const agentFocusBar = css({
  flexShrink: 0,
  display: "flex",
  alignItems: "baseline",
  gap: "0.5rem",
  minWidth: "0",
});
const agentFocusNote = css({
  fontSize: "0.66rem",
  color: "muted",
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
  minWidth: "0",
});

// The surface-level agent focus keys (the Claude Code agents-view precedent):
// ArrowLeft/ArrowRight cycle parent → agent 1 → … → agent N → parent; Escape returns to the
// parent. Editable/interactive targets own their keys (the composer, buttons, labeled overflow
// regions, code blocks) — the same exclusion discipline the feed's own navigation uses.
function ownsAgentFocusKeys(target: HTMLElement): boolean {
  if (
    target.tagName === "INPUT" ||
    target.tagName === "TEXTAREA" ||
    target.tagName === "SELECT" ||
    target.isContentEditable
  ) {
    return true;
  }
  return target.closest("button, a, pre, [role='group'], .cm-editor") !== null;
}

export function ConversationSurface({
  sessionId,
  onRetry,
  onShowDiagnostics,
  visible = true,
  scrollGeometryActive = true,
}: {
  sessionId: string;
  onRetry: () => void;
  onShowDiagnostics: () => void;
  /** False while the surface is kept mounted-but-hidden behind another chat (ChatsStageBody
      keep-alive pool). The projection keeps updating (data layer stays warm), the refs below keep
      TRACKING state — so a re-show never voices a stale transition — but nothing is announced to
      the global live regions from a chat the operator is not looking at. */
  visible?: boolean;
  /** False only when an ancestor removes the timeline's layout box (`display:none`: a cockpit
      view switch or the in-stage library). A keep-alive surface hidden with `visibility:hidden`
      keeps honest geometry and stays true here, so focus switches never arm scroll restoration.
      Deliberately separate from operator `visible`, which owns announcer semantics above. */
  scrollGeometryActive?: boolean;
}) {
  const projection = useActiveConversation((state) => state.bySession[sessionId]);
  const routeError = useActiveConversation((state) => state.errorBySession[sessionId]);
  const hideThinking = useHideThinking();
  const lastTurnKey = useRef<string | null>(null);
  const lastProcessKey = useRef<string | null>(null);
  const lastStreamNoteRef = useRef<string | null>(null);
  // The remembered position, re-read at every render so a re-show commit always carries the
  // latest scroll event's value (the map only changes via scrolls, which a hidden timeline never
  // sees — so the value read on the hide→show flip is exactly where the operator left it).
  const scrollMemory = readConversationScroll(sessionId);
  const handleScrollMemory = useCallback(
    (memory: ConversationScrollMemory) => rememberConversationScroll(sessionId, memory),
    [sessionId],
  );

  const items = useMemo<ConversationItem[]>(
    () =>
      projection === undefined
        ? []
        : projection.orderedItemIds.map((id) => projection.itemsById[id]),
    [projection],
  );

  // ── Sub-agent focus ──────────────────────────────────────────────────────────────────────────
  // The stored focus survives LRU eviction by design; it is NEVER applied blindly — the effective
  // focus is recomputed against the live roster, so a rehydrated projection without that agent
  // honestly falls back to the parent conversation.
  const storedAgentFocus = useActiveConversation((state) => state.agentFocusBySession[sessionId]);
  const agents = useMemo(() => deriveAgents(items), [items]);
  const agentFocus = effectiveAgentFocus(storedAgentFocus, agents);
  const focusedAgent = agentFocus === null ? undefined : agents.find((a) => a.agentId === agentFocus);
  const focusedItems = useMemo(
    () => filterItemsForFocus(items, agentFocus),
    [items, agentFocus],
  );

  const applyAgentFocus = useCallback(
    (next: string | null) => {
      activeConversationStore.getState().setAgentFocus(sessionId, next);
      // The switch is polite and keyed to the operator's own action; a hidden keep-alive surface
      // never voices it (the surface's announcer discipline).
      if (!visible) return;
      if (next === null) {
        announcePolite("viewing parent conversation");
      } else {
        const label = agents.find((agent) => agent.agentId === next)?.label ?? "agent";
        announcePolite(`viewing ${label}`);
      }
    },
    [agents, sessionId, visible],
  );

  const onSurfaceKeyDown = useCallback(
    (event: React.KeyboardEvent<HTMLDivElement>) => {
      const key = event.key;
      if (key !== "ArrowLeft" && key !== "ArrowRight" && key !== "Escape") return;
      if (ownsAgentFocusKeys(event.target as HTMLElement)) return;
      if (key === "Escape") {
        if (agentFocus === null) return;
        event.preventDefault();
        applyAgentFocus(null);
        return;
      }
      if (agents.length === 0) return;
      event.preventDefault();
      applyAgentFocus(
        cycleAgentFocus(
          agentFocus,
          agents.map((agent) => agent.agentId),
          key === "ArrowRight" ? 1 : -1,
        ),
      );
    },
    [agentFocus, agents, applyAgentFocus],
  );

  // Announcers key on (state + revision) and are suppressed for non-live delivery (hydration/replay).
  useEffect(() => {
    const status = projection?.status;
    if (status === undefined) return;
    // Hydration/re-page updates the store WITHOUT announcing (§14.5): only a `live`-delivered
    // event may voice a transition. `undefined` (fresh hydration, no event yet) is treated non-live.
    const live = projection?.lastAppliedDelivery === "live";
    const turnKey = `${status.turn.state}:${status.revision}`;
    if (turnKey !== lastTurnKey.current) {
      const previouslyKnown = lastTurnKey.current !== null;
      lastTurnKey.current = turnKey;
      if (live && previouslyKnown && visible) {
        if (status.turn.state === "failed") announceAssertive("turn failed");
        else if (status.turn.state === "ready") announcePolite("response complete");
      }
    }
    const processKey = `${status.process.state}:${status.revision}`;
    if (processKey !== lastProcessKey.current) {
      const previouslyKnown = lastProcessKey.current !== null;
      lastProcessKey.current = processKey;
      if (live && previouslyKnown && visible && status.process.state === "disconnected") {
        announceAssertive("process disconnected");
      }
    }
  }, [projection, visible]);

  // Reconnect/gap outcomes are polite, announced once per phase transition.
  useEffect(() => {
    const phase = projection?.stream;
    if (phase === undefined) return;
    if (phase === lastStreamNoteRef.current) return;
    lastStreamNoteRef.current = phase;
    if (!visible) return; // tracked, never voiced from a hidden keep-alive surface
    if (phase === "reconnecting") announcePolite("reconnecting");
    else if (phase === "gap") announcePolite("re-syncing history");
    else if (phase === "live" && lastStreamNoteRef.current !== null) {
      /* going live after a drop is implicit; no utterance needed */
    }
  }, [projection?.stream, visible]);

  if (projection === undefined) {
    // A first-connect page failure has no projection yet; surface the typed reason honestly.
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

  // The offending history capability (tool details first, then overall completeness) drives a
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
    <div className={surface} data-testid="conversation-surface" onKeyDown={onSurfaceKeyDown}>
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
      <AgentsArea agents={agents} focusedAgentId={agentFocus} onFocusAgent={applyAgentFocus} />
      {agentFocus !== null ? (
        <div className={agentFocusBar} data-testid="conversation-agent-focus-bar">
          <button
            type="button"
            className={toggle}
            onClick={() => applyAgentFocus(null)}
            data-testid="conversation-back-to-parent"
          >
            ← back to parent conversation
          </button>
          <span className={agentFocusNote} data-testid="conversation-agent-focus-note">
            viewing {focusedAgent?.label ?? "agent"}
          </span>
        </div>
      ) : null}
      {/* The timeline well stays mounted for an empty conversation; its center becomes the
          product welcome surface until the first item arrives. */}
      <ConversationTimeline
        items={focusedItems}
        totalItems={agentFocus === null ? projection.totalItems : undefined}
        hasOlder={projection.hasOlder}
        busy={projection.stream === "connecting" || projection.stream === "gap"}
        emptyNote={
          focusedItems.length === 0 && projection.stream === "live"
            ? agentFocus === null
              ? <ConversationWelcome
                  harness={projection.identity.harnessId}
                  processState={projection.status?.process.state}
                />
              : <span className={agentFocusNote} data-testid="conversation-agent-empty">
                  no evidence from {focusedAgent?.label ?? "this agent"} yet
                </span>
            : undefined
        }
        onLoadOlder={() => void loadOlderConversation(sessionId)}
        visible={scrollGeometryActive}
        restoreScroll={scrollMemory}
        onScrollMemory={handleScrollMemory}
        measurementCacheId={sessionId}
      />
    </div>
  );
}

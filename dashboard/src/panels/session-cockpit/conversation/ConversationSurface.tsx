// The active-conversation surface (design §12.1): page/stream state + scroll shell around the one
// role="feed" timeline. It reads the reconstructable store (never a fixture authority), renders the
// honest reconnect/failure states, drives revision-keyed announcers that stay SILENT during
// replay/hydration (§14.2), and exposes the global thinking toggle + a single non-dismissable
// history-completeness note (§10.2). It owns no data/paging/cursor logic — the store/reducer do.
// The render parts live in conversationSurfaceParts.tsx, the shared styles in
// conversationSurfaceStyles.ts.

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  type KeyboardEvent,
  type RefObject,
} from "react";

import { announceAssertive, announcePolite } from "../../../data/announcer";
import {
  cycleAgentFocus,
  deriveAgents,
  effectiveAgentFocus,
  filterItemsForFocus,
  type ConversationAgentView,
} from "../../../data/conversation/agents";
import {
  activeConversationStore,
  hydrateAgentConversation,
  readConversationScroll,
  rememberConversationScroll,
  useActiveConversation,
  type AgentHistoryLoadState,
  type ConversationScrollMemory,
} from "../../../data/conversation/store";
import { useHideThinking } from "../../../data/conversation/thinkingPreference";
import type { ConversationItem } from "../../../data/conversation/types";
import { AgentsArea } from "./AgentsArea";
import { ConversationReconnect } from "./ConversationReconnect";
import {
  AgentHistoryErrorBanner,
  ProjectionFailedSurface,
  resolveHistoryCapability,
  SurfaceToolbar,
  TimelineSection,
} from "./conversationSurfaceParts";
import { surface } from "./conversationSurfaceStyles";

// The surface-level agent focus keys (the Claude Code sub-agent navigation model):
// ArrowDown ANYWHERE on the surface (feed article AND scroll viewport) moves focus INTO the
// agents line when the roster is non-empty (the line owns Enter/menu from there; ArrowUp from
// the line returns focus to the timeline); ArrowLeft/ArrowRight cycle parent → agent 1 → … →
// agent N → parent as an additional path; Escape returns to the parent. Editable/interactive
// targets own their keys (the composer, buttons, labeled overflow regions, code blocks) — the
// same exclusion discipline the feed's own navigation uses.
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

function cycleSurfaceFocus(
  event: KeyboardEvent<HTMLDivElement>,
  agents: ConversationAgentView[],
  agentFocus: string | null,
  applyAgentFocus: (next: string | null) => void,
) {
  if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
  if (agents.length === 0) return;
  event.preventDefault();
  applyAgentFocus(
    cycleAgentFocus(
      agentFocus,
      agents.map((agent) => agent.agentId),
      event.key === "ArrowRight" ? 1 : -1,
    ),
  );
}

function handleSurfaceKeyDown(
  event: KeyboardEvent<HTMLDivElement>,
  agents: ConversationAgentView[],
  agentFocus: string | null,
  applyAgentFocus: (next: string | null) => void,
  surfaceRef: RefObject<HTMLDivElement | null>,
) {
  const target = event.target as HTMLElement;
  if (ownsAgentFocusKeys(target)) return;
  if (event.key === "ArrowDown") {
    // Down from ANYWHERE on the surface (feed article OR scroll viewport — one uniform
    // hijack) enters the agents line (the primary sub-agent path): the line owns
    // Enter/menu from there. The feed keeps PageUp/PageDown scrolling and [/] row moves.
    if (agents.length === 0) return;
    event.preventDefault();
    surfaceRef.current?.querySelector<HTMLElement>("[data-agents-line]")?.focus();
    return;
  }
  if (event.key === "Escape") {
    if (agentFocus === null) return;
    event.preventDefault();
    applyAgentFocus(null);
    return;
  }
  cycleSurfaceFocus(event, agents, agentFocus, applyAgentFocus);
}

function historyErrorDetail(
  state: AgentHistoryLoadState | undefined,
): string | undefined {
  return state?.phase === "failed" ? state.error.detail : undefined;
}

function agentHistoryRetry(
  sessionId: string,
  agentFocus: string | null,
): (() => void) | undefined {
  if (agentFocus === null) return undefined;
  return () => void hydrateAgentConversation(sessionId, agentFocus);
}

function useProjection(sessionId: string) {
  const projection = useActiveConversation((state) => state.bySession[sessionId]);
  const routeError = useActiveConversation((state) => state.errorBySession[sessionId]);
  const items = useMemo<ConversationItem[]>(
    () =>
      projection === undefined
        ? []
        : projection.orderedItemIds.map((id) => projection.itemsById[id]),
    [projection],
  );
  return { projection, routeError, items };
}

function useSurfaceScroll(sessionId: string) {
  const surfaceRef = useRef<HTMLDivElement>(null);
  // The remembered position, re-read at every render so a re-show commit always carries the
  // latest scroll event's value (the map only changes via scrolls, which a hidden timeline never
  // sees — so the value read on the hide→show flip is exactly where the operator left it).
  const scrollMemory = readConversationScroll(sessionId);
  const handleScrollMemory = useCallback(
    (memory: ConversationScrollMemory) =>
      rememberConversationScroll(sessionId, memory),
    [sessionId],
  );
  return { surfaceRef, scrollMemory, handleScrollMemory };
}

function useAgentFocus(
  sessionId: string,
  items: ConversationItem[],
  visible: boolean,
  bridgeEpoch: string | undefined,
) {
  // ── Sub-agent focus ──────────────────────────────────────────────────────────────────────────
  // The stored focus survives LRU eviction by design; it is NEVER applied blindly — the effective
  // focus is recomputed against the live roster, so a rehydrated projection without that agent
  // honestly falls back to the parent conversation.
  const storedAgentFocus = useActiveConversation(
    (state) => state.agentFocusBySession[sessionId],
  );
  const agents = useMemo(() => deriveAgents(items), [items]);
  const agentFocus = effectiveAgentFocus(storedAgentFocus, agents);
  const focusedAgent =
    agentFocus === null
      ? undefined
      : agents.find((a) => a.agentId === agentFocus);
  const agentHistoryState = useActiveConversation((state) =>
    agentFocus === null
      ? undefined
      : state.agentHistoryBySession[sessionId]?.[agentFocus],
  );
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
        const label =
          agents.find((agent) => agent.agentId === next)?.label ?? "agent";
        announcePolite(`viewing ${label}`);
      }
    },
    [agents, sessionId, visible],
  );

  // Focus can already exist when a rehydrated page or remounted keep-warm surface arrives. Drive
  // acquisition from the validated effective focus, not only from click handlers; the runtime
  // singleflight makes event selection + remount converge on exactly one POST.
  useEffect(() => {
    if (!visible || agentFocus === null) return;
    void hydrateAgentConversation(sessionId, agentFocus);
  }, [agentFocus, bridgeEpoch, sessionId, visible]);

  return {
    agents,
    agentFocus,
    focusedAgent,
    agentHistoryState,
    focusedItems,
    applyAgentFocus,
  };
}

function useSurfaceAnnouncers(
  projection: ReturnType<typeof useProjection>["projection"],
  visible: boolean,
) {
  const lastTurnKey = useRef<string | null>(null);
  const lastProcessKey = useRef<string | null>(null);
  const lastStreamNoteRef = useRef<string | null>(null);

  // Announcers key on (state + revision) and are suppressed for non-live delivery (hydration/replay).
  useEffect(() => {
    const status = projection?.status;
    if (status === undefined) return;
    // Hydration/re-page updates the store WITHOUT announcing (§14.5): only a `live`-delivered
    // event may voice a transition. `undefined` (fresh hydration, no event yet) is treated non-live.
    const live = projection?.lastAppliedDelivery === "live";
    const turnKey = `${status.turn.state}:${status.revision}`;
    if (turnKey === lastTurnKey.current) return;
    const previouslyKnown = lastTurnKey.current !== null;
    lastTurnKey.current = turnKey;
    if (!live || !previouslyKnown || !visible) return;
    if (status.turn.state === "failed") announceAssertive("turn failed");
    else if (status.turn.state === "ready") announcePolite("response complete");
  }, [projection, visible]);

  useEffect(() => {
    const status = projection?.status;
    if (status === undefined) return;
    const processKey = `${status.process.state}:${status.revision}`;
    if (processKey === lastProcessKey.current) return;
    const previouslyKnown = lastProcessKey.current !== null;
    lastProcessKey.current = processKey;
    if (
      projection?.lastAppliedDelivery === "live" &&
      previouslyKnown &&
      visible &&
      status.process.state === "disconnected"
    ) {
      announceAssertive("process disconnected");
    }
  }, [projection, visible]);

  // Reconnect/gap outcomes are polite, announced once per phase transition.
  useEffect(() => {
    const phase = projection?.stream;
    if (phase === undefined || phase === lastStreamNoteRef.current) return;
    lastStreamNoteRef.current = phase;
    if (!visible) return; // tracked, never voiced from a hidden keep-alive surface
    if (phase === "reconnecting") announcePolite("reconnecting");
    else if (phase === "gap") announcePolite("re-syncing history");
  }, [projection?.stream, visible]);
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
      keep-alive pool). Its projection/cursor stay warm but its physical stream is paused; the refs
      below still track any final in-flight transition so a re-show never voices stale state. */
  visible?: boolean;
  /** False whenever this retained timeline is not the active visible feed. Its DOM, scrollTop, and
      TanStack measurement cache remain mounted, while observers/listeners/timers detach. Kept
      separate from `visible` because the latter also owns announcer semantics above. */
  scrollGeometryActive?: boolean;
}) {
  const hideThinking = useHideThinking();
  const { projection, routeError, items } = useProjection(sessionId);
  const { surfaceRef, scrollMemory, handleScrollMemory } = useSurfaceScroll(sessionId);
  const focus = useAgentFocus(sessionId, items, visible, projection?.identity.bridgeEpoch);
  useSurfaceAnnouncers(projection, visible);

  const onSurfaceKeyDown = useCallback(
    (event: KeyboardEvent<HTMLDivElement>) =>
      handleSurfaceKeyDown(event, focus.agents, focus.agentFocus, focus.applyAgentFocus, surfaceRef),
    [focus.agents, focus.agentFocus, focus.applyAgentFocus, surfaceRef],
  );

  if (projection === undefined) {
    // A first-connect page failure has no projection yet; surface the typed reason honestly.
    return (
      <ProjectionFailedSurface
        routeError={routeError}
        onRetry={onRetry}
        onShowDiagnostics={onShowDiagnostics}
      />
    );
  }

  // The offending history capability (tool details first, then overall completeness) drives a
  // short cue; its full reason lives in the cue's hover disclosure, not an always-visible paragraph.
  const historyCapability = resolveHistoryCapability(
    projection.capabilities?.history,
  );

  return (
    <div
      className={surface}
      ref={surfaceRef}
      role="presentation"
      data-testid="conversation-surface"
      onKeyDown={onSurfaceKeyDown}
    >
      <SurfaceToolbar hideThinking={hideThinking} onShowDiagnostics={onShowDiagnostics} sessionId={sessionId} projection={projection} visible={visible} historyCapability={historyCapability} />
      <ConversationReconnect
        phase={projection.stream}
        reason={projection.stream === "projection-failed" ? routeError?.detail : undefined}
        onRetry={onRetry}
        onShowDiagnostics={onShowDiagnostics}
      />
      {/* The agents area owns the compact line — the count chip plus, in an agent view, the
          viewing note + back-to-parent affordance (260718-CHATS-L7R R5). */}
      <AgentsArea agents={focus.agents} focusedAgentId={focus.agentFocus} onFocusAgent={focus.applyAgentFocus} />
      <AgentHistoryErrorBanner detail={historyErrorDetail(focus.agentHistoryState)} onRetry={agentHistoryRetry(sessionId, focus.agentFocus)} />
      {/* The timeline well stays mounted for an empty conversation; its center becomes the
          product welcome surface until the first item arrives. */}
      <TimelineSection focusedItems={focus.focusedItems} totalItems={projection.totalItems} hasOlder={projection.hasOlder} stream={projection.stream} agentFocus={focus.agentFocus} focusedAgentLabel={focus.focusedAgent?.label} harnessId={projection.identity.harnessId} processState={projection.status?.process.state} sessionId={sessionId} scrollGeometryActive={scrollGeometryActive} scrollMemory={scrollMemory} onScrollMemory={handleScrollMemory} />
    </div>
  );
}

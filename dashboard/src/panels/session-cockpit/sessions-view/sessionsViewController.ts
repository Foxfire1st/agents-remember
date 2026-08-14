import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { ImperativePanelHandle } from "react-resizable-panels";

import {
  preferLiveSession,
  readLastActiveSessionId,
} from "../../../data/catalogPoll";
import {
  createCommandRegistry,
  registerDefaultCommands,
  type CommandContext,
  type PalettePage,
} from "../../../data/commands";
import {
  FOCUS_REGIONS,
  nextRegion,
  PTY_HOST_SELECTOR,
  regionTargetSelector,
  STAGE_HEADER_SELECTOR,
  type FocusRegion,
} from "../../../data/keymap/focus";
import {
  attentionRollup,
  buildRailModel,
  criticalBusSessionIds,
  railCycleOrder,
  smartDefaultFocus,
} from "../../../data/railModel";
import { startSeatStateAnnouncer } from "../../../data/announcer";
import {
  sessionCockpitStore,
  startCockpitMirror,
  useSessionCockpit,
} from "../../../data/sessionCockpitStore";
import { startRetireResidualSweep } from "../../../data/sessionLifecycle";
import {
  cycleEffortRequested,
  startSetPromotionWatcher,
} from "../../../data/setClient";
import { hasUnackedSetAttention } from "../../../data/setChips";
import {
  autoCollapseTransition,
  hasPersistedPanelLayout,
  INSPECTOR_AUTO_COLLAPSE_PX,
  RAIL_AUTO_COLLAPSE_PX,
  railDefaultPercent,
  stageBelowPtyFloor,
} from "../../../data/sessionLayout";
import { useActiveConversation } from "../../../data/conversation/store";
import { useSessions } from "../../../data/sessions";
import { shortId } from "../../../data/conversation/format";
import { useDashboard } from "../../../data/store";
import type { AgentPickupNode, TaskDocNode } from "../../../types/projection";
import { useConversationInterrupt } from "../conversation/useConversationControls";
import { useKeyboardZones } from "../useKeyboardZones";
import { useSessionsPaletteCommands } from "./useSessionsPaletteCommands";
import type { SessionComposerHandle } from "../../SessionComposer";
import { usePersistedFlag } from "../../file-viewer/usePersistedFlag";
import {
  INSPECTOR_OPEN_KEY,
  PANELS_AUTOSAVE_ID,
  RAIL_MAX_PERCENT,
  RAIL_MIN_PERCENT,
} from "./styles";

const EMPTY_DOCS: TaskDocNode[] = [];
const EMPTY_PICKUPS: AgentPickupNode[] = [];

type SessionsViewData = ReturnType<typeof useSessionsViewSelectors> &
  ReturnType<typeof useSessionsViewDerived>;
type SessionsViewHandlers = ReturnType<typeof useFocusAndLibraryHandlers> &
  ReturnType<typeof usePaletteAndRegionHandlers>;
type SessionsViewInspector = ReturnType<typeof useInspectorPanelActions>;

export interface SessionsViewProps {
  active: boolean;
  selectedLifecycleId?: string;
  selectedLeafKey?: string;
  taskDocuments?: TaskDocNode[];
  contextMaster?: string;
}

export interface SessionsViewRefs {
  rootRef: React.RefObject<HTMLDivElement | null>;
  stageRef: React.RefObject<HTMLElement | null>;
  railRef: React.RefObject<ImperativePanelHandle | null>;
  inspectorRef: React.RefObject<ImperativePanelHandle | null>;
  inspectorToggleRef: React.RefObject<HTMLButtonElement | null>;
  lastWidthRef: React.RefObject<number | null>;
  paletteInvokerRef: React.RefObject<HTMLElement | null>;
  inspectorCollapsedRef: React.RefObject<boolean>;
  inspectorAutoCollapsedRef: React.RefObject<boolean>;
  inspectorResponsiveTransitionRef: React.RefObject<
    "collapse" | "expand" | null
  >;
  inspectorPreferenceSyncRef: React.RefObject<boolean>;
  inspectorInitialPreferenceSyncRef: React.RefObject<boolean>;
  preferredActiveIdRef: React.RefObject<string | null>;
  composerRef: React.RefObject<SessionComposerHandle | null>;
  chatsLibraryReturnRef: React.RefObject<HTMLElement | null>;
  chatsDiagnosticsReturnRef: React.RefObject<HTMLElement | null>;
  chatsLibraryOpenRef: React.RefObject<boolean>;
  chatsDiagnosticsOpenRef: React.RefObject<boolean>;
  lastFocusRef: React.RefObject<{
    id: string | null;
    status: string;
    label: string | null;
  }>;
  railCalibratedRef: React.RefObject<boolean>;
  contextRef: React.RefObject<() => CommandContext>;
}

export function useSessionsViewRefs(): SessionsViewRefs {
  const rootRef = useRef<HTMLDivElement>(null);
  const stageRef = useRef<HTMLElement>(null);
  const railRef = useRef<ImperativePanelHandle>(null);
  const inspectorRef = useRef<ImperativePanelHandle>(null);
  const inspectorToggleRef = useRef<HTMLButtonElement>(null);
  const lastWidthRef = useRef<number | null>(null);
  const paletteInvokerRef = useRef<HTMLElement | null>(null);
  const inspectorCollapsedRef = useRef(false);
  const inspectorAutoCollapsedRef = useRef(false);
  const inspectorResponsiveTransitionRef = useRef<
    "collapse" | "expand" | null
  >(null);
  // Panel storage may emit an initial callback before the preference-sync effect gets its turn.
  // That callback describes recovered geometry, not a new operator decision.
  const inspectorPreferenceSyncRef = useRef(true);
  const inspectorInitialPreferenceSyncRef = useRef(true);
  // Capture the preference before any component effect can write. A catalog hydrate with no saved
  // preference still assigns its first live row to `activeId`; that transport fallback must not
  // override the cockpit's attention-aware smart default.
  const preferredActiveIdRef = useRef(readLastActiveSessionId());
  const composerRef = useRef<SessionComposerHandle>(null);
  const chatsLibraryReturnRef = useRef<HTMLElement | null>(null);
  const chatsDiagnosticsReturnRef = useRef<HTMLElement | null>(null);
  const chatsLibraryOpenRef = useRef(false);
  const chatsDiagnosticsOpenRef = useRef(false);
  const lastFocusRef = useRef<{
    id: string | null;
    status: string;
    label: string | null;
  }>({ id: null, status: "none", label: null });
  const railCalibratedRef = useRef(false);
  const contextRef = useRef<() => CommandContext>(
    () => undefined as unknown as CommandContext,
  );
  return useMemo(
    () => ({
      rootRef,
      stageRef,
      railRef,
      inspectorRef,
      inspectorToggleRef,
      lastWidthRef,
      paletteInvokerRef,
      inspectorCollapsedRef,
      inspectorAutoCollapsedRef,
      inspectorResponsiveTransitionRef,
      inspectorPreferenceSyncRef,
      inspectorInitialPreferenceSyncRef,
      preferredActiveIdRef,
      composerRef,
      chatsLibraryReturnRef,
      chatsDiagnosticsReturnRef,
      chatsLibraryOpenRef,
      chatsDiagnosticsOpenRef,
      lastFocusRef,
      railCalibratedRef,
      contextRef,
    }),
    [
      rootRef, stageRef, railRef, inspectorRef, inspectorToggleRef, lastWidthRef,
      paletteInvokerRef, inspectorCollapsedRef, inspectorAutoCollapsedRef, inspectorResponsiveTransitionRef,
      inspectorPreferenceSyncRef, inspectorInitialPreferenceSyncRef, preferredActiveIdRef,
      composerRef, chatsLibraryReturnRef, chatsDiagnosticsReturnRef, chatsLibraryOpenRef,
      chatsDiagnosticsOpenRef, lastFocusRef, railCalibratedRef, contextRef,
    ],
  );
}

export function useSessionsViewState(refs: SessionsViewRefs) {
  const [railCollapsed, setRailCollapsed] = useState(false);
  // Deliberate operator intent and the panel's current geometry are separate facts. A responsive
  // collapse may hide an opted-in inspector, but it must never rewrite that opt-in: a narrow
  // reload can therefore recover it when the viewport widens again.
  const [inspectorIntentOpen, setInspectorIntentOpen] = usePersistedFlag(
    INSPECTOR_OPEN_KEY,
    false,
  );
  const [inspectorCollapsed, setInspectorCollapsed] = useState(
    !inspectorIntentOpen,
  );
  refs.inspectorCollapsedRef.current = inspectorCollapsed;
  const [stageNarrow, setStageNarrow] = useState(false);
  // The VISIBLE pane's real column count: when a pane reports, the ~80-col floor chip
  // reflects the pane truth instead of the pixel estimate.
  const [ptyCols, setPtyCols] = useState<number | null>(null);
  const [palette, setPalette] = useState<{
    open: boolean;
    page: PalettePage;
    initialQuery: string;
  }>({ open: false, page: "commands", initialQuery: "" });
  const [handoff, setHandoff] = useState<string | null>(null);
  // The LaunchFlow dialog — opened from the palette, or pre-filled by the failed-launch
  // banner's 'Launch corrected…' (the refused pair, re-gated against the live catalog).
  const [launch, setLaunch] = useState<{
    open: boolean;
    prefill?: { harness: string; modelKey?: string; effort?: string };
  }>({ open: false });
  // The ModelEffortControl popover state lives here so the palette commands open the SAME
  // popover the header trigger opens (one control, two surfaces).
  const [controlPopoverOpen, setControlPopoverOpen] = useState(false);
  // The structured Chats stage-mode toggles (in-stage history browser and the
  // default-off terminal-diagnostics drawer). Both live here + in the palette (design §12.6) and
  // reset on focus change. A focus-return token restores the invoker when the library closes (§14.1).
  const [chatsLibraryOpen, setChatsLibraryOpen] = useState(false);
  const [chatsDiagnosticsOpen, setChatsDiagnosticsOpen] = useState(false);
  refs.chatsLibraryOpenRef.current = chatsLibraryOpen;
  refs.chatsDiagnosticsOpenRef.current = chatsDiagnosticsOpen;
  return {
    railCollapsed,
    setRailCollapsed,
    inspectorIntentOpen,
    setInspectorIntentOpen,
    inspectorCollapsed,
    setInspectorCollapsed,
    stageNarrow,
    setStageNarrow,
    ptyCols,
    setPtyCols,
    palette,
    setPalette,
    handoff,
    setHandoff,
    launch,
    setLaunch,
    controlPopoverOpen,
    setControlPopoverOpen,
    chatsLibraryOpen,
    setChatsLibraryOpen,
    chatsDiagnosticsOpen,
    setChatsDiagnosticsOpen,
  };
}

export function useSessionsViewSelectors(props: SessionsViewProps) {
  const registry = useMemo(
    () => registerDefaultCommands(createCommandRegistry()),
    [],
  );
  const sessions = useSessions((state) => state.sessions);
  const activeSessionId = useSessions((state) => state.activeId);
  const focusedSessionId = useSessionCockpit((state) => state.focusedSessionId);
  const treeView = useSessionCockpit((state) => state.orchestrationTreeView);
  const perSession = useSessionCockpit((state) => state.perSession);
  const projectedTaskDocuments = useDashboard(
    (state) => state.analytics?.taskDocuments ?? EMPTY_DOCS,
  );
  const taskDocuments = props.taskDocuments ?? projectedTaskDocuments;
  const pickups = useDashboard((state) =>
    state.analytics ? state.analytics.agentPickups : EMPTY_PICKUPS,
  );
  const agentNotifierHeartbeat = useDashboard(
    (state) => state.agentNotifierHeartbeat,
  );
  const chatsInterrupt = useConversationInterrupt(
    focusedSessionId ?? undefined,
  );
  const chatsInterruptRef = useRef(chatsInterrupt);
  chatsInterruptRef.current = chatsInterrupt;
  return {
    registry,
    sessions,
    activeSessionId,
    focusedSessionId,
    treeView,
    perSession,
    taskDocuments,
    pickups,
    agentNotifierHeartbeat,
    chatsInterrupt,
    chatsInterruptRef,
  };
}

export function useSessionsViewDerived(
  selectors: ReturnType<typeof useSessionsViewSelectors>,
) {
  const { focusedSessionId, sessions, perSession, pickups, taskDocuments } =
    selectors;
  // The focused seat's conversation projection carries its OWN live
  // turn signal over SSE (sub-second), which the sweep-bounded catalog turn-state lags by ~10s. When
  // the projection shows a turn actively streaming, prefer it so the stage authorities show a
  // WORKING state instead of a settled-green `turn-ended`.
  const focusedLiveTurnWorking = useActiveConversation((state) => {
    if (focusedSessionId === null || focusedSessionId === undefined) return false;
    const projection = state.bySession[focusedSessionId];
    if (projection === undefined || projection.stream !== "live") return false;
    const status = projection.status;
    if (status === undefined || status.freshness.state === "stale") return false;
    const turn = status.turn.state;
    return (
      turn === "working" ||
      turn === "settling" ||
      turn === "retrying" ||
      turn === "compacting"
    );
  });
  const focused = useMemo(() => {
    const focusedBase = sessions.find(
      (session) => session.id === focusedSessionId,
    );
    return focusedBase !== undefined && focusedLiveTurnWorking
      ? { ...focusedBase, liveTurnWorking: true }
      : focusedBase;
  }, [focusedLiveTurnWorking, focusedSessionId, sessions]);
  const focusedLive =
    focused !== undefined && (focused.status ?? "running") === "running";
  const focusedConversationLive = useActiveConversation((state) => {
    if (focusedSessionId === null || focusedSessionId === undefined) return false;
    return state.bySession[focusedSessionId]?.stream === "live";
  });
  const model = useMemo(
    () => buildRailModel(sessions, taskDocuments),
    [sessions, taskDocuments],
  );
  const unackedIds = useMemo(
    () =>
      sessions
        .filter((session) => hasUnackedSetAttention(perSession[session.id]))
        .map((session) => session.id),
    [sessions, perSession],
  );
  const criticalBus = useMemo(
    () => [...criticalBusSessionIds(pickups, sessions)],
    [pickups, sessions],
  );
  const rollup = useMemo(
    () =>
      attentionRollup(sessions, {
        unackedSessionIds: unackedIds,
        criticalBusSessionIds: criticalBus,
      }),
    [sessions, unackedIds, criticalBus],
  );
  return {
    focusedLiveTurnWorking,
    focused,
    focusedLive,
    focusedConversationLive,
    model,
    rollup,
  };
}

export function useFocusAndLibraryHandlers(
  refs: SessionsViewRefs,
  state: ReturnType<typeof useSessionsViewState>,
) {
  const rememberLiveSession = useCallback((id: string | null) => {
    // `activeId` is the launch/gate route and persisted reload preference, so only a live row owns
    // it. Cockpit focus may still inspect a landed row without the next catalog hydrate fighting it.
    if (preferLiveSession(id)) refs.preferredActiveIdRef.current = id;
  }, [refs]);

  const focusSession = useCallback(
    (id: string | null) => {
      state.setHandoff(null);
      // A focus switch never carries the previous seat's open control popover / stage mode along.
      state.setControlPopoverOpen(false);
      state.setChatsLibraryOpen(false);
      state.setChatsDiagnosticsOpen(false);
      rememberLiveSession(id);
      sessionCockpitStore.getState().setFocusedSession(id);
    },
    [rememberLiveSession, state],
  );

  // Open the in-stage history browser, capturing a stable focus-return token first (§14.1).
  const openChatsLibrary = useCallback(() => {
    const activeElement = document.activeElement;
    refs.chatsLibraryReturnRef.current =
      activeElement instanceof HTMLElement ? activeElement : null;
    state.setChatsLibraryOpen(true);
  }, [refs, state]);
  const closeChatsLibrary = useCallback(() => {
    state.setChatsLibraryOpen(false);
    const invoker = refs.chatsLibraryReturnRef.current;
    if (invoker?.isConnected) invoker.focus();
    refs.chatsLibraryReturnRef.current = null;
  }, [refs, state]);

  // Diagnostics drawer focus-return token (§12.6/§14.1): capture on open from any invoker
  // (toolbar button, reconnect action, palette), restore on close so focus never drops to <body>.
  const toggleChatsDiagnostics = useCallback(
    (open: boolean) => {
      if (open) {
        const activeElement = document.activeElement;
        refs.chatsDiagnosticsReturnRef.current =
          activeElement instanceof HTMLElement ? activeElement : null;
        state.setChatsDiagnosticsOpen(true);
      } else {
        state.setChatsDiagnosticsOpen(false);
        const invoker = refs.chatsDiagnosticsReturnRef.current;
        if (invoker?.isConnected) invoker.focus();
        refs.chatsDiagnosticsReturnRef.current = null;
      }
    },
    [refs, state],
  );

  return {
    rememberLiveSession,
    focusSession,
    openChatsLibrary,
    closeChatsLibrary,
    toggleChatsDiagnostics,
  };
}

export function usePaletteAndRegionHandlers(
  refs: SessionsViewRefs,
  state: ReturnType<typeof useSessionsViewState>,
) {
  const focusSelector = useCallback(
    (selector: string) => {
      const target = refs.rootRef.current?.querySelector<HTMLElement>(selector);
      target?.focus();
    },
    [refs],
  );

  const focusRegion = useCallback(
    (region: FocusRegion) => {
      // The rail's roving target is its selected row; the always-present rail container is only
      // the empty/collapsed-folder fallback. Query it explicitly before the generic first target.
      if (region === "rail") {
        const selected = refs.rootRef.current?.querySelector<HTMLElement>(
          '[data-region="rail"] [data-selected="true"]',
        );
        if (selected) {
          selected.focus();
          return;
        }
      }
      focusSelector(regionTargetSelector(region));
    },
    [focusSelector, refs],
  );

  // The F6 cycle: rail → stage → inspector → status line, collapsed panels excluded (design §5.3).
  const cycleRegion = useCallback(
    (direction: 1 | -1) => {
      const available = FOCUS_REGIONS.filter(
        (region) =>
          (region !== "rail" || !state.railCollapsed) &&
          (region !== "inspector" || !state.inspectorCollapsed),
      );
      const activeElement = document.activeElement;
      const currentHost =
        activeElement instanceof Element
          ? activeElement.closest("[data-region]")
          : null;
      const current = (currentHost?.getAttribute("data-region") ??
        null) as FocusRegion | null;
      const next = nextRegion(current, direction, available);
      if (next) focusRegion(next);
    },
    [focusRegion, state.inspectorCollapsed, state.railCollapsed],
  );

  const openPalette = useCallback(
    (page: PalettePage = "commands", initialQuery = "") => {
      const activeElement = document.activeElement;
      state.setPalette((current) => {
        // Keep the ORIGINAL invoker across an in-palette page switch so close returns focus there.
        if (!current.open) {
          refs.paletteInvokerRef.current =
            activeElement instanceof HTMLElement ? activeElement : null;
        }
        return { open: true, page, initialQuery };
      });
    },
    [refs, state],
  );

  const closePalette = useCallback(() => {
    state.setPalette({ open: false, page: "commands", initialQuery: "" });
    // Palette close returns focus to its invoker (when it is still in the document).
    const invoker = refs.paletteInvokerRef.current;
    if (invoker?.isConnected) invoker.focus();
    refs.paletteInvokerRef.current = null;
  }, [refs, state]);

  return {
    focusSelector,
    focusRegion,
    cycleRegion,
    openPalette,
    closePalette,
  };
}

export function useInspectorFocusHandlers(refs: SessionsViewRefs) {
  const restoreFocusFromInspector = useCallback(() => {
    const activeElement = document.activeElement;
    if (!(activeElement instanceof HTMLElement)) return;
    const inspector =
      refs.rootRef.current?.querySelector<HTMLElement>("#chats-inspector");
    const handle = refs.rootRef.current?.querySelector<HTMLElement>(
      '[data-testid="sessions-handle-inspector"]',
    );
    if (inspector?.contains(activeElement) || handle === activeElement) {
      refs.inspectorToggleRef.current?.focus();
    }
  }, [refs]);

  const armResponsiveInspectorTransition = useCallback(
    (transition: "collapse" | "expand") => {
      refs.inspectorResponsiveTransitionRef.current = transition;
      // An imperative no-op does not emit onCollapse/onExpand. Do not let its marker leak into a
      // later operator drag/keystroke and misclassify deliberate intent.
      window.requestAnimationFrame(() => {
        if (refs.inspectorResponsiveTransitionRef.current === transition) {
          refs.inspectorResponsiveTransitionRef.current = null;
        }
      });
    },
    [refs],
  );
  return { restoreFocusFromInspector, armResponsiveInspectorTransition };
}

export function useInspectorPanelActions(
  refs: SessionsViewRefs,
  state: ReturnType<typeof useSessionsViewState>,
  focusHandlers: ReturnType<typeof useInspectorFocusHandlers>,
) {
  const { restoreFocusFromInspector, armResponsiveInspectorTransition } =
    focusHandlers;
  const collapseInspector = useCallback(
    (source: "operator" | "responsive") => {
      if (source === "operator") {
      refs.inspectorPreferenceSyncRef.current = false;
      refs.inspectorAutoCollapsedRef.current = false;
      state.setInspectorIntentOpen(false);
      } else {
        refs.inspectorAutoCollapsedRef.current = true;
      }
      refs.inspectorCollapsedRef.current = true;
      state.setInspectorCollapsed(true);
      restoreFocusFromInspector();
      if (!refs.inspectorRef.current || refs.inspectorRef.current.isCollapsed()) {
        return;
      }
      if (source === "responsive") armResponsiveInspectorTransition("collapse");
      refs.inspectorRef.current?.collapse();
    },
    [armResponsiveInspectorTransition, restoreFocusFromInspector, refs, state],
  );

  const expandInspector = useCallback(
    (source: "operator" | "responsive") => {
      if (source === "operator") {
        refs.inspectorPreferenceSyncRef.current = false;
        refs.inspectorAutoCollapsedRef.current = false;
        state.setInspectorIntentOpen(true);
      }
      refs.inspectorCollapsedRef.current = false;
      state.setInspectorCollapsed(false);
      if (!refs.inspectorRef.current || refs.inspectorRef.current.isExpanded()) {
        return;
      }
      if (source === "responsive") armResponsiveInspectorTransition("expand");
      refs.inspectorRef.current?.expand();
    },
    [armResponsiveInspectorTransition, refs, state],
  );

  const handleInspectorCollapse = useCallback(() => {
    const responsive =
      refs.inspectorResponsiveTransitionRef.current === "collapse";
    if (responsive) refs.inspectorResponsiveTransitionRef.current = null;
    refs.inspectorCollapsedRef.current = true;
    state.setInspectorCollapsed(true);
    restoreFocusFromInspector();
    if (!responsive && !refs.inspectorPreferenceSyncRef.current) {
      // Divider drag / separator keyboard collapse is an operator action just like the toggle.
      refs.inspectorAutoCollapsedRef.current = false;
      state.setInspectorIntentOpen(false);
    }
  }, [refs, restoreFocusFromInspector, state]);

  const handleInspectorExpand = useCallback(() => {
    const responsive =
      refs.inspectorResponsiveTransitionRef.current === "expand";
    if (responsive) refs.inspectorResponsiveTransitionRef.current = null;
    // Panel autosave can replay a prior open layout after the narrow responsive decision. While
    // recovery is armed, that replay is geometry noise: only a width recovery or explicit open
    // clears the arm. Reassert the transient collapse without touching persisted intent.
    if (refs.inspectorAutoCollapsedRef.current && refs.inspectorCollapsedRef.current) {
      window.requestAnimationFrame(() => collapseInspector("responsive"));
      return;
    }
    refs.inspectorCollapsedRef.current = false;
    state.setInspectorCollapsed(false);
    if (!responsive && !refs.inspectorPreferenceSyncRef.current) {
      refs.inspectorAutoCollapsedRef.current = false;
      state.setInspectorIntentOpen(true);
    }
  }, [collapseInspector, refs, state]);

  return {
    collapseInspector,
    expandInspector,
    handleInspectorCollapse,
    handleInspectorExpand,
  };
}

export function useInspectorInitialSync(
  active: boolean,
  refs: SessionsViewRefs,
  state: ReturnType<typeof useSessionsViewState>,
  inspector: ReturnType<typeof useInspectorPanelActions>,
) {
  const { collapseInspector } = inspector;
  // `defaultSize={0}` is not sufficient when panel storage has a prior width. Deliberate intent
  // owns initial visibility; panel storage owns only the width once opened. On the FIRST mount,
  // reconcile against the already-painted root width too: a narrow reload must start transiently
  // collapsed while retaining the open preference for later recovery.
  useEffect(() => {
    const width = refs.rootRef.current?.clientWidth ?? 0;
    // Chats is a keep-alive route. Mounting while Operations is still active reports width 0;
    // becoming the visible route is the first meaningful geometry boundary, not that hidden mount.
    if (!active || width <= 0) return;
    const initial = refs.inspectorInitialPreferenceSyncRef.current;
    refs.inspectorInitialPreferenceSyncRef.current = false;
    refs.inspectorPreferenceSyncRef.current = true;
    const action = initialInspectorAction(
      initial,
      state.inspectorIntentOpen,
      width,
      refs.inspectorAutoCollapsedRef.current,
    );
    if (action === "collapse-responsive") {
      collapseInspector("responsive");
    } else if (action === "expand") {
      refs.inspectorRef.current?.expand();
    } else if (action === "collapse") {
      refs.inspectorRef.current?.collapse();
    }
    window.requestAnimationFrame(() => {
      refs.inspectorPreferenceSyncRef.current = false;
    });
  }, [active, collapseInspector, refs, state.inspectorIntentOpen]);
}

function initialInspectorAction(
  initial: boolean,
  intentOpen: boolean,
  width: number,
  autoCollapsed: boolean,
): "collapse-responsive" | "expand" | "collapse" | null {
  if (initial && intentOpen && width > 0 && width < INSPECTOR_AUTO_COLLAPSE_PX) {
    return "collapse-responsive";
  }
  if (intentOpen && !autoCollapsed) return "expand";
  if (!intentOpen) return "collapse";
  return null;
}

function applyRailResponsive(
  node: HTMLDivElement,
  sessionsLength: number,
  railCollapsed: boolean,
  previousWidth: number | null,
  refs: SessionsViewRefs,
) {
  const width = node.clientWidth;
  const railMove = autoCollapseTransition(
    previousWidth,
    width,
    RAIL_AUTO_COLLAPSE_PX,
  );
  // The rail owns the only chat-creation entrance. An empty narrow cockpit must keep it
  // directly actionable; once a chat exists, the normal responsive collapse policy applies.
  if (sessionsLength === 0) {
    if (railCollapsed) refs.railRef.current?.expand();
  } else if (railMove === "collapse") refs.railRef.current?.collapse();
  else if (railMove === "expand") refs.railRef.current?.expand();
}

function applyInspectorResponsive(
  node: HTMLDivElement,
  inspectorIntentOpen: boolean,
  previousWidth: number | null,
  refs: SessionsViewRefs,
  inspector: ReturnType<typeof useInspectorPanelActions>,
) {
  const width = node.clientWidth;
  const inspectorMove = autoCollapseTransition(
    previousWidth,
    width,
    INSPECTOR_AUTO_COLLAPSE_PX,
  );
  if (inspectorMove === "collapse" && inspectorIntentOpen) {
    inspector.collapseInspector("responsive");
  } else if (
    inspectorMove === "expand" &&
    inspectorIntentOpen &&
    refs.inspectorAutoCollapsedRef.current
  ) {
    refs.inspectorAutoCollapsedRef.current = false;
    inspector.expandInspector("responsive");
  }
}

function applyResponsiveLayout(
  node: HTMLDivElement,
  sessionsLength: number,
  railCollapsed: boolean,
  inspectorIntentOpen: boolean,
  refs: SessionsViewRefs,
  inspector: ReturnType<typeof useInspectorPanelActions>,
) {
  const width = node.clientWidth;
  if (!width) return; // hidden keep-alive layer (display:none) — never react to a 0-measure
  const percent = railCalibratedFor(refs, width);
  if (percent >= 0) refs.railRef.current?.resize(percent);
  const previousWidth = refs.lastWidthRef.current;
  refs.lastWidthRef.current = width;
  applyRailResponsive(node, sessionsLength, railCollapsed, previousWidth, refs);
  applyInspectorResponsive(
    node,
    inspectorIntentOpen,
    previousWidth,
    refs,
    inspector,
  );
}

function railCalibratedFor(refs: SessionsViewRefs, rootWidth: number): number {
  if (refs.railCalibratedRef.current || rootWidth <= 0) return -1;
  refs.railCalibratedRef.current = true;
  if (hasPersistedPanelLayout(PANELS_AUTOSAVE_ID)) return -1;
  return railDefaultPercent(rootWidth, RAIL_MIN_PERCENT, RAIL_MAX_PERCENT);
}

export function useSessionsViewLayout(
  refs: SessionsViewRefs,
  state: ReturnType<typeof useSessionsViewState>,
  data: SessionsViewData,
  inspector: ReturnType<typeof useInspectorPanelActions>,
) {
  // The ~80-col floor chip (design §2): the chip must always reflect the STAGE's actual
  // width — re-measured from every path that can change it.
  const measureStage = useCallback(() => {
    state.setStageNarrow(stageBelowPtyFloor(refs.stageRef.current?.clientWidth ?? 0));
  }, [refs, state]);

  // Panel-layout changes (divider drags, collapse/expand — including palette-driven ones) never
  // resize the view ROOT, so they re-measure the stage here.
  const handlePanelLayout = useCallback(() => {
    measureStage();
    const width = refs.rootRef.current?.clientWidth ?? 0;
    const percent = railCalibratedFor(refs, width);
    if (percent >= 0) refs.railRef.current?.resize(percent);
  }, [measureStage, refs]);

  // Narrow-width rules: rail behavior is unchanged. The inspector defaults closed and only
  // auto-reopens after a width recovery when that same width transition closed it.
  useEffect(() => {
    const node = refs.rootRef.current;
    const stage = refs.stageRef.current;
    if (!node) return undefined;
    const measure = () => {
      measureStage();
      applyResponsiveLayout(
        node,
        data.sessions.length,
        state.railCollapsed,
        state.inspectorIntentOpen,
        refs,
        inspector,
      );
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(node);
    // The stage element itself is observed too: a divider drag changes the stage width with NO
    // root resize, and the observer callback re-runs measureStage first.
    if (stage) observer.observe(stage);
    return () => observer.disconnect();
  }, [
    inspector,
    data.sessions.length,
    measureStage,
    refs,
    state.inspectorIntentOpen,
    state.railCollapsed,
  ]);

  return { measureStage, handlePanelLayout };
}

export function useSessionsViewWatchers() {
  useEffect(() => startCockpitMirror(), []);
  // Retire residuals are captured for EVERY row — focus-independent — so an
  // unfocused seat's retireControlStopError still surfaces (and resurfaces after a reload).
  useEffect(() => startRetireResidualSweep(), []);
  // The promotion/drift watcher (turn-ended + focus re-GETs) and the assertive
  // failed/awaiting-input announcer — both refcounted, view-lifetime.
  useEffect(() => startSetPromotionWatcher(), []);
  useEffect(() => startSeatStateAnnouncer(), []);
}

function focusStatusOf(
  focusedSessionId: string | null,
  sessions: SessionsViewData["sessions"],
) {
  const current = focusedSessionId
    ? sessions.find((session) => session.id === focusedSessionId)
    : undefined;
  const status = current
    ? current.status ?? "running"
    : focusedSessionId
      ? "gone"
      : "none";
  return { current, status };
}

function needsFocusHandoff(
  previous: { id: string | null; status: string },
  focusedSessionId: string | null,
  status: string,
): boolean {
  return (
    previous.id !== null &&
    previous.id === focusedSessionId &&
    (previous.status === "running"
      ? status !== "running"
      : previous.status !== "none" && status === "gone")
  );
}

function handoffWord(status: string): string {
  if (status === "landed") return "landed";
  if (status === "gone") return "ended";
  return "retired";
}

function handoffMessage(
  previous: { id: string | null; label: string | null },
  current:
    | { label?: string; landedReason?: string; retiredReason?: string }
    | undefined,
  status: string,
): string {
  const why = current?.landedReason ?? current?.retiredReason;
  const word = handoffWord(status);
  return `${current?.label ?? previous.label ?? shortId(previous.id ?? "")} ${word}${why ? ` — ${why}` : ""} · focus handed off`;
}

function shouldSmartDefault(
  focusedSessionId: string | null,
  activeSessionId: string | null,
  preferredActiveIdRef: React.RefObject<string | null>,
  sessions: SessionsViewData["sessions"],
): boolean {
  return (
    focusedSessionId === null &&
    !(
      activeSessionId &&
      activeSessionId === preferredActiveIdRef.current &&
      sessions.some((session) => session.id === activeSessionId)
    )
  );
}

export function useFocusHandoff(
  refs: SessionsViewRefs,
  state: ReturnType<typeof useSessionsViewState>,
  data: SessionsViewData,
  handlers: SessionsViewHandlers,
) {
  const { focusSession, rememberLiveSession } = handlers;
  const setHandoff = state.setHandoff;
  // Smart-default focus (never an empty landing) + focus handoff. An extant landed row
  // remains deliberately inspectable; once the focused row is actually removed (for example by
  // landed cleanup), focus must move to the smart live default instead of pointing at nothing.
  // Remember the last-known human LABEL of the focused seat too, so the focus-handoff banner
  // names the seat the operator knows (its human label) instead of leading with its raw UUID once
  // the row is gone (its label is no longer in `sessions` at handoff time).
  useEffect(() => {
    const previous = refs.lastFocusRef.current;
    const { current, status } = focusStatusOf(data.focusedSessionId, data.sessions);
    if (needsFocusHandoff(previous, data.focusedSessionId, status)) {
      setHandoff(handoffMessage(previous, current, status));
      // Retire residuals are NOT captured here: the focus-independent sweep
      // (startRetireResidualSweep) owns that for every row, focused or not.
      const next = smartDefaultFocus(data.sessions);
      rememberLiveSession(next);
      sessionCockpitStore.getState().setFocusedSession(next);
    } else if (
      shouldSmartDefault(
        data.focusedSessionId,
        data.activeSessionId,
        refs.preferredActiveIdRef,
        data.sessions,
      )
    ) {
      const next = smartDefaultFocus(data.sessions);
      if (next !== null) focusSession(next);
    }
    const nextFocusedId = sessionCockpitStore.getState().focusedSessionId;
    refs.lastFocusRef.current = {
      id: nextFocusedId,
      status,
      label: nextFocusedId
        ? (data.sessions.find((session) => session.id === nextFocusedId)?.label ??
          (nextFocusedId === previous.id ? previous.label : null))
        : null,
    };
  }, [
    data.activeSessionId,
    focusSession,
    rememberLiveSession,
    data.sessions,
    data.focusedSessionId,
    refs,
    setHandoff,
  ]);
}

export function useSessionsViewMirrors(
  refs: SessionsViewRefs,
  state: ReturnType<typeof useSessionsViewState>,
  data: SessionsViewData,
) {
  // The catalog hydrator restores its preferred active row from localStorage. Mirror that exact
  // row into cockpit focus so reloads and cross-tab creates return to the operator's last chat.
  useEffect(() => {
    if (
      data.focusedSessionId === null &&
      data.activeSessionId &&
      data.activeSessionId === refs.preferredActiveIdRef.current &&
      data.activeSessionId !== data.focusedSessionId &&
      data.sessions.some((session) => session.id === data.activeSessionId)
    ) {
      sessionCockpitStore.getState().setFocusedSession(data.activeSessionId);
    }
  }, [data.activeSessionId, data.focusedSessionId, data.sessions, refs]);

  // Mirror the view-owned layout/palette facts into the cockpit store (design §4.3 skeleton).
  useEffect(() => {
    sessionCockpitStore
      .getState()
      .setLayout({ railCollapsed: state.railCollapsed, inspectorCollapsed: state.inspectorCollapsed });
  }, [state.railCollapsed, state.inspectorCollapsed]);
  useEffect(() => {
    sessionCockpitStore.getState().setPaletteOpen(state.palette.open);
  }, [state.palette.open]);
}

function switchRailSession(
  direction: 1 | -1,
  model: SessionsViewData["model"],
  focusedSessionId: string | null,
  focusSession: (id: string | null) => void,
) {
  const order = railCycleOrder(model);
  if (order.length === 0) return;
  const index = focusedSessionId ? order.indexOf(focusedSessionId) : -1;
  const next =
    index === -1
      ? direction === 1
        ? order[0]
        : order[order.length - 1]
      : order[(index + direction + order.length) % order.length];
  focusSession(next);
}

function buildCommandContext(
  state: ReturnType<typeof useSessionsViewState>,
  data: SessionsViewData,
  refs: SessionsViewRefs,
  handlers: SessionsViewHandlers,
  inspector: SessionsViewInspector,
): CommandContext {
  return {
    railCollapsed: state.railCollapsed,
    inspectorCollapsed: state.inspectorCollapsed,
    paletteOpen: state.palette.open,
    actions: {
      openPalette: handlers.openPalette,
      closePalette: handlers.closePalette,
      toggleRail: () =>
        state.railCollapsed
          ? refs.railRef.current?.expand()
          : refs.railRef.current?.collapse(),
      toggleInspector: () =>
        state.inspectorCollapsed
          ? inspector.expandInspector("operator")
          : inspector.collapseInspector("operator"),
      focusRegion: handlers.focusRegion,
      cycleRegion: handlers.cycleRegion,
      focusStageHeader: () => handlers.focusSelector(STAGE_HEADER_SELECTOR),
      focusTerminal: () => handlers.focusSelector(PTY_HOST_SELECTOR),
      // alt+↑/↓ cycles the rail order (spine → clusters → unattached, live rows only).
      switchSession: (direction) =>
        switchRailSession(
          direction,
          data.model,
          data.focusedSessionId,
          handlers.focusSession,
        ),
      // Cycle the REQUESTED effort through the live menu — no dialog; the chips carry
      // the async honesty story.
      cycleEffort: (direction) => {
        if (data.focusedSessionId) {
          cycleEffortRequested(data.focusedSessionId, direction);
        }
      },
      submitComposer: () => refs.composerRef.current?.submit(),
      popBackComposer: () => refs.composerRef.current?.popBack(),
    },
  };
}

export function useSessionsViewCommands(
  refs: SessionsViewRefs,
  state: ReturnType<typeof useSessionsViewState>,
  data: SessionsViewData,
  handlers: SessionsViewHandlers,
  inspector: SessionsViewInspector,
  active: boolean,
) {
  const buildContext = useCallback(
    () => buildCommandContext(state, data, refs, handlers, inspector),
    [state, data, refs, handlers, inspector],
  );
  refs.contextRef.current = buildContext;
  const getContext = useCallback(() => refs.contextRef.current(), [refs]);
  const dispatch = useCallback(
    (commandId: string) => {
      data.registry.run(commandId, getContext());
    },
    [getContext, data.registry],
  );
  useKeyboardZones({ active, dispatch });
  // Palette commands + the exact-turn interrupt chord: all registration lives in the hook so
  // this view keeps only its state, geometry, and layout surface.
  useSessionsPaletteCommands({
    registry: data.registry,
    focused: data.focused,
    setLaunch: state.setLaunch,
    setControlPopoverOpen: state.setControlPopoverOpen,
    openChatsLibrary: handlers.openChatsLibrary,
    closeChatsLibrary: handlers.closeChatsLibrary,
    toggleChatsDiagnostics: handlers.toggleChatsDiagnostics,
    chatsInterruptRef: data.chatsInterruptRef,
    chatsLibraryOpenRef: refs.chatsLibraryOpenRef,
    chatsDiagnosticsOpenRef: refs.chatsDiagnosticsOpenRef,
    treeView: data.treeView,
    rollup: data.rollup,
    sessions: data.sessions,
    model: data.model,
    focusSession: handlers.focusSession,
    rootRef: refs.rootRef,
  });
  return { registry: data.registry, getContext, dispatch };
}

export function useSessionsViewController(props: SessionsViewProps) {
  const refs = useSessionsViewRefs();
  const state = useSessionsViewState(refs);
  const selectors = useSessionsViewSelectors(props);
  const derived = useSessionsViewDerived(selectors);
  const data = { ...selectors, ...derived };
  const focusAndLibrary = useFocusAndLibraryHandlers(refs, state);
  const paletteAndRegion = usePaletteAndRegionHandlers(refs, state);
  const handlers = { ...focusAndLibrary, ...paletteAndRegion };
  const inspectorFocus = useInspectorFocusHandlers(refs);
  const inspector = useInspectorPanelActions(refs, state, inspectorFocus);
  useInspectorInitialSync(props.active, refs, state, inspector);
  const layout = useSessionsViewLayout(refs, state, data, inspector);
  useSessionsViewWatchers();
  useFocusHandoff(refs, state, data, handlers);
  useSessionsViewMirrors(refs, state, data);
  const commands = useSessionsViewCommands(
    refs,
    state,
    data,
    handlers,
    inspector,
    props.active,
  );
  return {
    refs,
    state,
    data,
    handlers,
    inspector,
    layout,
    commands,
    propsActive: props.active,
    propsSelectedLifecycleId: props.selectedLifecycleId,
    propsSelectedLeafKey: props.selectedLeafKey,
    propsContextMaster: props.contextMaster,
  };
}

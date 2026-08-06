// The canonical Chats cockpit: rail / stage / inspector as a react-resizable-panels group with
// the narrow-width rules (inspector auto-collapses <~1100px, rail <~900px — both reopenable) and

import { memo, useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Panel,
  PanelGroup,
  PanelResizeHandle,
  type ImperativePanelHandle,
} from "react-resizable-panels";

import { cx } from "../../../../styled-system/css";
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
  masterLabels,
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
import { seatVisualState } from "../../../data/stateGrammar";
import {
  cycleEffortRequested,
  startSetPromotionWatcher,
} from "../../../data/setClient";
import {
  hasUnackedSetAttention,
  queuedComposerHint,
} from "../../../data/setChips";
import {
  autoCollapseTransition,
  hasPersistedPanelLayout,
  INSPECTOR_AUTO_COLLAPSE_PX,
  PTY_MIN_COLS,
  RAIL_AUTO_COLLAPSE_PX,
  RAIL_FALLBACK_PERCENT,
  railDefaultPercent,
  stageBelowPtyFloor,
} from "../../../data/sessionLayout";
import { useActiveConversation } from "../../../data/conversation/store";
import { useSessions } from "../../../data/sessions";
import { shortId } from "../../../data/conversation/format";
import { useDashboard } from "../../../data/store";
import type { AgentPickupNode, TaskDocNode } from "../../../types/projection";
import { CockpitLiveRegions } from "../CockpitLiveRegions";
import { ChatContextBar, ChatSessionActions } from "../ChatContextBar";
import { CommandPalette } from "../CommandPalette";
import { FailedLaunchBanner } from "../FailedLaunchBanner";
import { InteractionBar } from "../InteractionBar";
import { LaunchFlow, type LaunchPrefill } from "../LaunchFlow";
import { LandedCleanupNotice } from "../LandedCleanupNotice";
import { ChatsStageBody } from "../ChatsStageBody";
import { ConversationWorkingLine } from "../conversation/ConversationWorkingLine";
import { useConversationInterrupt } from "../conversation/useConversationControls";
import { SeatInspector } from "../SeatInspector";
import { SessionRail } from "../SessionRail";
import { SessionStage } from "../SessionStage";
import { SetOutcomeToasts } from "../SetOutcomeToasts";
import { useKeyboardZones } from "../useKeyboardZones";
import { useSessionsPaletteCommands } from "./useSessionsPaletteCommands";
import { WorkingLine } from "../WorkingLine";
import {
  SessionComposer,
  type SessionComposerHandle,
} from "../../SessionComposer";
import { usePersistedFlag } from "../../file-viewer/usePersistedFlag";
import {
  INSPECTOR_OPEN_KEY,
  PANELS_AUTOSAVE_ID,
  RAIL_MAX_PERCENT,
  RAIL_MIN_PERCENT,
  floorChip,
  inspectorScroll,
  pane,
  paneHeading,
  reopenButton,
  resizeHandle,
  root,
  stagePane,
  workingLineSlot,
} from "./styles";


const EMPTY_DOCS: TaskDocNode[] = [];
const EMPTY_PICKUPS: AgentPickupNode[] = [];

export interface SessionsViewProps {
  active: boolean;
  selectedLifecycleId?: string;
  selectedLeafKey?: string;
  taskDocuments?: TaskDocNode[];
  contextMaster?: string;
}

function SessionsViewImpl({
  active,
  selectedLifecycleId,
  selectedLeafKey,
  taskDocuments: suppliedTaskDocuments,
  contextMaster,
}: SessionsViewProps) {
  const rootRef = useRef<HTMLDivElement>(null);
  const stageRef = useRef<HTMLElement>(null);
  const railRef = useRef<ImperativePanelHandle>(null);
  const inspectorRef = useRef<ImperativePanelHandle>(null);
  const inspectorToggleRef = useRef<HTMLButtonElement>(null);
  const lastWidthRef = useRef<number | null>(null);
  const paletteInvokerRef = useRef<HTMLElement | null>(null);
  const inspectorAutoCollapsedRef = useRef(false);
  const inspectorResponsiveTransitionRef = useRef<"collapse" | "expand" | null>(
    null,
  );
  // Panel storage may emit an initial callback before the preference-sync effect gets its turn.
  // That callback describes recovered geometry, not a new operator decision.
  const inspectorPreferenceSyncRef = useRef(true);
  const inspectorInitialPreferenceSyncRef = useRef(true);
  // Capture the preference before any component effect can write. A catalog hydrate with no saved
  // preference still assigns its first live row to `activeId`; that transport fallback must not
  // override the cockpit's attention-aware smart default.
  const preferredActiveIdRef = useRef(readLastActiveSessionId());

  const [railCollapsed, setRailCollapsed] = useState(false);
  // Deliberate operator intent and the panel's current geometry are separate facts. A responsive
  // collapse may hide an opted-in inspector, but it must never rewrite that opt-in: a narrow
  // reload can therefore recover it when the viewport widens again.
  const [inspectorIntentOpen, setInspectorIntentOpen] = usePersistedFlag(
    INSPECTOR_OPEN_KEY,
    false,
  );
  const [inspectorCollapsed, setInspectorCollapsed] =
    useState(!inspectorIntentOpen);
  const inspectorCollapsedRef = useRef(inspectorCollapsed);
  inspectorCollapsedRef.current = inspectorCollapsed;
  const [stageNarrow, setStageNarrow] = useState(false);
  // The VISIBLE pane's real column count: when a pane reports, the ~80-col floor chip
  // reflects the pane truth instead of the pixel estimate.
  const [ptyCols, setPtyCols] = useState<number | null>(null);
  const composerRef = useRef<SessionComposerHandle>(null);
  const [palette, setPalette] = useState<{
    open: boolean;
    page: PalettePage;
    initialQuery: string;
  }>({
    open: false,
    page: "commands",
    initialQuery: "",
  });

  const registry = useMemo(
    () => registerDefaultCommands(createCommandRegistry()),
    [],
  );

  // ── The shared session feed + cockpit state ─────────────────────────────────────────
  const sessions = useSessions((state) => state.sessions);
  const activeSessionId = useSessions((state) => state.activeId);
  const focusedSessionId = useSessionCockpit((state) => state.focusedSessionId);
  const treeView = useSessionCockpit((state) => state.orchestrationTreeView);
  const perSession = useSessionCockpit((state) => state.perSession);
  const projectedTaskDocuments = useDashboard(
    (state) => state.analytics?.taskDocuments ?? EMPTY_DOCS,
  );
  const taskDocuments = suppliedTaskDocuments ?? projectedTaskDocuments;
  const pickups = useDashboard((state) =>
    state.analytics ? state.analytics.agentPickups : EMPTY_PICKUPS,
  );
  const supervisorHeartbeat = useDashboard(
    (state) => state.supervisorHeartbeat,
  );
  const [handoff, setHandoff] = useState<string | null>(null);
  // The LaunchFlow dialog — opened from the palette, or pre-filled by the failed-launch
  // banner's 'Launch corrected…' (the refused pair, re-gated against the live catalog).
  const [launch, setLaunch] = useState<{
    open: boolean;
    prefill?: LaunchPrefill;
  }>({
    open: false,
  });
  // The ModelEffortControl popover state lives here so the palette commands open the SAME
  // popover the header trigger opens (one control, two surfaces).
  const [controlPopoverOpen, setControlPopoverOpen] = useState(false);
  // The structured Chats stage-mode toggles (in-stage history browser and the
  // default-off terminal-diagnostics drawer). Both live here + in the palette (design §12.6) and
  // reset on focus change. A focus-return token restores the invoker when the library closes (§14.1).
  const [chatsLibraryOpen, setChatsLibraryOpen] = useState(false);
  const [chatsDiagnosticsOpen, setChatsDiagnosticsOpen] = useState(false);
  const chatsLibraryReturnRef = useRef<HTMLElement | null>(null);
  const chatsDiagnosticsReturnRef = useRef<HTMLElement | null>(null);
  const chatsLibraryOpenRef = useRef(chatsLibraryOpen);
  chatsLibraryOpenRef.current = chatsLibraryOpen;
  const chatsDiagnosticsOpenRef = useRef(chatsDiagnosticsOpen);
  chatsDiagnosticsOpenRef.current = chatsDiagnosticsOpen;

  useEffect(() => startCockpitMirror(), []);
  // Retire residuals are captured for EVERY row — focus-independent — so an
  // unfocused seat's retireControlStopError still surfaces (and resurfaces after a reload).
  useEffect(() => startRetireResidualSweep(), []);
  // The promotion/drift watcher (turn-ended + focus re-GETs) and the assertive
  // failed/awaiting-input announcer — both refcounted, view-lifetime.
  useEffect(() => startSetPromotionWatcher(), []);
  useEffect(() => startSeatStateAnnouncer(), []);

  // The focused seat's conversation projection carries its OWN live
  // turn signal over SSE (sub-second), which the sweep-bounded catalog turn-state lags by ~10s. When
  // the projection shows a turn actively streaming, prefer it so the stage authorities (HeaderStrip
  // StateDot, StatusLine, WorkingLine) show a WORKING state instead of a settled-green `turn-ended`.
  // Honest fallback: a stale/disconnected projection is never trusted, and a terminal/fault/blocked
  // catalog state still wins (seatVisualState checks those first).
  const focusedLiveTurnWorking = useActiveConversation((state) => {
    if (focusedSessionId === null || focusedSessionId === undefined) return false;
    const projection = state.bySession[focusedSessionId];
    if (projection === undefined || projection.stream !== "live") return false;
    const status = projection.status;
    if (status === undefined || status.freshness.state === "stale") return false;
    const turn = status.turn.state;
    return turn === "working" || turn === "settling" || turn === "retrying" || turn === "compacting";
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
  // The WorkingLine slot's source-selection rule. A harness seat whose
  // conversation stream is LIVE gets the SSE-driven ConversationWorkingLine (sub-second, same
  // projection evidence as the stop control); legacy raw sessions and the SSE connect/reconnect
  // window keep the catalog-driven WorkingLine, which honestly bounds its own staleness.
  const focusedConversationLive = useActiveConversation((state) => {
    if (focusedSessionId === null || focusedSessionId === undefined) return false;
    return state.bySession[focusedSessionId]?.stream === "live";
  });
  // Exact-turn interrupt for the focused session (drives WorkingLine's stop control). A ref lets
  // the registered `conversation.stop` command + chord always read the latest availability/onStop
  // without re-registering on every projection tick.
  const chatsInterrupt = useConversationInterrupt(focusedSessionId ?? undefined);
  const chatsInterruptRef = useRef(chatsInterrupt);
  chatsInterruptRef.current = chatsInterrupt;

  const labels = useMemo(() => masterLabels(taskDocuments), [taskDocuments]);
  const model = useMemo(
    () => buildRailModel(sessions, { masterLabel: (key) => labels.get(key) }),
    [sessions, labels],
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

  const rememberLiveSession = useCallback((id: string | null) => {
    // `activeId` is the launch/gate route and persisted reload preference, so only a live row owns
    // it. Cockpit focus may still inspect a landed row without the next catalog hydrate fighting it.
    if (preferLiveSession(id)) preferredActiveIdRef.current = id;
  }, []);

  const focusSession = useCallback(
    (id: string | null) => {
      setHandoff(null);
      // A focus switch never carries the previous seat's open control popover / stage mode along.
      setControlPopoverOpen(false);
      setChatsLibraryOpen(false);
      setChatsDiagnosticsOpen(false);
      rememberLiveSession(id);
      sessionCockpitStore.getState().setFocusedSession(id);
    },
    [rememberLiveSession],
  );

  // Open the in-stage history browser, capturing a stable focus-return token first (§14.1).
  const openChatsLibrary = useCallback(() => {
    const activeElement = document.activeElement;
    chatsLibraryReturnRef.current =
      activeElement instanceof HTMLElement ? activeElement : null;
    setChatsLibraryOpen(true);
  }, []);
  const closeChatsLibrary = useCallback(() => {
    setChatsLibraryOpen(false);
    const invoker = chatsLibraryReturnRef.current;
    if (invoker?.isConnected) invoker.focus();
    chatsLibraryReturnRef.current = null;
  }, []);

  // Diagnostics drawer focus-return token (§12.6/§14.1): capture on open from any invoker
  // (toolbar button, reconnect action, palette), restore on close so focus never drops to <body>.
  const toggleChatsDiagnostics = useCallback((open: boolean) => {
    if (open) {
      const activeElement = document.activeElement;
      chatsDiagnosticsReturnRef.current =
        activeElement instanceof HTMLElement ? activeElement : null;
      setChatsDiagnosticsOpen(true);
    } else {
      setChatsDiagnosticsOpen(false);
      const invoker = chatsDiagnosticsReturnRef.current;
      if (invoker?.isConnected) invoker.focus();
      chatsDiagnosticsReturnRef.current = null;
    }
  }, []);

  // The catalog hydrator restores its preferred active row from localStorage. Mirror that exact
  // row into cockpit focus so reloads and cross-tab creates return to the operator's last chat.
  useEffect(() => {
    if (
      focusedSessionId === null &&
      activeSessionId &&
      activeSessionId === preferredActiveIdRef.current &&
      activeSessionId !== focusedSessionId &&
      sessions.some((session) => session.id === activeSessionId)
    ) {
      sessionCockpitStore.getState().setFocusedSession(activeSessionId);
    }
  }, [activeSessionId, focusedSessionId, sessions]);

  // Smart-default focus (never an empty landing) + focus handoff. An extant landed row
  // remains deliberately inspectable; once the focused row is actually removed (for example by
  // landed cleanup), focus must move to the smart live default instead of pointing at nothing.
  // Remember the last-known human LABEL of the focused seat too, so the focus-handoff banner
  // names the seat the operator knows (its human label) instead of leading with its raw UUID once the
  // row is gone (its label is no longer in `sessions` at handoff time).
  const lastFocusRef = useRef<{ id: string | null; status: string; label: string | null }>({
    id: null,
    status: "none",
    label: null,
  });
  useEffect(() => {
    const previous = lastFocusRef.current;
    const current = focusedSessionId
      ? sessions.find((session) => session.id === focusedSessionId)
      : undefined;
    const status = current
      ? current.status ?? "running"
      : focusedSessionId
        ? "gone"
        : "none";
    const stoppedUnderFocus =
      previous.status === "running" && status !== "running";
    const focusedRowRemoved = previous.status !== "none" && status === "gone";
    if (
      previous.id !== null &&
      previous.id === focusedSessionId &&
      (stoppedUnderFocus || focusedRowRemoved)
    ) {
      const why = current?.landedReason ?? current?.retiredReason;
      const word =
        status === "landed"
          ? "landed"
          : status === "gone"
            ? "ended"
            : "retired";
      setHandoff(
        `${current?.label ?? previous.label ?? shortId(previous.id)} ${word}${why ? ` — ${why}` : ""} · focus handed off`,
      );
      // Retire residuals are NOT captured here: the focus-independent sweep
      // (startRetireResidualSweep) owns that for every row, focused or not.
      const next = smartDefaultFocus(sessions);
      rememberLiveSession(next);
      sessionCockpitStore.getState().setFocusedSession(next);
    } else if (
      focusedSessionId === null &&
      !(
        activeSessionId &&
        activeSessionId === preferredActiveIdRef.current &&
        sessions.some((session) => session.id === activeSessionId)
      )
    ) {
      const next = smartDefaultFocus(sessions);
      if (next !== null) focusSession(next);
    }
    const nextFocusedId = sessionCockpitStore.getState().focusedSessionId;
    lastFocusRef.current = {
      id: nextFocusedId,
      status,
      label: nextFocusedId
        ? (sessions.find((session) => session.id === nextFocusedId)?.label ??
          (nextFocusedId === previous.id ? previous.label : null))
        : null,
    };
  }, [
    activeSessionId,
    focusSession,
    rememberLiveSession,
    sessions,
    focusedSessionId,
  ]);

  // Mirror the view-owned layout/palette facts into the cockpit store (design §4.3 skeleton).
  useEffect(() => {
    sessionCockpitStore
      .getState()
      .setLayout({ railCollapsed, inspectorCollapsed });
  }, [railCollapsed, inspectorCollapsed]);
  useEffect(() => {
    sessionCockpitStore.getState().setPaletteOpen(palette.open);
  }, [palette.open]);


  // Palette commands + the exact-turn interrupt chord: all registration lives in the hook so
  // this view keeps only its state, geometry, and layout surface.
  useSessionsPaletteCommands({
    registry,
    focused,
    setLaunch,
    setControlPopoverOpen,
    openChatsLibrary,
    closeChatsLibrary,
    toggleChatsDiagnostics,
    chatsInterruptRef,
    chatsLibraryOpenRef,
    chatsDiagnosticsOpenRef,
    treeView,
    rollup,
    sessions,
    model,
    focusSession,
    rootRef,
  });


  const focusSelector = useCallback((selector: string) => {
    const target = rootRef.current?.querySelector<HTMLElement>(selector);
    target?.focus();
  }, []);

  const focusRegion = useCallback(
    (region: FocusRegion) => {
      // The rail's roving target is its selected row; the always-present rail container is only
      // the empty/collapsed-folder fallback. Query it explicitly before the generic first target.
      if (region === "rail") {
        const selected = rootRef.current?.querySelector<HTMLElement>(
          '[data-region="rail"] [data-selected="true"]',
        );
        if (selected) {
          selected.focus();
          return;
        }
      }
      focusSelector(regionTargetSelector(region));
    },
    [focusSelector],
  );

  // The F6 cycle: rail → stage → inspector → status line, collapsed panels excluded (design §5.3).
  const cycleRegion = useCallback(
    (direction: 1 | -1) => {
      const available = FOCUS_REGIONS.filter(
        (region) =>
          (region !== "rail" || !railCollapsed) &&
          (region !== "inspector" || !inspectorCollapsed),
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
    [focusRegion, inspectorCollapsed, railCollapsed],
  );

  const openPalette = useCallback(
    (page: PalettePage = "commands", initialQuery = "") => {
      const activeElement = document.activeElement;
      setPalette((current) => {
        // Keep the ORIGINAL invoker across an in-palette page switch so close returns focus there.
        if (!current.open)
          paletteInvokerRef.current =
            activeElement instanceof HTMLElement ? activeElement : null;
        return { open: true, page, initialQuery };
      });
    },
    [],
  );

  const closePalette = useCallback(() => {
    setPalette({ open: false, page: "commands", initialQuery: "" });
    // Palette close returns focus to its invoker (when it is still in the document).
    const invoker = paletteInvokerRef.current;
    if (invoker?.isConnected) invoker.focus();
    paletteInvokerRef.current = null;
  }, []);

  const restoreFocusFromInspector = useCallback(() => {
    const activeElement = document.activeElement;
    if (!(activeElement instanceof HTMLElement)) return;
    const inspector =
      rootRef.current?.querySelector<HTMLElement>("#chats-inspector");
    const handle = rootRef.current?.querySelector<HTMLElement>(
      '[data-testid="sessions-handle-inspector"]',
    );
    if (inspector?.contains(activeElement) || handle === activeElement) {
      inspectorToggleRef.current?.focus();
    }
  }, []);

  const armResponsiveInspectorTransition = useCallback(
    (transition: "collapse" | "expand") => {
      inspectorResponsiveTransitionRef.current = transition;
      // An imperative no-op does not emit onCollapse/onExpand. Do not let its marker leak into a
      // later operator drag/keystroke and misclassify deliberate intent.
      window.requestAnimationFrame(() => {
        if (inspectorResponsiveTransitionRef.current === transition) {
          inspectorResponsiveTransitionRef.current = null;
        }
      });
    },
    [],
  );

  const collapseInspector = useCallback(
    (source: "operator" | "responsive") => {
      if (source === "operator") {
        inspectorPreferenceSyncRef.current = false;
        inspectorAutoCollapsedRef.current = false;
        setInspectorIntentOpen(false);
      } else {
        inspectorAutoCollapsedRef.current = true;
      }
      inspectorCollapsedRef.current = true;
      setInspectorCollapsed(true);
      restoreFocusFromInspector();
      if (!inspectorRef.current || inspectorRef.current.isCollapsed()) return;
      if (source === "responsive") armResponsiveInspectorTransition("collapse");
      inspectorRef.current?.collapse();
    },
    [
      armResponsiveInspectorTransition,
      restoreFocusFromInspector,
      setInspectorIntentOpen,
    ],
  );

  const expandInspector = useCallback(
    (source: "operator" | "responsive") => {
      if (source === "operator") {
        inspectorPreferenceSyncRef.current = false;
        inspectorAutoCollapsedRef.current = false;
        setInspectorIntentOpen(true);
      }
      inspectorCollapsedRef.current = false;
      setInspectorCollapsed(false);
      if (!inspectorRef.current || inspectorRef.current.isExpanded()) return;
      if (source === "responsive") armResponsiveInspectorTransition("expand");
      inspectorRef.current?.expand();
    },
    [armResponsiveInspectorTransition, setInspectorIntentOpen],
  );

  const handleInspectorCollapse = useCallback(() => {
    const responsive = inspectorResponsiveTransitionRef.current === "collapse";
    if (responsive) inspectorResponsiveTransitionRef.current = null;
    inspectorCollapsedRef.current = true;
    setInspectorCollapsed(true);
    restoreFocusFromInspector();
    if (!responsive && !inspectorPreferenceSyncRef.current) {
      // Divider drag / separator keyboard collapse is an operator action just like the toggle.
      inspectorAutoCollapsedRef.current = false;
      setInspectorIntentOpen(false);
    }
  }, [restoreFocusFromInspector, setInspectorIntentOpen]);

  const handleInspectorExpand = useCallback(() => {
    const responsive = inspectorResponsiveTransitionRef.current === "expand";
    if (responsive) inspectorResponsiveTransitionRef.current = null;
    // Panel autosave can replay a prior open layout after the narrow responsive decision. While
    // recovery is armed, that replay is geometry noise: only a width recovery or explicit open
    // clears the arm. Reassert the transient collapse without touching persisted intent.
    if (inspectorAutoCollapsedRef.current && inspectorCollapsedRef.current) {
      window.requestAnimationFrame(() => collapseInspector("responsive"));
      return;
    }
    inspectorCollapsedRef.current = false;
    setInspectorCollapsed(false);
    if (!responsive && !inspectorPreferenceSyncRef.current) {
      inspectorAutoCollapsedRef.current = false;
      setInspectorIntentOpen(true);
    }
  }, [collapseInspector, setInspectorIntentOpen]);

  // `defaultSize={0}` is not sufficient when panel storage has a prior width. Deliberate intent
  // owns initial visibility; panel storage owns only the width once opened. On the FIRST mount,
  // reconcile against the already-painted root width too: a narrow reload must start transiently
  // collapsed while retaining the open preference for later recovery. Later explicit opens below
  // the threshold bypass this initial-only rule and remain reopenable.
  useEffect(() => {
    const width = rootRef.current?.clientWidth ?? 0;
    // Chats is a keep-alive route. Mounting while Operations is still active reports width 0;
    // becoming the visible route is the first meaningful geometry boundary, not that hidden mount.
    if (!active || width <= 0) return;
    const initial = inspectorInitialPreferenceSyncRef.current;
    inspectorInitialPreferenceSyncRef.current = false;
    inspectorPreferenceSyncRef.current = true;
    if (
      initial &&
      inspectorIntentOpen &&
      width > 0 &&
      width < INSPECTOR_AUTO_COLLAPSE_PX
    ) {
      collapseInspector("responsive");
    } else if (inspectorIntentOpen && !inspectorAutoCollapsedRef.current) {
      inspectorRef.current?.expand();
    } else if (!inspectorIntentOpen) {
      inspectorRef.current?.collapse();
    }
    window.requestAnimationFrame(() => {
      inspectorPreferenceSyncRef.current = false;
    });
  }, [active, collapseInspector, inspectorIntentOpen]);

  const buildContext = useCallback(
    (): CommandContext => ({
      railCollapsed,
      inspectorCollapsed,
      paletteOpen: palette.open,
      actions: {
        openPalette,
        closePalette,
        toggleRail: () =>
          railCollapsed
            ? railRef.current?.expand()
            : railRef.current?.collapse(),
        toggleInspector: () =>
          inspectorCollapsed
            ? expandInspector("operator")
            : collapseInspector("operator"),
        focusRegion,
        cycleRegion,
        focusStageHeader: () => focusSelector(STAGE_HEADER_SELECTOR),
        focusTerminal: () => focusSelector(PTY_HOST_SELECTOR),
        // alt+↑/↓ cycles the rail order (spine → clusters → unattached, live rows only).
        switchSession: (direction) => {
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
        },
        // Cycle the REQUESTED effort through the live menu — no dialog; the chips carry
        // the async honesty story.
        cycleEffort: (direction) => {
          if (focusedSessionId)
            cycleEffortRequested(focusedSessionId, direction);
        },
        submitComposer: () => composerRef.current?.submit(),
        popBackComposer: () => composerRef.current?.popBack(),
      },
    }),
    [
      closePalette,
      collapseInspector,
      cycleRegion,
      expandInspector,
      focusRegion,
      focusSelector,
      focusSession,
      focusedSessionId,
      inspectorCollapsed,
      model,
      openPalette,
      palette.open,
      railCollapsed,
    ],
  );
  const contextRef = useRef(buildContext);
  contextRef.current = buildContext;
  const getContext = useCallback(() => contextRef.current(), []);

  const dispatch = useCallback(
    (commandId: string) => {
      registry.run(commandId, getContext());
    },
    [getContext, registry],
  );

  useKeyboardZones({ active, dispatch });

  // The ~80-col floor chip (design §2): the chip must always reflect the STAGE's actual
  // width — it is re-measured from every path that can change it (root resizes via the observer
  // below, and panel-layout changes via the PanelGroup onLayout callback), so a divider drag or a
  // palette-driven collapse can neither miss a squeeze nor leave a stale false alarm.
  const measureStage = useCallback(() => {
    setStageNarrow(stageBelowPtyFloor(stageRef.current?.clientWidth ?? 0));
  }, []);

  // ~280px rail default: react-resizable-panels is percentage-only,
  // so on the FIRST real width measurement — and only when the user has no persisted layout —
  // the rail is resized to the percentage equivalent of the design's ~280px target. One-shot;
  // the persisted layout (autoSaveId) owns everything afterwards.
  const railCalibratedRef = useRef(false);
  const calibrateRail = useCallback((rootWidth: number) => {
    if (railCalibratedRef.current || rootWidth <= 0) return;
    railCalibratedRef.current = true;
    if (hasPersistedPanelLayout(PANELS_AUTOSAVE_ID)) return;
    railRef.current?.resize(
      railDefaultPercent(rootWidth, RAIL_MIN_PERCENT, RAIL_MAX_PERCENT),
    );
  }, []);

  // Panel-layout changes (divider drags, collapse/expand — including palette-driven ones) never
  // resize the view ROOT, so they re-measure the stage here.
  const handlePanelLayout = useCallback(() => {
    measureStage();
    calibrateRail(rootRef.current?.clientWidth ?? 0);
  }, [calibrateRail, measureStage]);

  // Narrow-width rules: rail behavior is unchanged. The inspector defaults closed and only
  // auto-reopens after a width recovery when that same width transition closed it; a deliberate
  // default close is never undone by a resize.
  useEffect(() => {
    const node = rootRef.current;
    const stage = stageRef.current;
    if (!node) return undefined;
    const measure = () => {
      measureStage();
      const width = node.clientWidth;
      if (!width) return; // hidden keep-alive layer (display:none) — never react to a 0-measure
      calibrateRail(width);
      const previous = lastWidthRef.current;
      lastWidthRef.current = width;
      const railMove = autoCollapseTransition(
        previous,
        width,
        RAIL_AUTO_COLLAPSE_PX,
      );
      // The rail owns the only chat-creation entrance. An empty narrow cockpit must keep it
      // directly actionable; once a chat exists, the normal responsive collapse policy applies.
      if (sessions.length === 0) {
        if (railCollapsed) railRef.current?.expand();
      } else if (railMove === "collapse") railRef.current?.collapse();
      else if (railMove === "expand") railRef.current?.expand();
      const inspectorMove = autoCollapseTransition(
        previous,
        width,
        INSPECTOR_AUTO_COLLAPSE_PX,
      );
      if (inspectorMove === "collapse" && inspectorIntentOpen) {
        collapseInspector("responsive");
      } else if (
        inspectorMove === "expand" &&
        inspectorIntentOpen &&
        inspectorAutoCollapsedRef.current
      ) {
        inspectorAutoCollapsedRef.current = false;
        expandInspector("responsive");
      }
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(node);
    // The stage element itself is observed too: a divider drag changes the stage width with NO
    // root resize, and the observer callback re-runs measureStage first.
    if (stage) observer.observe(stage);
    return () => observer.disconnect();
  }, [
    calibrateRail,
    collapseInspector,
    expandInspector,
    inspectorIntentOpen,
    measureStage,
    railCollapsed,
    sessions.length,
  ]);

  return (
    <div
      ref={rootRef}
      className={cx(root, "sessions--view")}
      data-view="sessions"
      data-testid="sessions-view"
    >
      <PanelGroup
        direction="horizontal"
        autoSaveId={PANELS_AUTOSAVE_ID}
        onLayout={handlePanelLayout}
      >
        <Panel
          ref={railRef}
          collapsible
          collapsedSize={0}
          // The 1280px-reference fallback; the first real measurement calibrates toward ~280px
          // (calibrateRail above) unless a persisted layout exists.
          defaultSize={RAIL_FALLBACK_PERCENT}
          minSize={RAIL_MIN_PERCENT}
          maxSize={RAIL_MAX_PERCENT}
          onCollapse={() => setRailCollapsed(true)}
          onExpand={() => setRailCollapsed(false)}
          order={1}
        >
          <aside
            className={cx(pane, "sessions__rail")}
            // A collapsed rail leaves NO residual sliver: display:none removes the aside's own
            // padding/border box (~21px) entirely, so the 0px panel is truly empty, not a dead strip.
            // The drag min stays at RAIL_MIN_PERCENT (12%); below it the panel snaps fully collapsed and
            // the ☰ rail chip (StatusLine) plus the in-place resize handle are the reopen affordances.
            style={railCollapsed ? { display: "none" } : undefined}
            aria-hidden={railCollapsed}
            data-region="rail"
            data-testid="sessions-rail"
            aria-label="Chat rail"
          >
            <h2 className={paneHeading}>Chats</h2>
            <ChatContextBar
              selectedLifecycleId={selectedLifecycleId}
              onLaunchChat={() => setLaunch({ open: true })}
              onSessionOpened={focusSession}
            />
            <SessionRail
              onFocusSession={(sessionId) => {
                // Clicking a chat in the rail lands the CURSOR in the chat
                // input — the composer's focus target for controlled seats, the PTY host for
                // raw terminals. Deferred a frame so the stage has re-rendered for the new seat.
                focusSession(sessionId);
                window.requestAnimationFrame(() => {
                  const stageTarget = document.querySelector<HTMLElement>(
                    regionTargetSelector("stage"),
                  );
                  const target =
                    stageTarget ?? document.querySelector<HTMLElement>(PTY_HOST_SELECTOR);
                  target?.focus();
                });
              }}
              focusedSessionId={focusedSessionId}
              model={model}
              rollup={rollup}
            />
          </aside>
        </Panel>
        <PanelResizeHandle
          className={resizeHandle}
          aria-label="Resize chat rail"
          data-testid="sessions-handle-rail"
        />
        {/* An explicit defaultSize on EVERY panel keeps the group's layout state complete before
            any DOM measurement — the imperative collapse/expand handles depend on it. */}
        <Panel
          defaultSize={inspectorIntentOpen ? 54 : 78}
          minSize={35}
          order={2}
        >
          <section
            ref={stageRef}
            className={cx(stagePane, "sessions__stage")}
            data-region="stage"
            data-testid="sessions-stage"
            aria-label="Chat stage"
          >
            <SessionStage
              focused={focused}
              cockpit={focused ? perSession[focused.id] : undefined}
              controlPopover={{
                open: controlPopoverOpen,
                onOpenChange: setControlPopoverOpen,
              }}
              handoff={handoff}
              headerActions={
                // The inspector/rail toggles live on the title row's
                // right — the StatusLine bar they sat on is removed (declutter).
                <>
                  <ChatSessionActions
                    focused={focused}
                    selectedLeafKey={selectedLeafKey}
                    taskDocuments={taskDocuments}
                    contextMaster={contextMaster}
                    onBrowseHistory={openChatsLibrary}
                  />
                  {railCollapsed ? (
                    <button
                      type="button"
                      className={reopenButton}
                      onClick={() => railRef.current?.expand()}
                      data-testid="sessions-reopen-rail"
                    >
                      ☰ rail
                    </button>
                  ) : null}
                  <button
                    ref={inspectorToggleRef}
                    type="button"
                    className={reopenButton}
                    aria-controls="chats-inspector"
                    aria-expanded={!inspectorCollapsed}
                    onClick={() => {
                      if (inspectorCollapsed) expandInspector("operator");
                      else collapseInspector("operator");
                    }}
                    data-testid="sessions-toggle-inspector"
                  >
                    {inspectorCollapsed ? "◫ show inspector" : "◫ hide inspector"}
                  </button>
                </>
              }
              headerExtra={
                // With a live pane the chip reflects the pane's REAL column count; the pixel
                // estimate only covers the pane-less stage.
                (ptyCols !== null ? ptyCols < PTY_MIN_COLS : stageNarrow) ? (
                  <span
                    className={floorChip}
                    data-testid="sessions-pty-floor-chip"
                    title={
                      ptyCols !== null
                        ? `The pane is ${ptyCols} columns wide (< ${PTY_MIN_COLS}) — a squeezed hosted TUI is a layout fact, not harness misbehavior.`
                        : `The stage is narrower than ~${PTY_MIN_COLS} columns — a squeezed hosted TUI is a layout fact, not harness misbehavior.`
                    }
                  >
                    {ptyCols !== null
                      ? `pane ${ptyCols} cols (< ${PTY_MIN_COLS})`
                      : `pane narrower than ~${PTY_MIN_COLS} cols`}
                  </span>
                ) : null
              }
            >
              {/* Ending a chat produces NO stacked notice —
                  StopResidualNotes is unmounted; stop details stay recorded in the lifecycle
                  store for the Inspector/debug surfaces. */}
              {/* A focused FAILED launch renders its refusal verbatim — ABOVE the pty
                  surface; never hidden, never auto-retried;
                  Retire / 'Launch corrected…' are the only actions. */}
              {focusedLive && focused.controlState === "failed" ? (
                <FailedLaunchBanner
                  session={focused}
                  onLaunchCorrected={(prefill) =>
                    setLaunch({ open: true, prefill })
                  }
                />
              ) : null}
              {/* The structured one-roof body replaces the unconditional PTY. It
                  hosts the default ConversationSurface, the in-stage history library, and the
                  default-off read-only terminal-diagnostics drawer; a legacy-raw session keeps its
                  interactive PTY as the primary body inside it. */}
              <ChatsStageBody
                focused={focused}
                onVisibleCols={setPtyCols}
                libraryOpen={chatsLibraryOpen}
                onCloseLibrary={closeChatsLibrary}
                diagnosticsOpen={chatsDiagnosticsOpen}
                onToggleDiagnostics={toggleChatsDiagnostics}
                onSessionOpened={focusSession}
                viewActive={active}
              />
              {/* The turn theater sits where the eye
                  waits for the next message — under the conversation, above the composer
                  (the bottom-left "chat is still working" spot) — not in the stage's top
                  chrome nobody watches mid-turn. Same source-selection rule. */}
              <div
                data-slot="working-line"
                className={workingLineSlot}
                data-testid="stage-working-line-slot"
              >
                {focusedLive ? (
                  focused.kind === "harness" && focusedConversationLive ? (
                    <ConversationWorkingLine sessionId={focused.id} />
                  ) : (
                    <WorkingLine
                      session={focused}
                      cockpit={perSession[focused.id]}
                      // Only the raw-terminal path (no composer) keeps a line-hosted
                      // stop; controlled seats host it in the composer beside send.
                      interrupt={
                        focused.kind === "terminal" ? chatsInterrupt : undefined
                      }
                    />
                  )
                ) : null}
              </div>
              {focusedLive ? (
                <InteractionBar session={focused} composerRef={composerRef} />
              ) : null}
              {/* A raw terminal seat gets NO
                  dashboard composer — the vendor TUI owns input there, and the dead editor +
                  its two explanation bars only shrank the terminal. Controlled seats keep it. */}
              {focusedLive && focused.kind !== "terminal" ? (
                <SessionComposer
                  ref={composerRef}
                  session={focused}
                  queuedSetHint={queuedComposerHint(perSession[focused.id])}
                  onSlashAtLineStart={() => openPalette("commands", "/")}
                  onEscape={() => focusSelector(STAGE_HEADER_SELECTOR)}
                  // The ⏹ stop docks beside send — SSE-preferred working signal, catalog
                  // fallback, the same precedence the WorkingLine slot's source-selection uses.
                  interrupt={chatsInterrupt}
                  turnWorking={
                    focusedLiveTurnWorking ||
                    seatVisualState(focused).key === "working"
                  }
                />
              ) : null}
            </SessionStage>
          </section>
        </Panel>
        <PanelResizeHandle
          className={resizeHandle}
          aria-label="Resize inspector"
          aria-hidden={inspectorCollapsed}
          disabled={inspectorCollapsed}
          tabIndex={inspectorCollapsed ? -1 : 0}
          style={
            inspectorCollapsed ? { visibility: "hidden", width: 0 } : undefined
          }
          data-testid="sessions-handle-inspector"
        />
        <Panel
          ref={inspectorRef}
          collapsible
          collapsedSize={0}
          defaultSize={inspectorIntentOpen ? 24 : 0}
          minSize={14}
          maxSize={40}
          onCollapse={handleInspectorCollapse}
          onExpand={handleInspectorExpand}
          order={3}
        >
          <aside
            id="chats-inspector"
            className={cx(pane, "sessions__inspector")}
            style={inspectorCollapsed ? { visibility: "hidden" } : undefined}
            aria-hidden={inspectorCollapsed}
            data-region="inspector"
            data-testid="sessions-inspector"
            aria-label="Inspector"
          >
            <h2 className={paneHeading}>Evidence · Capabilities · Bus</h2>
            {/* The focused seat's provenance/outcome card — the tabbed inspector
                (Evidence · Capabilities · Bus) replaces this pane. */}
            <div className={inspectorScroll} data-focus-target tabIndex={-1}>
              <SeatInspector
                session={focused}
                cockpit={focused ? perSession[focused.id] : undefined}
                pickups={pickups}
                heartbeat={supervisorHeartbeat}
                visible={active && !inspectorCollapsed}
              />
            </div>
          </aside>
        </Panel>
      </PanelGroup>
      <LandedCleanupNotice />
      {/* The StatusLine bar is REMOVED — every fact it held
          was duplicated (seat/harness/ws/quiet live in the HeaderStrip; poll health surfaces as
          an exception through the attention system) and its actions moved to the title row. */}
      <CommandPalette
        open={palette.open}
        page={palette.page}
        initialQuery={palette.initialQuery}
        registry={registry}
        getContext={getContext}
        onClose={closePalette}
        onPage={(page) => setPalette({ open: true, page, initialQuery: "" })}
      />
      <LaunchFlow
        open={launch.open}
        prefill={launch.prefill}
        sessions={sessions}
        lifecycleId={selectedLifecycleId}
        onClose={() => setLaunch({ open: false })}
        onFocusSession={focusSession}
      />
      {/* Unfocused set outcomes persist until explicitly marked seen; live regions. */}
      <SetOutcomeToasts
        sessions={sessions}
        focusedSessionId={focusedSessionId}
        onFocusSession={focusSession}
      />
      <CockpitLiveRegions />
    </div>
  );
}

// Memoized to bound tab-switch CPU cost: a keep-alive cockpit layer — the shell re-renders on every
// view switch, and the memo gate skips this whole subtree unless `active` (or another prop)
// actually changed; the cockpit's own store subscriptions still drive its updates.
export const SessionsView = memo(SessionsViewImpl);

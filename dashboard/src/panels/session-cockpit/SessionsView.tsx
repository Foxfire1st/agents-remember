// The sessions cockpit view (260715-FEUI-L1 S2 shell + 260715-FEUI-L2): rail / stage / inspector
// as a react-resizable-panels group with the narrow-width rules (inspector auto-collapses
// <~1100px, rail <~900px — both reopenable) and the ~80-col PTY floor hint chip. L2 fills the
// rail (SessionRail — ruled role hierarchy + fleet attention), the stage container + HeaderStrip
// (empty ModelEffortControl slot for L4, reserved WorkingLine slot for L6), and the focused-seat
// inspector card (L7 replaces it with the tabbed inspector). The PTY and reliable composer are
// keyboard-zone anchors. The view root carries [data-view="sessions"]:
// the WebTUI scope root (S1) and the keyboard layer's home.
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Panel,
  PanelGroup,
  PanelResizeHandle,
  type ImperativePanelHandle,
} from "react-resizable-panels";

import { css, cx } from "../../../styled-system/css";
import { startCatalogPollDriver } from "../../data/catalogPoll";
import {
  createCommandRegistry,
  registerDefaultCommands,
  type CommandContext,
  type PalettePage,
} from "../../data/commands";
import {
  FOCUS_REGIONS,
  nextRegion,
  PTY_HOST_SELECTOR,
  regionTargetSelector,
  STAGE_HEADER_SELECTOR,
  type FocusRegion,
} from "../../data/keymap/focus";
import {
  attentionRollup,
  buildRailModel,
  criticalBusSessionIds,
  interactionPromptPreview,
  jumpToAttentionTarget,
  masterLabels,
  railCycleOrder,
  smartDefaultFocus,
  waitingSeats,
} from "../../data/railModel";
import { startSeatStateAnnouncer } from "../../data/announcer";
import { sessionCockpitStore, startCockpitMirror, useSessionCockpit } from "../../data/sessionCockpitStore";
import { startRetireResidualSweep } from "../../data/sessionLifecycle";
import { cycleEffortRequested, startSetPromotionWatcher } from "../../data/setClient";
import { hasUnackedSetAttention, queuedComposerHint } from "../../data/setChips";
import { seatVisualState } from "../../data/stateGrammar";
import {
  autoCollapseTransition,
  hasPersistedPanelLayout,
  INSPECTOR_AUTO_COLLAPSE_PX,
  PTY_MIN_COLS,
  RAIL_AUTO_COLLAPSE_PX,
  RAIL_FALLBACK_PERCENT,
  railDefaultPercent,
  stageBelowPtyFloor,
} from "../../data/sessionLayout";
import { useSessions } from "../../data/sessions";
import { useDashboard } from "../../data/store";
import type { AgentPickupNode, TaskDocNode } from "../../types/projection";
import { CockpitLiveRegions } from "./CockpitLiveRegions";
import { CommandPalette } from "./CommandPalette";
import { FailedLaunchBanner } from "./FailedLaunchBanner";
import { InteractionBar } from "./InteractionBar";
import { LaunchFlow, type LaunchPrefill } from "./LaunchFlow";
import { STOP_TURN_DISABLED_REASON } from "./lifecycleCopy";
import { PtySurface } from "./PtySurface";
import { SeatInspector } from "./SeatInspector";
import { endLanded, SessionRail } from "./SessionRail";
import { SessionStage } from "./SessionStage";
import { SetOutcomeToasts } from "./SetOutcomeToasts";
import { StopResidualNotes } from "./StopResidualNotes";
import { useKeyboardZones } from "./useKeyboardZones";
import { WorkingLine } from "./WorkingLine";
import { SessionComposer, type SessionComposerHandle } from "../SessionComposer";

const root = css({
  position: "relative", // anchors the palette overlay inside the scope root
  display: "flex",
  flexDirection: "column",
  flex: "1",
  minHeight: "0",
  minWidth: "0",
  gap: "0.4rem",
});
const pane = css({
  height: "100%",
  minWidth: "0",
  display: "flex",
  flexDirection: "column",
  gap: "0.4rem",
  background: "bgPanel",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "grid",
  borderRadius: "3px",
  padding: "0.5rem 0.6rem",
  overflow: "hidden",
});
const stagePane = css({
  height: "100%",
  minWidth: "0",
  display: "flex",
  flexDirection: "column",
  gap: "0.4rem",
  overflow: "hidden",
});
const floorChip = css({
  fontSize: "0.64rem",
  color: "amber",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "amber",
  borderRadius: "2px",
  paddingInline: "0.35rem",
});
const inspectorScroll = css({ flex: "1", minHeight: "0", overflowY: "auto" });
const ptyPlaceholder = css({
  flex: "1",
  minHeight: "0",
  display: "grid",
  placeItems: "center",
  padding: "0.8rem",
  color: "muted",
  fontSize: "0.74rem",
  lineHeight: "1.5",
  textAlign: "center",
  background: "bg",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "grid",
  borderRadius: "2px",
  _focusVisible: { outlineWidth: "1px", outlineStyle: "solid", outlineColor: "amber", outlineOffset: "1px" },
});
const statusLine = css({
  display: "flex",
  flexShrink: 0,
  alignItems: "baseline",
  gap: "0.8rem",
  flexWrap: "wrap",
  borderTopWidth: "1px",
  borderTopStyle: "solid",
  borderTopColor: "grid",
  paddingTop: "0.35rem",
  fontSize: "0.72rem",
  color: "muted",
});
const statusFocus = css({
  _focusVisible: { outlineWidth: "1px", outlineStyle: "solid", outlineColor: "amber", outlineOffset: "1px" },
});
const reopenButton = css({
  font: "inherit",
  fontSize: "0.68rem",
  letterSpacing: "0.06em",
  paddingInline: "0.45rem",
  paddingBlock: "0.06rem",
  background: "transparent",
  color: "muted",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "grid",
  borderRadius: "2px",
  cursor: "pointer",
  _hover: { color: "amber", borderColor: "amber" },
  _focusVisible: { outlineWidth: "1px", outlineStyle: "solid", outlineColor: "amber", outlineOffset: "1px" },
});
const resizeHandle = css({
  width: "3px",
  background: "grid",
  transition: "background 0.15s ease",
  _hover: { background: "amber" },
  "&[data-resize-handle-state='drag']": { background: "amber" },
});
const paneHeading = css({ flexShrink: 0 });

const PANELS_AUTOSAVE_ID = "cockpit.sessions.panels";
// The rail panel's percentage bounds — shared by the Panel props and the ~280px calibration.
const RAIL_MIN_PERCENT = 12;
const RAIL_MAX_PERCENT = 40;

const EMPTY_DOCS: TaskDocNode[] = [];
const EMPTY_PICKUPS: AgentPickupNode[] = [];

export function SessionsView({ active }: { active: boolean }) {
  const rootRef = useRef<HTMLDivElement>(null);
  const stageRef = useRef<HTMLElement>(null);
  const railRef = useRef<ImperativePanelHandle>(null);
  const inspectorRef = useRef<ImperativePanelHandle>(null);
  const lastWidthRef = useRef<number | null>(null);
  const paletteInvokerRef = useRef<HTMLElement | null>(null);

  const [railCollapsed, setRailCollapsed] = useState(false);
  const [inspectorCollapsed, setInspectorCollapsed] = useState(false);
  const [stageNarrow, setStageNarrow] = useState(false);
  // The VISIBLE pane's real column count (L6 R8): when a pane reports, the ~80-col floor chip
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

  const registry = useMemo(() => registerDefaultCommands(createCommandRegistry()), []);

  // ── L2: the shared session feed + cockpit state ─────────────────────────────────────────
  const sessions = useSessions((state) => state.sessions);
  const focusedSessionId = useSessionCockpit((state) => state.focusedSessionId);
  const treeView = useSessionCockpit((state) => state.orchestrationTreeView);
  const perSession = useSessionCockpit((state) => state.perSession);
  const taskDocuments = useDashboard((state) => state.analytics?.taskDocuments ?? EMPTY_DOCS);
  const pickups = useDashboard((state) => state.analytics?.agentPickups ?? EMPTY_PICKUPS);
  const [handoff, setHandoff] = useState<string | null>(null);
  // L3: the LaunchFlow dialog — opened from the palette, or pre-filled by the failed-launch
  // banner's 'Launch corrected…' (the refused pair, re-gated against the live catalog).
  const [launch, setLaunch] = useState<{ open: boolean; prefill?: LaunchPrefill }>({
    open: false,
  });
  // L4: the ModelEffortControl popover state lives here so the palette commands open the SAME
  // popover the header trigger opens (one control, two surfaces — design FQ2).
  const [controlPopoverOpen, setControlPopoverOpen] = useState(false);

  // The feed must outlive view switches (the layer is keep-alive) — refcounted, shared with
  // Cockpit's own subscription, so this never double-polls.
  useEffect(() => startCatalogPollDriver(), []);
  useEffect(() => startCockpitMirror(), []);
  // Review F1 (sev-3): retire residuals are captured for EVERY row — focus-independent — so an
  // unfocused seat's retireControlStopError still surfaces (and resurfaces after a reload).
  useEffect(() => startRetireResidualSweep(), []);
  // L4 R4: the promotion/drift watcher (turn-ended + focus re-GETs) and the assertive
  // failed/awaiting-input announcer — both refcounted, view-lifetime.
  useEffect(() => startSetPromotionWatcher(), []);
  useEffect(() => startSeatStateAnnouncer(), []);

  const focused = sessions.find((session) => session.id === focusedSessionId);

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
    () => attentionRollup(sessions, { unackedSessionIds: unackedIds, criticalBusSessionIds: criticalBus }),
    [sessions, unackedIds, criticalBus],
  );

  const focusSession = useCallback((id: string | null) => {
    setHandoff(null);
    // A focus switch never carries the previous seat's open control popover along.
    setControlPopoverOpen(false);
    sessionCockpitStore.getState().setFocusedSession(id);
  }, []);

  // R9 smart-default focus (never an empty landing) + F17 focus handoff: refocus happens ONLY
  // when nothing is focused or the focused seat stopped running UNDER us — a user deliberately
  // inspecting a landed row is never fought.
  const lastFocusRef = useRef<{ id: string | null; status: string }>({ id: null, status: "none" });
  useEffect(() => {
    const previous = lastFocusRef.current;
    const current = focusedSessionId
      ? sessions.find((session) => session.id === focusedSessionId)
      : undefined;
    const status = current ? (current.status ?? "running") : focusedSessionId ? "gone" : "none";
    if (
      previous.id !== null &&
      previous.id === focusedSessionId &&
      previous.status === "running" &&
      status !== "running"
    ) {
      const why = current?.landedReason ?? current?.retiredReason;
      const word = status === "landed" ? "landed" : status === "gone" ? "ended" : "retired";
      setHandoff(`${current?.label ?? previous.id} ${word}${why ? ` — ${why}` : ""} · focus handed off`);
      // Retire residuals are NOT captured here: the focus-independent sweep
      // (startRetireResidualSweep) owns that for every row, focused or not (review F1).
      sessionCockpitStore.getState().setFocusedSession(smartDefaultFocus(sessions));
    } else if (focusedSessionId === null) {
      const next = smartDefaultFocus(sessions);
      if (next !== null) sessionCockpitStore.getState().setFocusedSession(next);
    }
    lastFocusRef.current = { id: sessionCockpitStore.getState().focusedSessionId, status };
  }, [sessions, focusedSessionId]);

  // Mirror the view-owned layout/palette facts into the cockpit store (design §4.3 skeleton).
  useEffect(() => {
    sessionCockpitStore.getState().setLayout({ railCollapsed, inspectorCollapsed });
  }, [railCollapsed, inspectorCollapsed]);
  useEffect(() => {
    sessionCockpitStore.getState().setPaletteOpen(palette.open);
  }, [palette.open]);

  // L3: the launch command — the palette is the flow's entry point (design §7.1).
  useEffect(() => {
    return registry.register({
      id: "session.launch",
      title: "Launch session…",
      keywords: ["launch", "new", "open", "harness", "model", "effort"],
      run: () => setLaunch({ open: true }),
    });
  }, [registry]);

  // L4: the ModelEffortControl's palette surface — the SAME popover the header trigger opens.
  useEffect(() => {
    const controlAvailable = () =>
      focused !== undefined &&
      focused.harness !== undefined &&
      (focused.status ?? "running") === "running";
    const disposers = [
      registry.register({
        id: "control.setModel",
        title: "Set model…",
        keywords: ["model", "switch", "change", "capability"],
        when: controlAvailable,
        run: () => setControlPopoverOpen(true),
      }),
      registry.register({
        id: "control.setEffort",
        title: "Set effort…",
        keywords: ["effort", "thinking", "reasoning", "capability"],
        when: controlAvailable,
        run: () => setControlPopoverOpen(true),
      }),
    ];
    return () => {
      for (const dispose of disposers) dispose();
    };
  }, [registry, focused]);

  // L2 palette commands (dynamic titles carry the HONEST preview counts + names): the tree
  // toggle, jump-to-attention, bulk end at sprint + master level, and question triage (R16).
  useEffect(() => {
    const disposers = [
      registry.register({
        id: "rail.treeToggle",
        title: treeView ? "Rail: show role hierarchy" : "Rail: show orchestration tree",
        keywords: ["tree", "spawn", "provenance", "hierarchy", "rail"],
        run: () => sessionCockpitStore.getState().setOrchestrationTreeView(!treeView),
      }),
      registry.register({
        id: "attention.jump",
        title: "Jump to attention",
        keywords: ["attention", "next", "triage"],
        when: () => jumpToAttentionTarget(rollup, sessions) !== null,
        run: () => {
          const target = jumpToAttentionTarget(rollup, sessions);
          if (target) focusSession(target);
        },
      }),
      // L6 R6 (design §9.7): Stop turn exists, UA-7-gated — the palette names the gap instead
      // of hiding the command; running it reveals the welded (disabled) control + its reason.
      registry.register({
        id: "turn.stop",
        title: `Stop turn — unavailable: ${STOP_TURN_DISABLED_REASON}`,
        keywords: ["stop", "interrupt", "cancel", "turn"],
        // The gate matches the WorkingLine's OWN render condition (review finding 3): the
        // grammar yields working to awaiting-input/failed, and the command must never offer a
        // stop control that is not on screen.
        when: () => focused !== undefined && seatVisualState(focused).key === "working",
        run: () => {
          window.requestAnimationFrame(() =>
            rootRef.current
              ?.querySelector<HTMLElement>('[data-testid="working-line-stop"]')
              ?.focus(),
          );
        },
      }),
    ];
    const allLanded = [
      ...model.masters.flatMap((master) => master.completed),
      ...model.completedUnattached,
    ];
    if (allLanded.length > 0) {
      disposers.push(
        registry.register({
          id: "sessions.endCompleted",
          title: `End ${allLanded.length} completed — sprint: ${allLanded.map((s) => s.label).join(", ")}`,
          keywords: ["end", "completed", "bulk", "cleanup"],
          run: () => void endLanded(allLanded),
        }),
      );
    }
    for (const master of model.masters) {
      if (master.completed.length === 0) continue;
      disposers.push(
        registry.register({
          id: `sessions.endDone.${master.key}`,
          title: `End ${master.completed.length} done — ${master.label}: ${master.completed.map((s) => s.label).join(", ")}`,
          keywords: ["end", "done", "bulk", master.label],
          run: () => void endLanded(master.completed),
        }),
      );
    }
    for (const seat of waitingSeats(sessions)) {
      const preview = interactionPromptPreview(seat.controlPendingInteraction, 60);
      disposers.push(
        registry.register({
          id: `triage.${seat.id}`,
          title: `Answer pending question — ${seat.label}${preview ? `: “${preview}”` : ""}`,
          keywords: ["answer", "question", "pending", "input"],
          // L6 R4: answering was the user's explicit intent — focus the seat's InteractionBar
          // (the palette invoked it; this is the invoked action, not a focus steal).
          run: () => {
            focusSession(seat.id);
            window.requestAnimationFrame(() =>
              rootRef.current
                ?.querySelector<HTMLElement>('[data-testid="interaction-bar"] button')
                ?.focus(),
            );
          },
        }),
      );
    }
    return () => {
      for (const dispose of disposers) dispose();
    };
  }, [registry, model, rollup, sessions, treeView, focused, focusSession]);

  const focusSelector = useCallback((selector: string) => {
    const target = rootRef.current?.querySelector<HTMLElement>(selector);
    target?.focus();
  }, []);

  const focusRegion = useCallback(
    (region: FocusRegion) => focusSelector(regionTargetSelector(region)),
    [focusSelector],
  );

  // The F6 cycle: rail → stage → inspector → status line, collapsed panels excluded (design §5.3).
  const cycleRegion = useCallback(
    (direction: 1 | -1) => {
      const available = FOCUS_REGIONS.filter(
        (region) =>
          (region !== "rail" || !railCollapsed) && (region !== "inspector" || !inspectorCollapsed),
      );
      const activeElement = document.activeElement;
      const currentHost =
        activeElement instanceof Element ? activeElement.closest("[data-region]") : null;
      const current = (currentHost?.getAttribute("data-region") ?? null) as FocusRegion | null;
      const next = nextRegion(current, direction, available);
      if (next) focusRegion(next);
    },
    [focusRegion, inspectorCollapsed, railCollapsed],
  );

  const openPalette = useCallback((page: PalettePage = "commands", initialQuery = "") => {
    const activeElement = document.activeElement;
    setPalette((current) => {
      // Keep the ORIGINAL invoker across an in-palette page switch so close returns focus there.
      if (!current.open)
        paletteInvokerRef.current = activeElement instanceof HTMLElement ? activeElement : null;
      return { open: true, page, initialQuery };
    });
  }, []);

  const closePalette = useCallback(() => {
    setPalette({ open: false, page: "commands", initialQuery: "" });
    // R7: palette close returns focus to its invoker (when it is still in the document).
    const invoker = paletteInvokerRef.current;
    if (invoker?.isConnected) invoker.focus();
    paletteInvokerRef.current = null;
  }, []);

  const buildContext = useCallback(
    (): CommandContext => ({
      railCollapsed,
      inspectorCollapsed,
      paletteOpen: palette.open,
      actions: {
        openPalette,
        closePalette,
        toggleRail: () =>
          railCollapsed ? railRef.current?.expand() : railRef.current?.collapse(),
        toggleInspector: () =>
          inspectorCollapsed ? inspectorRef.current?.expand() : inspectorRef.current?.collapse(),
        focusRegion,
        cycleRegion,
        focusStageHeader: () => focusSelector(STAGE_HEADER_SELECTOR),
        focusTerminal: () => focusSelector(PTY_HOST_SELECTOR),
        // L2: alt+↑/↓ cycles the rail order (spine → clusters → unattached, live rows only).
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
        // L4 R7: cycle the REQUESTED effort through the live menu — no dialog; the chips carry
        // the async honesty story.
        cycleEffort: (direction) => {
          if (focusedSessionId) cycleEffortRequested(focusedSessionId, direction);
        },
        submitComposer: () => composerRef.current?.submit(),
        popBackComposer: () => composerRef.current?.popBack(),
      },
    }),
    [
      closePalette,
      cycleRegion,
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

  // The ~80-col floor chip (design §2, R3): the chip must always reflect the STAGE's actual
  // width — it is re-measured from every path that can change it (root resizes via the observer
  // below, and panel-layout changes via the PanelGroup onLayout callback), so a divider drag or a
  // palette-driven collapse can neither miss a squeeze nor leave a stale false alarm (review
  // round 2, finding 1).
  const measureStage = useCallback(() => {
    setStageNarrow(stageBelowPtyFloor(stageRef.current?.clientWidth ?? 0));
  }, []);

  // ~280px rail default (review round 2, finding 4): react-resizable-panels is percentage-only,
  // so on the FIRST real width measurement — and only when the user has no persisted layout —
  // the rail is resized to the percentage equivalent of the design's ~280px target. One-shot;
  // the persisted layout (autoSaveId) owns everything afterwards.
  const railCalibratedRef = useRef(false);
  const calibrateRail = useCallback((rootWidth: number) => {
    if (railCalibratedRef.current || rootWidth <= 0) return;
    railCalibratedRef.current = true;
    if (hasPersistedPanelLayout(PANELS_AUTOSAVE_ID)) return;
    railRef.current?.resize(railDefaultPercent(rootWidth, RAIL_MIN_PERCENT, RAIL_MAX_PERCENT));
  }, []);

  // Panel-layout changes (divider drags, collapse/expand — including palette-driven ones) never
  // resize the view ROOT, so they re-measure the stage here.
  const handlePanelLayout = useCallback(() => {
    measureStage();
    calibrateRail(rootRef.current?.clientWidth ?? 0);
  }, [calibrateRail, measureStage]);

  // Narrow-width rules (R3): auto-collapse on a downward threshold crossing, auto-expand on the
  // way back up; a manual reopen below the threshold is respected (pure decision — sessionLayout).
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
      const railMove = autoCollapseTransition(previous, width, RAIL_AUTO_COLLAPSE_PX);
      if (railMove === "collapse") railRef.current?.collapse();
      else if (railMove === "expand") railRef.current?.expand();
      const inspectorMove = autoCollapseTransition(previous, width, INSPECTOR_AUTO_COLLAPSE_PX);
      if (inspectorMove === "collapse") inspectorRef.current?.collapse();
      else if (inspectorMove === "expand") inspectorRef.current?.expand();
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(node);
    // The stage element itself is observed too: a divider drag changes the stage width with NO
    // root resize, and the observer callback re-runs measureStage first.
    if (stage) observer.observe(stage);
    return () => observer.disconnect();
  }, [calibrateRail, measureStage]);

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
            data-region="rail"
            data-testid="sessions-rail"
            aria-label="Session rail"
          >
            <h2 className={paneHeading}>Sessions</h2>
            <SessionRail
              onFocusSession={focusSession}
              focusedSessionId={focusedSessionId}
              model={model}
              rollup={rollup}
            />
          </aside>
        </Panel>
        <PanelResizeHandle className={resizeHandle} data-testid="sessions-handle-rail" />
        {/* An explicit defaultSize on EVERY panel keeps the group's layout state complete before
            any DOM measurement — the imperative collapse/expand handles depend on it. */}
        <Panel defaultSize={54} minSize={35} order={2}>
          <section
            ref={stageRef}
            className={cx(stagePane, "sessions__stage")}
            data-region="stage"
            data-testid="sessions-stage"
            aria-label="Session stage"
          >
            <SessionStage
              focused={focused}
              cockpit={focused ? perSession[focused.id] : undefined}
              controlPopover={{ open: controlPopoverOpen, onOpenChange: setControlPopoverOpen }}
              handoff={handoff}
              headerExtra={
                // R8: with a live pane the chip reflects the pane's REAL column count; the pixel
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
              workingLine={
                focused ? (
                  <WorkingLine session={focused} cockpit={perSession[focused.id]} />
                ) : undefined
              }
            >
              <StopResidualNotes />
              {/* L3 R6: a focused FAILED launch renders its refusal verbatim — ABOVE the pty
                  surface (ruled merge resolution); never hidden, never auto-retried;
                  Retire / 'Launch corrected…' are the only actions. */}
              {focused?.controlState === "failed" ? (
                <FailedLaunchBanner
                  session={focused}
                  onLaunchCorrected={(prefill) => setLaunch({ open: true, prefill })}
                />
              ) : null}
              {focused ? (
                <PtySurface focused={focused} onVisibleCols={setPtyCols} />
              ) : (
                <div
                  className={ptyPlaceholder}
                  data-kbzone="pty"
                  data-testid="sessions-pty-placeholder"
                  tabIndex={-1}
                  aria-label="Terminal placeholder"
                >
                  no focused session — the terminal renders here once a seat is focused; every
                  key passes to the harness except the reserved set (? lists it); F6 exits to
                  chrome
                </div>
              )}
              {focused ? (
                <InteractionBar
                  session={focused}
                  composerRef={composerRef}
                />
              ) : null}
              {focused ? (
                <SessionComposer
                  ref={composerRef}
                  session={focused}
                  queuedSetHint={queuedComposerHint(perSession[focused.id])}
                  onSlashAtLineStart={() => openPalette("commands", "/")}
                  onEscape={() => focusSelector(STAGE_HEADER_SELECTOR)}
                />
              ) : null}
            </SessionStage>
          </section>
        </Panel>
        <PanelResizeHandle className={resizeHandle} data-testid="sessions-handle-inspector" />
        <Panel
          ref={inspectorRef}
          collapsible
          collapsedSize={0}
          defaultSize={24}
          minSize={14}
          maxSize={40}
          onCollapse={() => setInspectorCollapsed(true)}
          onExpand={() => setInspectorCollapsed(false)}
          order={3}
        >
          <aside
            className={cx(pane, "sessions__inspector")}
            data-region="inspector"
            data-testid="sessions-inspector"
            aria-label="Inspector"
          >
            <h2 className={paneHeading}>Inspector</h2>
            {/* The focused seat's provenance/outcome card (R7/R17) — the L7 tabbed inspector
                (Evidence · Capabilities · Bus) replaces this pane. */}
            <div className={inspectorScroll} data-focus-target tabIndex={-1}>
              <SeatInspector
                session={focused}
                cockpit={focused ? perSession[focused.id] : undefined}
              />
            </div>
          </aside>
        </Panel>
      </PanelGroup>
      <footer
        className={cx(statusLine, "sessions__statusline")}
        data-region="statusline"
        data-testid="sessions-statusline"
      >
        <span className={statusFocus} data-focus-target tabIndex={-1}>
          sessions scaffold — StatusLine lands in L7
        </span>
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
        {inspectorCollapsed ? (
          <button
            type="button"
            className={reopenButton}
            onClick={() => inspectorRef.current?.expand()}
            data-testid="sessions-reopen-inspector"
          >
            ◫ inspector
          </button>
        ) : null}
        <span className={css({ marginLeft: "auto" })}>ctrl+k palette · ? keys · F6 regions</span>
      </footer>
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
        onClose={() => setLaunch({ open: false })}
        onFocusSession={focusSession}
      />
      {/* L4 R6: unfocused set outcomes persist until dismissed; R8: the two live regions. */}
      <SetOutcomeToasts
        sessions={sessions}
        focusedSessionId={focusedSessionId}
        onFocusSession={focusSession}
      />
      <CockpitLiveRegions />
    </div>
  );
}

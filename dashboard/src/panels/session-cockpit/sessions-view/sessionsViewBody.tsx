import { Panel, PanelGroup, PanelResizeHandle } from "react-resizable-panels";

import { cx } from "../../../../styled-system/css";
import {
  PTY_HOST_SELECTOR,
  regionTargetSelector,
  STAGE_HEADER_SELECTOR,
} from "../../../data/keymap/focus";
import {
  PTY_MIN_COLS,
  RAIL_FALLBACK_PERCENT,
} from "../../../data/sessionLayout";
import { seatVisualState } from "../../../data/stateGrammar";
import { queuedComposerHint } from "../../../data/setChips";
import { CockpitLiveRegions } from "../CockpitLiveRegions";
import { ChatContextBar, ChatSessionActions } from "../ChatContextBar";
import { CommandPalette } from "../CommandPalette";
import { FailedLaunchBanner } from "../FailedLaunchBanner";
import { InteractionBar } from "../InteractionBar";
import { LaunchFlow } from "../LaunchFlow";
import { LandedCleanupNotice } from "../LandedCleanupNotice";
import { ChatsStageBody } from "../ChatsStageBody";
import { ConversationWorkingLine } from "../conversation/ConversationWorkingLine";
import { SeatInspector } from "../SeatInspector";
import { SessionRail } from "../SessionRail";
import { SessionStage } from "../SessionStage";
import { SetOutcomeToasts } from "../SetOutcomeToasts";
import { WorkingLine } from "../WorkingLine";
import { SessionComposer } from "../../SessionComposer";
import type { useSessionsViewController } from "./sessionsViewController";
import {
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

type View = ReturnType<typeof useSessionsViewController>;

function RailPanel({ view }: { view: View }) {
  const { refs, state, data, handlers } = view;
  return (
    <Panel
      ref={refs.railRef}
      collapsible
      collapsedSize={0}
      // The 1280px-reference fallback; the first real measurement calibrates toward ~280px
      // (calibrateRail above) unless a persisted layout exists.
      defaultSize={RAIL_FALLBACK_PERCENT}
      minSize={RAIL_MIN_PERCENT}
      maxSize={RAIL_MAX_PERCENT}
      onCollapse={() => state.setRailCollapsed(true)}
      onExpand={() => state.setRailCollapsed(false)}
      order={1}
    >
      <aside
        className={cx(pane, "sessions__rail")}
        // A collapsed rail leaves NO residual sliver: display:none removes the aside's own
        // padding/border box (~21px) entirely, so the 0px panel is truly empty, not a dead strip.
        // The drag min stays at RAIL_MIN_PERCENT (12%); below it the panel snaps fully collapsed and
        // the ☰ rail chip (StatusLine) plus the in-place resize handle are the reopen affordances.
        style={state.railCollapsed ? { display: "none" } : undefined}
        aria-hidden={state.railCollapsed}
        data-region="rail"
        data-testid="sessions-rail"
        aria-label="Chat rail"
      >
        <h2 className={paneHeading}>Chats</h2>
        <ChatContextBar
          selectedLifecycleId={view.propsSelectedLifecycleId}
          onLaunchChat={() => state.setLaunch({ open: true })}
          onSessionOpened={handlers.focusSession}
        />
        <SessionRail
          onFocusSession={(sessionId) => {
            // Clicking a chat in the rail lands the CURSOR in the chat
            // input — the composer's focus target for controlled seats, the PTY host for
            // raw terminals. Deferred a frame so the stage has re-rendered for the new seat.
            handlers.focusSession(sessionId);
            window.requestAnimationFrame(() => {
              const stageTarget = document.querySelector<HTMLElement>(
                regionTargetSelector("stage"),
              );
              const target =
                stageTarget ??
                document.querySelector<HTMLElement>(PTY_HOST_SELECTOR);
              target?.focus();
            });
          }}
          focusedSessionId={data.focusedSessionId}
          model={data.model}
          rollup={data.rollup}
        />
      </aside>
    </Panel>
  );
}

function StageHeaderActions({ view }: { view: View }) {
  const { refs, state, data, handlers } = view;
  return (
    <>
      <ChatSessionActions
        focused={data.focused}
        selectedLeafKey={view.propsSelectedLeafKey}
        taskDocuments={data.taskDocuments}
        contextMaster={view.propsContextMaster}
        onBrowseHistory={handlers.openChatsLibrary}
      />
      {state.railCollapsed ? (
        <button
          type="button"
          className={reopenButton}
          onClick={() => refs.railRef.current?.expand()}
          data-testid="sessions-reopen-rail"
        >
          ☰ rail
        </button>
      ) : null}
      <button
        ref={refs.inspectorToggleRef}
        type="button"
        className={reopenButton}
        aria-controls="chats-inspector"
        aria-expanded={!state.inspectorCollapsed}
        onClick={() => {
          if (state.inspectorCollapsed) view.inspector.expandInspector("operator");
          else view.inspector.collapseInspector("operator");
        }}
        data-testid="sessions-toggle-inspector"
      >
        {state.inspectorCollapsed ? "◫ show inspector" : "◫ hide inspector"}
      </button>
    </>
  );
}

function StageHeaderExtra({ view }: { view: View }) {
  const { state } = view;
  const narrow =
    state.ptyCols !== null
      ? state.ptyCols < PTY_MIN_COLS
      : state.stageNarrow;
  if (!narrow) return null;
  return (
    <span
      className={floorChip}
      data-testid="sessions-pty-floor-chip"
      title={
        state.ptyCols !== null
          ? `The pane is ${state.ptyCols} columns wide (< ${PTY_MIN_COLS}) — a squeezed hosted TUI is a layout fact, not harness misbehavior.`
          : `The stage is narrower than ~${PTY_MIN_COLS} columns — a squeezed hosted TUI is a layout fact, not harness misbehavior.`
      }
    >
      {state.ptyCols !== null
        ? `pane ${state.ptyCols} cols (< ${PTY_MIN_COLS})`
        : `pane narrower than ~${PTY_MIN_COLS} cols`}
    </span>
  );
}

function FailedLaunchSlot({ view }: { view: View }) {
  const { state, data } = view;
  const { focused, focusedLive } = data;
  if (!focusedLive || focused === undefined || focused.controlState !== "failed") {
    return null;
  }
  return (
    <FailedLaunchBanner
      session={focused}
      onLaunchCorrected={(prefill) => state.setLaunch({ open: true, prefill })}
    />
  );
}

function WorkingLineSlot({ view }: { view: View }) {
  const { data } = view;
  const { focused, focusedLive, focusedConversationLive, perSession } = data;
  if (!focusedLive || focused === undefined) return null;
  return (
    <div
      data-slot="working-line"
      className={workingLineSlot}
      data-testid="stage-working-line-slot"
    >
      {focused.kind === "harness" && focusedConversationLive ? (
        <ConversationWorkingLine sessionId={focused.id} />
      ) : (
        <WorkingLine
          session={focused}
          cockpit={perSession[focused.id]}
          // Only the raw-terminal path (no composer) keeps a line-hosted
          // stop; controlled seats host it in the composer beside send.
          interrupt={focused.kind === "terminal" ? data.chatsInterrupt : undefined}
        />
      )}
    </div>
  );
}

function ComposerSlot({ view }: { view: View }) {
  const { refs, data, handlers } = view;
  const { focused, focusedLive, perSession, chatsInterrupt } = data;
  if (!focusedLive || focused === undefined) return null;
  return (
    <>
      <InteractionBar session={focused} composerRef={refs.composerRef} />
      {/* A raw terminal seat gets NO
          dashboard composer — the vendor TUI owns input there, and the dead editor +
          its two explanation bars only shrank the terminal. Controlled seats keep it. */}
      {focused.kind !== "terminal" ? (
        <SessionComposer
          ref={refs.composerRef}
          session={focused}
          queuedSetHint={queuedComposerHint(perSession[focused.id])}
          onSlashAtLineStart={() => handlers.openPalette("commands", "/")}
          onEscape={() => handlers.focusSelector(STAGE_HEADER_SELECTOR)}
          // The ⏹ stop docks beside send — SSE-preferred working signal, catalog
          // fallback, the same precedence the WorkingLine slot's source-selection uses.
          interrupt={chatsInterrupt}
          turnWorking={
            data.focusedLiveTurnWorking ||
            seatVisualState(focused).key === "working"
          }
        />
      ) : null}
    </>
  );
}

function StageWorkingArea({ view }: { view: View }) {
  const { state, data, handlers } = view;
  return (
    <>
      {/* Ending a chat produces NO stacked notice —
          StopResidualNotes is unmounted; stop details stay recorded in the lifecycle
          store for the Inspector/debug surfaces. */}
      {/* A focused FAILED launch renders its refusal verbatim — ABOVE the pty
          surface; never hidden, never auto-retried;
          Retire / 'Launch corrected…' are the only actions. */}
      <FailedLaunchSlot view={view} />
      {/* The structured one-roof body replaces the unconditional PTY. It
          hosts the default ConversationSurface, the in-stage history library, and the
          default-off read-only terminal-diagnostics drawer; a legacy-raw session keeps its
          interactive PTY as the primary body inside it. */}
      <ChatsStageBody
        focused={data.focused}
        onVisibleCols={state.setPtyCols}
        libraryOpen={state.chatsLibraryOpen}
        onCloseLibrary={handlers.closeChatsLibrary}
        diagnosticsOpen={state.chatsDiagnosticsOpen}
        onToggleDiagnostics={handlers.toggleChatsDiagnostics}
        onSessionOpened={handlers.focusSession}
        viewActive={view.propsActive}
      />
      {/* The turn theater sits where the eye
          waits for the next message — under the conversation, above the composer
          (the bottom-left "chat is still working" spot) — not in the stage's top
          chrome nobody watches mid-turn. Same source-selection rule. */}
      <WorkingLineSlot view={view} />
      <ComposerSlot view={view} />
    </>
  );
}

function StagePanel({ view }: { view: View }) {
  const { refs, state, data } = view;
  return (
    <Panel
      defaultSize={state.inspectorIntentOpen ? 54 : 78}
      minSize={35}
      order={2}
    >
      <section
        ref={refs.stageRef}
        className={cx(stagePane, "sessions__stage")}
        data-region="stage"
        data-testid="sessions-stage"
        aria-label="Chat stage"
      >
        <SessionStage
          focused={data.focused}
          cockpit={data.focused ? data.perSession[data.focused.id] : undefined}
          controlPopover={{
            open: state.controlPopoverOpen,
            onOpenChange: state.setControlPopoverOpen,
          }}
          handoff={state.handoff}
          headerActions={<StageHeaderActions view={view} />}
          headerExtra={<StageHeaderExtra view={view} />}
        >
          <StageWorkingArea view={view} />
        </SessionStage>
      </section>
    </Panel>
  );
}

function InspectorPanel({ view }: { view: View }) {
  const { refs, state, data } = view;
  const { focused, perSession } = data;
  return (
    <>
      <PanelResizeHandle
        className={resizeHandle}
        aria-label="Resize inspector"
        aria-hidden={state.inspectorCollapsed}
        disabled={state.inspectorCollapsed}
        tabIndex={state.inspectorCollapsed ? -1 : 0}
        style={
          state.inspectorCollapsed
            ? { visibility: "hidden", width: 0 }
            : undefined
        }
        data-testid="sessions-handle-inspector"
      />
      <Panel
        ref={refs.inspectorRef}
        collapsible
        collapsedSize={0}
        defaultSize={state.inspectorIntentOpen ? 24 : 0}
        minSize={14}
        maxSize={40}
        onCollapse={view.inspector.handleInspectorCollapse}
        onExpand={view.inspector.handleInspectorExpand}
        order={3}
      >
        <aside
          id="chats-inspector"
          className={cx(pane, "sessions__inspector")}
          style={
            state.inspectorCollapsed ? { visibility: "hidden" } : undefined
          }
          aria-hidden={state.inspectorCollapsed}
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
              pickups={data.pickups}
              heartbeat={data.supervisorHeartbeat}
              visible={view.propsActive && !state.inspectorCollapsed}
            />
          </div>
        </aside>
      </Panel>
    </>
  );
}

function SessionsViewOverlays({ view }: { view: View }) {
  const { state, data, handlers, commands } = view;
  return (
    <>
      <LandedCleanupNotice />
      {/* The StatusLine bar is REMOVED — every fact it held
          was duplicated (seat/harness/ws/quiet live in the HeaderStrip; poll health surfaces as
          an exception through the attention system) and its actions moved to the title row. */}
      <CommandPalette
        open={state.palette.open}
        page={state.palette.page}
        initialQuery={state.palette.initialQuery}
        registry={commands.registry}
        getContext={commands.getContext}
        onClose={handlers.closePalette}
        onPage={(page) =>
          state.setPalette({ open: true, page, initialQuery: "" })
        }
      />
      <LaunchFlow
        open={state.launch.open}
        prefill={state.launch.prefill}
        sessions={data.sessions}
        lifecycleId={view.propsSelectedLifecycleId}
        onClose={() => state.setLaunch({ open: false })}
        onFocusSession={handlers.focusSession}
      />
      {/* Unfocused set outcomes persist until explicitly marked seen; live regions. */}
      <SetOutcomeToasts
        sessions={data.sessions}
        focusedSessionId={data.focusedSessionId}
        onFocusSession={handlers.focusSession}
      />
      <CockpitLiveRegions />
    </>
  );
}

export function SessionsViewBody({ view }: { view: View }) {
  return (
    <div
      ref={view.refs.rootRef}
      className={cx(root, "sessions--view")}
      data-view="sessions"
      data-testid="sessions-view"
    >
      <PanelGroup
        direction="horizontal"
        autoSaveId={PANELS_AUTOSAVE_ID}
        onLayout={view.layout.handlePanelLayout}
      >
        <RailPanel view={view} />
        <PanelResizeHandle
          className={resizeHandle}
          aria-label="Resize chat rail"
          data-testid="sessions-handle-rail"
        />
        {/* An explicit defaultSize on EVERY panel keeps the group's layout state complete before
            any DOM measurement — the imperative collapse/expand handles depend on it. */}
        <StagePanel view={view} />
        <InspectorPanel view={view} />
      </PanelGroup>
      <SessionsViewOverlays view={view} />
    </div>
  );
}

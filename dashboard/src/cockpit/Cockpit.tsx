import {
  memo,
  useCallback,
  useEffect,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
  type PointerEvent as ReactPointerEvent,
} from "react";
import { motion } from "motion/react";

import { css, cva, cx } from "../../styled-system/css";
import {
  preferLiveSession,
  startCatalogPollDriver,
  startCatalogReconciler,
} from "../data/catalogPoll";
import { clientMatchesServingBuild, servingCommitLabel } from "../data/buildIdentity";
import { humanizeDuration } from "../data/conversation/format";
import { startScreenWakeLock } from "../data/screenWakeLock";
import { createGatedSeatEventApplier } from "../data/seatEvents";
import { sessionCockpitStore } from "../data/sessionCockpitStore";
import { selectQueue } from "../data/selectors";
import type { ConnState } from "../data/store";
import { dashboardStore, useDashboard } from "../data/store";
import { connectEvents, connectState } from "../data/stream";
import {
  lifecycleIdForSelection,
  lifecycleSelectionKey,
  masterFolderForSelection,
} from "../data/taskIdentity";
import { ModeBar } from "../grammar/ModeBar";
import { AttentionQueue } from "../panels/AttentionQueue";
import { ChangeSetViewer, type ChangeSetTarget } from "../panels/changeset/ChangeSetViewer";
import { NotesReaderViewer, type NotesReaderTarget } from "../panels/notes-reader/NotesReaderViewer";
import { DetailPanel } from "../panels/DetailPanel";
import { EngineRoom } from "../panels/EngineRoom";
import { useShouldAnimate } from "../panels/engine-room/useShouldAnimate";
import { EventRiver } from "../panels/EventRiver";
import { FileViewer } from "../panels/file-viewer/FileViewer";
import { Hangar } from "../panels/Hangar";
import { HighlightComposer } from "../panels/HighlightComposer";
import { LifecycleList } from "../panels/LifecycleList";
import { MemoryMirror } from "../panels/MemoryMirror";
import { RailChat } from "../panels/RailChat";
import { usePersistedFlag, usePersistedNumber } from "../panels/file-viewer/usePersistedFlag";
import { SessionsView } from "../panels/session-cockpit/SessionsView";
import { Topology } from "../panels/Topology";
import type { EngineProcessNode, TaskDocNode } from "../types/projection";

// A stable empty array so the `analytics?.taskDocuments ?? …` selector never returns a fresh reference
// (which would churn the zustand snapshot and re-render every tick).
const EMPTY_TASK_DOCS: TaskDocNode[] = [];
const EMPTY_ENGINE_PROCESSES: EngineProcessNode[] = [];

// The cockpit shell: persistent command chrome that never hides the alarms —
// a top status bar, a left rail (attention queue + lifecycle list = the master-caution, always
// visible), a switchable centre viewport (Operations / Engine Room / Memory / Topology / Hangar),
// and a persistent right rail (the event river ticker). The mode bar selects the viewport.
// Selection is ephemeral UI state held here and shared across panels and views.
// The "machine map" views (Engine Room / Topology) and the Chats terminal
// drop the rails and span the full body width; the top-bar caution stays visible so an
// alarm is never hidden.
export type CockpitView =
  | "operations"
  | "files"
  | "engine"
  | "memory"
  | "topology"
  | "hangar"
  | "chats";

const VIEWS: { id: CockpitView; label: string }[] = [
  { id: "operations", label: "Operations" },
  { id: "files", label: "File Viewer" },
  { id: "engine", label: "Engine Room" },
  { id: "memory", label: "Memory" },
  { id: "topology", label: "Topology" },
  { id: "hangar", label: "Hangar" },
  { id: "chats", label: "Chats" },
];

// Shell layout (co-located Panda css). The shell pins to the viewport so the top + mode
// bars stay fixed and the rails / viewport scroll internally; the marker classes (cockpit--shell /
// shell__body / rail / viewport) are kept so the per-panel descendant rules + tests still resolve.
const shell = css({
  position: "relative",
  display: "flex",
  flexDirection: "column",
  height: "100vh",
  minHeight: "0",
  overflow: "hidden",
  padding: "0.6rem 0.8rem",
  gap: "0.6rem",
});
const topbar = css({
  display: "flex",
  flexShrink: 0,
  alignItems: "center",
  justifyContent: "space-between",
  gap: "1rem",
  borderBottomWidth: "1px",
  borderBottomStyle: "solid",
  borderBottomColor: "grid",
  paddingBottom: "0.5rem",
});
const title = css({
  margin: "0",
  fontSize: "0.9rem",
  letterSpacing: "0.18em",
  color: "amber",
  // The brand is one lockup, never `AGENTS REMEMBER ·/MISSION/CONTROL` broken across lines.
  whiteSpace: "nowrap",
  textShadow: "0 0 calc(6px * var(--glow-strength)) oklch(0.82 0.16 75 / 0.5)",
});
const statusRow = css({
  display: "flex",
  alignItems: "center",
  // The top bar wraps BETWEEN fact-chips (each of which is nowrap), never mid-phrase; the
  // cluster reflows onto a second line as a whole instead of shattering into red fragments.
  flexWrap: "wrap",
  justifyContent: "flex-end",
  gap: "0.9rem",
  rowGap: "0.3rem",
  fontSize: "0.78rem",
});
const dim = css({ color: "muted", whiteSpace: "nowrap" });
const effectsToggle = cva({
  base: {
    font: "inherit",
    fontSize: "0.74rem",
    letterSpacing: "0.06em",
    paddingInline: "0.45rem",
    paddingBlock: "0.08rem",
    borderRadius: "2px",
    borderWidth: "1px",
    borderStyle: "solid",
    cursor: "pointer",
    background: "transparent",
    transition: "color 0.15s ease, border-color 0.15s ease",
    _hover: { borderColor: "muted" },
    _focusVisible: { outline: "1px solid token(colors.amber)", outlineOffset: "1px" },
  },
  variants: {
    on: {
      true: { color: "amber", borderColor: "amber" },
      false: { color: "muted", borderColor: "grid" },
    },
  },
});
// The right-rail River⇄Chat switch: a small two-segment control sitting above the rail
// content. React-state driven (railView) so the rail swaps the Event River for the single-instance chat.
const railToggle = css({
  display: "flex",
  flexShrink: 0,
  gap: "0.25rem",
  paddingBottom: "0.1rem",
});
const railToggleButton = cva({
  base: {
    flex: "1",
    font: "inherit",
    fontSize: "0.7rem",
    letterSpacing: "0.06em",
    paddingBlock: "0.12rem",
    borderRadius: "2px",
    borderWidth: "1px",
    borderStyle: "solid",
    cursor: "pointer",
    background: "transparent",
    transition: "color 0.15s ease, border-color 0.15s ease",
    _hover: { borderColor: "muted" },
    _focusVisible: { outline: "1px solid token(colors.amber)", outlineOffset: "1px" },
  },
  variants: {
    on: {
      true: { color: "amber", borderColor: "amber" },
      false: { color: "muted", borderColor: "grid" },
    },
  },
});
const caution = cva({
  base: { letterSpacing: "0.06em", color: "muted", whiteSpace: "nowrap" },
  variants: {
    sev: {
      clear: { color: "mint" },
      info: { color: "cyan" },
      warn: { color: "amber" },
      alarm: { color: "alarm", animation: "pulse 0.6s steps(1) infinite" },
    },
  },
});
const connBadge = cva({
  base: { fontWeight: "600", letterSpacing: "0.1em", whiteSpace: "nowrap" },
  variants: {
    state: {
      connecting: { color: "amber" },
      live: { color: "mint" },
      "signal-lost": { color: "alarm", animation: "pulse 0.5s steps(1) infinite" },
    },
  },
});
// The body grid: the railed 3-column shell, or a single full-width column for the machine-map
// views (Engine Room / Topology), which render their own internal layout.
const bodyGrid = cva({
  base: {
    display: "grid",
    gridTemplateRows: "minmax(0, 1fr)",
    gap: "0.7rem",
    flex: "1",
    minHeight: "0",
  },
  variants: {
    bleed: {
      false: {
        gridTemplateColumns: "minmax(300px, 1fr) minmax(420px, 2.2fr) minmax(260px, 0.95fr)",
      },
      true: { gridTemplateColumns: "1fr" },
    },
  },
});
const rail = css({
  position: "relative", // anchors the absolutely-positioned drag handle on the rail's inner edge
  display: "flex",
  flexDirection: "column",
  gap: "0.7rem",
  minWidth: "0",
  minHeight: "0",
  overflow: "hidden", // the rail itself does not scroll — each panel scrolls on its own
});
// Operations rails are draggable (like the File Viewer's split): each rail owns a thin gutter on its
// inner edge that drags its pixel width, persisted so the layout survives a reload. Bounds keep a rail
// from collapsing to nothing or eating the centre column. The handle sits flush inside the rail edge so
// the rail's own `overflow:hidden` never clips it.
const RAIL_MIN = 220;
const RAIL_MAX = 560;
const RAIL_STEP = 24; // keyboard nudge per ArrowLeft/ArrowRight
const railHandle = cva({
  base: {
    position: "absolute",
    top: "0",
    bottom: "0",
    width: "7px",
    zIndex: "3",
    cursor: "col-resize",
    background: "transparent",
    transition: "background 0.15s ease",
    _hover: { background: "amber" },
    _focusVisible: { outline: "1px solid token(colors.amber)", outlineOffset: "-1px" },
  },
  variants: {
    side: {
      left: { right: "0" }, // left rail: gutter on its right (centre-facing) edge
      right: { left: "0" }, // right rail: gutter on its left (centre-facing) edge
    },
  },
});

function clampRail(n: number): number {
  return Math.max(RAIL_MIN, Math.min(RAIL_MAX, Math.round(n)));
}

// One rail's resize gutter. Pointer drag adjusts the rail width live (left rail grows rightward, right
// rail grows leftward); Arrow keys nudge it. The new width flows straight to the persisted state so it
// both re-lays-out and survives a reload.
// Memoized like the rail panels it sits beside (tab-switch CPU): the shell re-renders on
// every view switch with unchanged props, and the memo gate skips this handle's re-render then.
const RailResizeHandle = memo(function RailResizeHandle({
  side,
  width,
  onResize,
}: {
  side: "left" | "right";
  width: number;
  onResize: (next: number) => void;
}) {
  const onPointerDown = (event: ReactPointerEvent<HTMLDivElement>) => {
    event.preventDefault();
    const startX = event.clientX;
    const startWidth = width;
    event.currentTarget.setPointerCapture(event.pointerId);
    const move = (moveEvent: globalThis.PointerEvent) => {
      const delta = moveEvent.clientX - startX;
      onResize(clampRail(side === "left" ? startWidth + delta : startWidth - delta));
    };
    const up = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  };
  const onKeyDown = (event: ReactKeyboardEvent<HTMLDivElement>) => {
    const dir = event.key === "ArrowRight" ? 1 : event.key === "ArrowLeft" ? -1 : 0;
    if (!dir) return;
    event.preventDefault();
    onResize(clampRail(width + (side === "left" ? dir : -dir) * RAIL_STEP));
  };
  return (
    <div
      role="separator"
      aria-orientation="vertical"
      aria-label={`Resize ${side} rail`}
      tabIndex={0}
      className={railHandle({ side })}
      onPointerDown={onPointerDown}
      onKeyDown={onKeyDown}
      data-testid={`rail-resize-${side}`}
    />
  );
});
const viewport = css({
  display: "flex",
  flexDirection: "column",
  minWidth: "0",
  minHeight: "0",
  overflow: "hidden", // the viewport does not scroll — its panel scrolls on its own
});
// Chats is kept mounted across every view (hidden via display, never unmounted) so the xterm
// instance, its scrollback buffer, and the live WebSocket survive a view switch — the cure for
// "switching away throws the terminal away." Cheap: the heavy xterm chunk is lazy and loads once a
// session opens, and re-entry is instant (no remount / re-init).
const chatsLayer = css({
  display: "flex",
  flexDirection: "column",
  flex: "1",
  minHeight: "0",
  minWidth: "0",
});
// The File Viewer is kept mounted across view switches too (hidden via display, never
// unmounted) so its repo/scope selection, open file, expanded trees, and view-mode survive a tab
// switch instead of resetting — same rationale as `chatsLayer`, and the same layout, so it reuses it.
const filesLayer = chatsLayer;
// Operations' DetailPanel is kept mounted across view switches for the SAME reason: its drill state
// (the opened sub-task `openSlug`) lives inside DetailPanel, so unmounting it on a tab switch reset the
// view back to the master overview on return. Hidden-not-unmounted preserves the drilled leaf (and the
// rail's reported leaf key with it). Same layout as the other persistent layers, so it reuses it.
const operationsLayer = chatsLayer;
// The Engine Room is kept mounted too (CPU diagnosis): it was the ONLY view that fully
// unmounted per tab entry, and it is the most expensive one — each entry measured a ~230–275 ms long
// task rebuilding 578 DOM nodes + a GSAP context + the backdrop video. Hidden-not-unmounted makes
// re-entry instant; the room's off-screen gates (useElementVisible: GSAP context paused, backdrop
// video paused, header pulse stilled) keep the hidden cost at ~0. Same layout, so it reuses it.
const engineLayer = chatsLayer;
// Hoisted rail-enter tween props: spread onto the two motion.aside rails. Module-level constants so
// the asides' props hold a stable identity across shell re-renders — fresh {initial/animate} +
// transition literals per render would make motion re-diff its animation config on every view
// switch (tab-switch CPU prop audit).
const RAIL_ENTER = { initial: { opacity: 0 }, animate: { opacity: 1 } };
const RAIL_ENTER_STILL = {};
const RAIL_TRANSITION = { duration: 0.18 };
// Memoization contract (tab-switch CPU): every persistent layer below
// — TopBar, both rail asides' panels, EngineRoom, DetailPanel, FileViewer, SessionsView, RailChat,
// the notes reader — is a React.memo component whose props are either state/store slices or
// useCallback-stable. A view switch then re-renders ONLY the shell's own chrome (grid/display
// flips + ModeBar) instead of reconciling the whole tree; the layers keep updating from their own
// store subscriptions. Measured residual before this: a uniform ~150–250 ms reconcile per setView.
// Cockpit wires the live SSE streams, then renders the presentational shell. The shell is split
// out so the dev gallery (/dev/bench) renders the exact same surface against fixture state.
export function Cockpit() {
  useEffect(() => connectState(), []);
  useEffect(() => {
    // The seat-event reconciler rides the SAME /api/events connection as the
    // Event River — one EventSource, two consumers. The catalog poll stays the authoritative
    // session-row source; push only pre-applies retire/land/rename. Every connection replays a
    // backlog before its `ready` marker — incl. reconnects whose cursor the server cannot decode
    // — and replayed history must never touch live rows (a stale rename would flicker until the
    // next poll beat), so application runs through the per-connection backlog gate.
    const gate = createGatedSeatEventApplier();
    return connectEvents(
      (line) => {
        dashboardStore.getState().pushEvent(line);
        gate.onLine(line);
      },
      "",
      () => {
        gate.onReady();
        dashboardStore.getState().markEventsHydrated();
      },
      gate.onInterrupt,
    );
  }, []);
  return <CockpitShell />;
}

export function CockpitShell({ initialView = "operations" }: { initialView?: CockpitView } = {}) {
  // Catalog ownership is shell-lifetime and view-independent. CockpitShell is also the exact dev
  // scenario surface, so keeping both drivers here preserves deterministic poll transitions there.
  useEffect(() => startCatalogPollDriver(), []);
  useEffect(() => startCatalogReconciler(), []);
  // A visible cockpit means someone is MONITORING — hold the screen wake lock (the same
  // display-required request video playback uses) so watching agents work counts as activity
  // against OS idle/lock timers. Auto-releases while the tab is hidden.
  useEffect(() => startScreenWakeLock(), []);
  const [view, setView] = useState<CockpitView>(initialView);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  // The Change-Set Viewer is a task-scoped TAKEOVER: when set, it replaces the railed body
  // full-bleed; the screen's back link clears it, restoring the rails + Operations. A mode-bar
  // switch or an open() also clears it (the takeover is transient, not a standing tab).
  const [changeSet, setChangeSet] = useState<ChangeSetTarget | null>(null);
  // The Notes Reader is a task-scoped TAKEOVER like the Change-Set Viewer, but it PERSISTS its
  // selection like the File Viewer: `notes` (the open note) is retained once opened so the reader stays
  // mounted (hidden) — selection survives back/forward — and `notesOpen` toggles the takeover's visibility.
  // Back + mode-bar/node switches hide it (retaining `notes`); a fresh entry point re-shows it on the note.
  const [notes, setNotes] = useState<NotesReaderTarget | null>(null);
  const [notesOpen, setNotesOpen] = useState(false);
  // The right rail toggles between the Event River (default) and the single-instance leaf chat.
  // Persisted to localStorage (same pattern as the effects toggle) so the choice survives a window refresh.
  const [chatRail, setChatRail] = usePersistedFlag("cockpit.rail-chat", false);
  const railView: "river" | "chat" = chatRail ? "chat" : "river";
  // Every callback handed to a memoized layer is useCallback-stable (see the memoization contract
  // above): a fresh closure per shell render would defeat the memo gate it is passed to.
  const setRailView = useCallback(
    (next: "river" | "chat") => setChatRail(next === "chat"),
    [setChatRail],
  );
  // Operations rail widths (px), draggable + persisted. They drive the railed grid's outer columns; the
  // centre takes the rest. Defaults roughly match the old fixed fr layout.
  const [leftRailWidth, setLeftRailWidth] = usePersistedNumber("cockpit.rail-left-w", 340);
  const [rightRailWidth, setRightRailWidth] = usePersistedNumber("cockpit.rail-right-w", 300);
  const animate = useShouldAnimate();
  const selectedLifecycleId = useDashboard((s) =>
    lifecycleIdForSelection(selectedId, s.lifecycles, s.analytics),
  );
  // The leaf the detail panel is actually SHOWING (a drilled sub-task or a directly-opened leaf doc),
  // reported up from DetailPanel — its durable QUALIFIED LEAF ID, so the rail chat + "attach to leaf"
  // key by the real leaf, not the master/series. Lifted here so it survives DetailPanel
  // unmount (a full-bleed view switch) and reaches both RailChat and the Chats cockpit.
  const [viewedLeafKey, setViewedLeafKey] = useState<string | undefined>(undefined);
  const taskDocuments = useDashboard((s) => s.analytics?.taskDocuments ?? EMPTY_TASK_DOCS);
  const engineProcesses = useDashboard((s) => s.analytics?.engineProcesses ?? EMPTY_ENGINE_PROCESSES);
  // The master folder of the current selection — pre-drills the leaf-attach picker to the task in context.
  const contextMaster = useDashboard((s) =>
    masterFolderForSelection(selectedId, s.lifecycles, s.analytics),
  );

  // The machine-map views + canonical Chats cockpit span full width: the shell rails hide and the
  // view's own rail/stage layout breathes.
  const fullBleed =
    view === "files" ||
    view === "engine" ||
    view === "topology" ||
    view === "chats";
  // A takeover (Change-Set Viewer or the Notes Reader) replaces the railed body full-bleed.
  const takeover = Boolean(changeSet) || notesOpen;

  // Open a node AND surface it in Operations: the attention queue / topology / hangar all jump
  // into the detail view, so a cross-view click lands where you can inspect it.
  const open = useCallback((id: string) => {
    setChangeSet(null); // leaving the change-set takeover for a selected node
    setNotesOpen(false); // and the notes reader takeover (selection retained)
    setSelectedId(
      id.startsWith("taskdoc:") || id.startsWith("series:") || id.startsWith("lifecycle:")
        ? id
        : lifecycleSelectionKey(id),
    );
    setView("operations");
  }, []);

  // Mode-bar switches exit the takeover too (it is not one of the standing views).
  const changeView = useCallback((next: CockpitView) => {
    setChangeSet(null);
    setNotesOpen(false); // exit the notes reader takeover too (selection retained)
    setView(next);
  }, []);

  // The Change-Set and Notes takeovers are mutually exclusive full-bleed screens: opening one hides the
  // other. `openNotes` retains `notes` after back (the reader stays mounted, so selection survives).
  const openChangeSet = useCallback((target: ChangeSetTarget) => {
    setNotesOpen(false);
    setChangeSet(target);
  }, []);
  const openNotes = useCallback((target: NotesReaderTarget) => {
    setChangeSet(null);
    setNotes(target);
    setNotesOpen(true);
  }, []);
  const closeChangeSet = useCallback(() => setChangeSet(null), []);
  const closeNotes = useCallback(() => setNotesOpen(false), []);
  const selectNote = useCallback(
    (path: string) => setNotes((cur) => (cur ? { ...cur, path } : cur)),
    [],
  );
  // HighlightComposer → Chats handoff: show the operator the sent context package landing.
  const showSentSession = useCallback((sessionId: string) => {
    preferLiveSession(sessionId);
    sessionCockpitStore.getState().setFocusedSession(sessionId);
    setView("chats");
  }, []);

  // Gated fade-in on the rails' first mount (reduced-motion / data-effects=off → no tween). The
  // asides then stay mounted across view switches (hidden via display on a full-bleed view — see
  // below), so returning to a railed view is instant rather than a re-animation.
  const railEnter = animate ? RAIL_ENTER : RAIL_ENTER_STILL;

  return (
    <div className={cx(shell, "cockpit--shell")}>
      <div className="crt-overlay" aria-hidden="true" />
      <TopBar />
      {changeSet ? (
        <div className={cx(bodyGrid({ bleed: true }), "shell__body")} data-fullbleed={true}>
          <main className={cx(viewport, "viewport")} data-view="changeset">
            <ChangeSetViewer {...changeSet} onBack={closeChangeSet} />
          </main>
        </div>
      ) : null}
      {/* The Notes Reader takeover: mounted once opened and kept mounted (hidden via display) even
          after Back — like the File Viewer — so its listing + open note survive back/forward. Its own
          `notesOpen` toggles visibility; a change-set takeover (if both were somehow set) wins the screen. */}
      {notes ? (
        <div
          className={cx(bodyGrid({ bleed: true }), "shell__body")}
          data-fullbleed={true}
          style={changeSet || !notesOpen ? { display: "none" } : undefined}
          aria-hidden={changeSet || !notesOpen ? true : undefined}
        >
          <main className={cx(viewport, "viewport")} data-view="notes-reader">
            <NotesReaderViewer
              repo={notes.repo}
              master={notes.master}
              path={notes.path}
              onSelectNote={selectNote}
              onBack={closeNotes}
            />
          </main>
        </div>
      ) : null}
      {/* The railed body is never UNMOUNTED while the change-set takeover shows — only hidden — so the
          DetailPanel's drill state (which leaf you were reading) survives. The viewer's back link then
          returns you exactly where you opened it from (a drilled leaf, not a reset to the master
          overview). Same hidden-not-unmounted pattern as the File Viewer + Chats layers below. */}
      <div
        className={cx(bodyGrid({ bleed: fullBleed }), "shell__body")}
        data-fullbleed={fullBleed}
        style={{
          ...(takeover ? { display: "none" } : {}),
          // The railed grid uses the persisted rail widths; full-bleed keeps the cva's single column.
          ...(fullBleed
            ? {}
            : { gridTemplateColumns: `${leftRailWidth}px minmax(380px, 1fr) ${rightRailWidth}px` }),
        }}
        aria-hidden={takeover ? true : undefined}
      >
        {/* The rails are never unmounted on a full-bleed view — only hidden — so re-entering a
            railed view is instant instead of a fresh AttentionQueue + LifecycleList mount, and rail
            state (scroll, collapsed groups) survives the switch (same keep-alive pattern
            as the engine/files/chats layers below). display:none drops them from the grid, so the
            full-bleed single column is unaffected. */}
        <motion.aside
          key="rail-left"
          className={cx(rail, "rail rail--left")}
          transition={RAIL_TRANSITION}
          style={{ display: fullBleed ? "none" : "flex" }}
          aria-hidden={fullBleed}
          {...railEnter}
        >
          {/* These two panels are the reason grammar/Dot.tsx cannot lean on colour alone. They are
              SIBLINGS in one always-visible rail — severities above, lifecycle states below — so a
              developer reads both grammars in one glance, and an amber dot up here says nothing
              about an amber dot down there (the reducer builds no attention row for an
              `awaiting-developer` lifecycle). Cockpit.test.tsx renders this rail and pins the two
              dots apart. */}
          <AttentionQueue onSelect={open} active={!fullBleed && !takeover} />
          <LifecycleList
            selectedId={selectedId}
            onSelect={open}
            active={!fullBleed && !takeover}
          />
          <RailResizeHandle side="left" width={leftRailWidth} onResize={setLeftRailWidth} />
        </motion.aside>
        <main className={cx(viewport, "viewport")} data-view={view}>
          {/* Memory / Topology / Hangar render transiently; Operations, File Viewer, Engine Room,
              and Chats are persistent hidden layers below so their in-panel state survives a switch
              (and the Engine Room's 578-node SVG + GSAP substrate is not rebuilt per entry). */}
          {view !== "chats" && view !== "files" && view !== "operations" && view !== "engine" && (
            <ViewBody view={view} onOpen={open} />
          )}
          {/* The Engine Room is never unmounted — only hidden — so a tab switch back is instant
              instead of a full remount (see `engineLayer`). Its off-screen cost is gated to ~0 by
              the room's own visibility gates (GSAP paused, backdrop video paused). */}
          <div
            className={engineLayer}
            style={{ display: view === "engine" ? "flex" : "none" }}
            aria-hidden={view !== "engine"}
          >
            <EngineRoom />
          </div>
          {/* Operations' DetailPanel is never unmounted — only hidden — so the drilled-open sub-task
              survives a view switch instead of resetting to the master overview (see `operationsLayer`). */}
          <div
            className={operationsLayer}
            style={{ display: view === "operations" ? "flex" : "none" }}
            aria-hidden={view !== "operations"}
          >
            <DetailPanel
              selectedId={selectedId}
              onOpenLifecycle={open}
              onOpenChangeSet={openChangeSet}
              onOpenNotes={openNotes}
              onViewLeaf={setViewedLeafKey}
            />
          </div>
          {/* The File Viewer is never unmounted — only hidden — so its repo/scope selection, open
              file, expanded trees, and view-mode survive a view switch instead of resetting. Its
              boot catalog read is deferred to the first actual showing (`active`). */}
          <div
            className={filesLayer}
            style={{ display: view === "files" ? "flex" : "none" }}
            aria-hidden={view !== "files"}
          >
            <FileViewer active={view === "files"} />
          </div>
          {/* The sole product-facing Chats cockpit is never unmounted — only hidden — so its PTY
              buffers, WebSockets, focus, drafts, and inspector state survive a view switch. The
              display:none hiding DESTROYS the timeline's DOM scroll offset, so `active` also
              reports the takeover cover: the timeline restores its remembered per-session scroll
              position on every re-show. */}
          <div
            className={chatsLayer}
            style={{ display: view === "chats" ? "flex" : "none" }}
            aria-hidden={view !== "chats"}
          >
            <SessionsView
              active={view === "chats" && !takeover}
              selectedLifecycleId={selectedLifecycleId}
              selectedLeafKey={viewedLeafKey}
              taskDocuments={taskDocuments}
              contextMaster={contextMaster}
            />
          </div>
        </main>
        <motion.aside
          key="rail-right"
          className={cx(rail, "rail rail--right")}
          transition={RAIL_TRANSITION}
          style={{ display: fullBleed ? "none" : "flex" }}
          aria-hidden={fullBleed}
          {...railEnter}
        >
          <RailResizeHandle side="right" width={rightRailWidth} onResize={setRightRailWidth} />
          <RailToggle value={railView} onChange={setRailView} />
          {railView === "river" ? (
            <EventRiver />
          ) : (
            <RailChat
              leafKey={viewedLeafKey}
              selectedLifecycleId={selectedLifecycleId}
              taskDocuments={taskDocuments}
              engineProcesses={engineProcesses}
              contextMaster={contextMaster}
            />
          )}
        </motion.aside>
      </div>
      <ModeBar items={VIEWS} value={view} onChange={changeView} label="Views" />
      {/* A cockpit-wide composer that a text selection raises — send the selection (+ a
          message) to a chat session as a context package. Mounted once here so it works on every view;
          renders nothing until there is a selection. `onSent` flips to Chats so the operator sees it land. */}
      <HighlightComposer
        selectedLifecycleId={selectedLifecycleId}
        viewedLeafKey={viewedLeafKey}
        leafChatActive={!fullBleed && railView === "chat"}
        onSent={showSentSession}
      />
    </div>
  );
}

function ViewBody({ view, onOpen }: { view: CockpitView; onOpen: (id: string) => void }) {
  switch (view) {
    case "memory":
      return <MemoryMirror />;
    case "topology":
      return <Topology onSelect={onOpen} />;
    case "hangar":
      return <Hangar onSelect={onOpen} />;
    // "operations", "files", "engine", and "chats" are intentionally not here — all four are kept
    // mounted in CockpitShell (hidden via CSS) so their in-panel state survives a view switch; routing
    // them through this transient switch would unmount them.
    default:
      return null;
  }
}

// The serving-build stamp, muted: commit
// short-hash (or the package version off-checkout) + process boot time, so a STALE serving
// process is visible at a glance. Data rides the snapshot (`servingBuild`, boot-time cached).
// A `dirty` serving checkout renders as `hash*` (git-prompt convention; the word `dirty` stays
// in the tooltip) — uncommitted fix-round code is visible without widening the one-line bar.
function ServingBuildStamp() {
  const build = useDashboard((s) => s.servingBuild);
  if (!build) return null;
  const clientCurrent = clientMatchesServingBuild(build);
  const commitLabel = servingCommitLabel(build);
  const booted = new Date(build.bootedAt);
  // "up" reads as an uptime, so show a humanized duration (one time format in the bar); the
  // absolute start stamp stays in the tooltip below. Removes the bar's second, 12h clock format.
  const bootLabel = Number.isNaN(booted.getTime())
    ? build.bootedAt
    : humanizeDuration(Date.now() - booted.getTime());
  // The client-vs-serving-build honesty cue. When the bundle executing in
  // this tab provably differs from the one the daemon now ships (`clientCurrent === false`), a stale
  // tab silently renders wrong data; append that to the hoverable stamp tooltip so the operator knows
  // to reload. `null` (a legacy server advertising no comparable bundle identity) and `true` (a real
  // match) add nothing — never assert a state we cannot prove, and never fabricate a match.
  const clientMismatchNote =
    clientCurrent === false
      ? " · client bundle differs from the serving build — reload to update"
      : "";
  return (
    <span
      className={dim}
      data-testid="serving-build"
      data-client-build-current={clientCurrent === null ? "unknown" : String(clientCurrent)}
      title={`Serving build v${build.version}${commitLabel ? ` @ ${commitLabel}` : ""}${build.dirty ? " (dirty — serving uncommitted code)" : ""} · process up since ${build.bootedAt}${clientMismatchNote}`}
    >
      {commitLabel ?? `v${build.version}`} · up {bootLabel}
    </span>
  );
}

// "The last turtle is the developer's glance" -- the supervisor sweep's own
// heartbeat tick, red past its staleness cutoff. `lastTickAt: null` means the supervisor has
// never ticked in this workspace (dashboard/supervisor autostart is opt-in), which is NOT the
// same as stale -- the badge renders nothing for it rather than a false alarm.
function SupervisorHeartbeatBadge() {
  const heartbeat = useDashboard((s) => s.supervisorHeartbeat);
  if (!heartbeat || heartbeat.lastTickAt === null) return null;
  // Humanize the age (no raw `9512.1m`): the two most-significant units read as human time.
  const age =
    heartbeat.ageSeconds !== null ? humanizeDuration(heartbeat.ageSeconds * 1000) : null;
  const label =
    age !== null
      ? `supervisor ${heartbeat.stale ? "stale" : "ok"} ${age}`
      : "supervisor stale";
  const duration =
    heartbeat.lastSweepDurationSeconds !== null
      ? `${heartbeat.lastSweepDurationSeconds.toFixed(2)}s`
      : "n/a";
  const backlog = `${heartbeat.redeliverableInboxCount}/${heartbeat.pendingInboxCount}`;
  return (
    <span
      // A long-stale supervisor degrades to a QUIET-distinct amber (warn), never the pulsing
      // cried-wolf red: six-day staleness is expected for an idle workspace, not a fault to alarm on.
      className={heartbeat.stale ? caution({ sev: "warn" }) : dim}
      data-testid="supervisor-heartbeat"
      title={`Supervisor last ticked ${heartbeat.lastTickAt}; staleness cutoff ${heartbeat.staleCutoffSeconds}s; redeliverable/pending inbox ${backlog}; last sweep ${duration}`}
    >
      {heartbeat.stale ? "⚠ " : ""}
      {label} · inbox {backlog} · sweep {duration}
    </span>
  );
}

// Memoized (tab-switch CPU): the top bar takes no props — every update it shows arrives via
// its own store subscriptions, so a shell re-render (view switch) never needs to reconcile it.
const TopBar = memo(function TopBar() {
  const conn = useDashboard((s) => s.conn);
  const metrics = useDashboard((s) => s.metrics);
  const generatedAt = useDashboard((s) => s.generatedAt);
  const queue = useDashboard(selectQueue);
  const topSeverity = queue[0]?.severity ?? "clear"; // queue is severity-sorted server-side

  return (
    <header className={topbar}>
      <h1 className={title}>AGENTS REMEMBER · MISSION CONTROL</h1>
      <div className={statusRow}>
        {/* A reassurance zero wearing an alarm glyph is a lie: render the waiting chip only
            when something actually waits. An empty queue simply shows nothing (absence = clear). */}
        {queue.length > 0 ? (
          <span className={caution({ sev: topSeverity })} data-testid="caution">
            ⚠ {queue.length} waiting
          </span>
        ) : null}
        {metrics ? (
          // Explicit scope: these are LIFECYCLE/task counts, a different authority from the Chats
          // rail's chat-seat states. Labeling the scope keeps one fact in one home (no backend change).
          <span
            className={dim}
            data-testid="task-metrics"
            title="Lifecycle task counts (not Chats seats) — chat-seat working/waiting/failed states live in the Chats rail's attention strip."
          >
            tasks {metrics.runningCount} running · {metrics.blockedCount} blocked
            {/* The NOTIFY-AND-CONTINUE handoff, appended only while something is actually handed
                back. Without it a lifecycle that has stopped and given the developer the turn is
                in `lifecycleCount` and `totalTokens` and in none of the numbers on this bar —
                the rollup bucket exists on the wire but nothing reads it. It follows the chip
                above rather than the running/blocked pair: those two are the workspace's standing
                rhythm and read fine at zero, while a permanent `0 awaiting you` is the same
                reassurance-zero lie the caution chip refuses to tell. */}
            {metrics.awaitingDeveloperCount > 0
              ? ` · ${metrics.awaitingDeveloperCount} awaiting you`
              : ""}{" "}
            · {metrics.totalTokens} tok
          </span>
        ) : null}
        {generatedAt ? <span className={dim}>@ {generatedAt.slice(11, 19)}</span> : null}
        <SupervisorHeartbeatBadge />
        <ServingBuildStamp />
        <ConnBadge conn={conn} />
        <EffectsToggle />
      </div>
    </header>
  );
});

// The right-rail River⇄Chat switch. Two radio-style segments; the active one is amber.
// Memoized (tab-switch CPU): `value`/`onChange` are stable across a view switch, so the
// toggle skips the shell-driven reconcile.
const RailToggle = memo(function RailToggle({
  value,
  onChange,
}: {
  value: "river" | "chat";
  onChange: (next: "river" | "chat") => void;
}) {
  return (
    <div className={railToggle} role="radiogroup" aria-label="Right rail view" data-testid="rail-toggle">
      <button
        type="button"
        role="radio"
        aria-checked={value === "river"}
        className={railToggleButton({ on: value === "river" })}
        onClick={() => onChange("river")}
        data-testid="rail-toggle-river"
      >
        River
      </button>
      <button
        type="button"
        role="radio"
        aria-checked={value === "chat"}
        className={railToggleButton({ on: value === "chat" })}
        onClick={() => onChange("chat")}
        data-testid="rail-toggle-chat"
      >
        Chat
      </button>
    </div>
  );
});

function ConnBadge({ conn }: { conn: ConnState }) {
  const label =
    conn === "live" ? "● LIVE" : conn === "signal-lost" ? "✶ SIGNAL LOST" : "… CONNECTING";
  return (
    <span className={connBadge({ state: conn })} data-testid="conn">
      {label}
    </span>
  );
}

// A visible motion toggle. Flips html[data-effects] (which useShouldAnimate reads live, so the
// engine-room backdrop + all gated motion respond at once) and persists the choice to the
// `calm-cockpit` localStorage flag main.tsx reads on the next load. Default = effects on.
function EffectsToggle() {
  const [on, setOn] = useState(
    () => typeof document === "undefined" || document.documentElement.dataset.effects !== "off",
  );
  const toggle = () => {
    const next = !on;
    setOn(next);
    if (next) {
      delete document.documentElement.dataset.effects;
      window.localStorage.removeItem("calm-cockpit");
    } else {
      document.documentElement.dataset.effects = "off";
      window.localStorage.setItem("calm-cockpit", "1");
    }
  };
  return (
    <button
      type="button"
      className={effectsToggle({ on })}
      onClick={toggle}
      aria-pressed={on}
      data-testid="effects-toggle"
      title={on ? "Effects on — click to calm (freeze motion + backdrop)" : "Calm — click to enable motion + backdrop"}
    >
      {on ? "✦ Effects" : "❄ Calm"}
    </button>
  );
}

import { statusLabel, type TaskStatusKey } from '../panels/detail-panel/pageParts';
import {
  memo,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type ReactNode,
} from 'react';
import { motion } from 'motion/react';

import { cx } from '../../styled-system/css';
import { shell, topbar, title, statusRow, dim, effectsToggle, railToggle, railToggleButton, caution, connBadge, bodyGrid, rail, RailResizeHandle, viewport, chatsLayer, takeoverBody, useNarrowCockpit, sidebarStrip, LEFT_RAIL_ID, RIGHT_RAIL_ID } from './layout';
import { useReviewChat, type ReviewChatColumn } from './reviewChat';
import { ReviewChatClose, ReviewMain, reviewRailView } from './reviewPage';
import { useReviewHash, parseReviewHash } from './reviewHash';
import { RoleChatsPane } from './RoleChats';
import { seatChatTarget, type AgentChatTarget } from './roleChatTarget';
import { clientMatchesServingBuild, servingCommitLabel } from '../data/buildIdentity';
import { humanizeDuration } from '../data/conversation/format';
import { startScreenWakeLock } from '../data/screenWakeLock';
import { selectHostActivityUnknown, selectQueue } from '../data/selectors';
import type { ConnState } from '../data/store';
import { dashboardStore, useDashboard } from '../data/store';
import { connectEvents, connectState } from '../data/stream';
import { ModeBar } from '../grammar/ModeBar';
import { SidebarToggle, useSidebar, type SidebarState } from '../grammar/SidebarToggle';
import { ChangeSetViewer, type ChangeSetTarget } from '../panels/changeset/ChangeSetViewer';
import type { TaskArtifactReaderTarget as NotesReaderTarget } from '../data/taskArtifacts';
import { NotesReaderViewer } from '../panels/notes-reader/NotesReaderViewer';
import { DetailPanel } from '../panels/detail-panel/DetailPanel';
import { IntentEntryRevalidation } from '../data/intentEntryRevalidation';
import { parseReaderHash } from '../data/knowledgeReader';
import type { ViewedTaskContext } from '../panels/detail-panel/state';
import { EngineRoom } from '../panels/EngineRoom';
import { useShouldAnimate } from '../panels/engine-room/useShouldAnimate';
import { EventRiver } from '../panels/EventRiver';
import { FileViewer } from '../panels/file-viewer/FileViewer';
import { Hangar } from '../panels/Hangar';
import { KnowledgeReader } from '../panels/knowledge-reader/KnowledgeReader';
import { LifecycleList } from '../panels/lifecycle-list/LifecycleList';
import type { AttentionAgentTarget } from '../panels/AttentionQueue';
import { MemoryMirror } from '../panels/MemoryMirror';
import { DocumentChat } from './document-chat/DocumentChat';
import { TrayBarSlot, trayBarRow } from '../tray/TrayButton';
import { TrayLayer } from '../tray/TrayLayer';
import { usePublishShownPage } from '../tray/shownChat';
import { usePersistedFlag, usePersistedNumber } from '../panels/file-viewer/usePersistedFlag';
import { Topology } from '../panels/Topology';
import type { EngineProcessNode, TaskEntry } from '../types/projection';

// A stable empty array so the `analytics?.engineProcesses ?? …` selector keeps the same
// reference while engine-process analytics are absent.
const EMPTY_ENGINE_PROCESSES: EngineProcessNode[] = [];

// The cockpit shell: persistent command chrome that never hides the alarms —
// a top status bar, a left rail (the lifecycle list with the attention queue's bell in its head =
// the master-caution, always visible), a switchable centre viewport (Chats / Operations /
// Knowledge / File Viewer),
// and a persistent right rail (the event river ticker). The mode bar selects the viewport.
// Selection is ephemeral UI state held here and shared across panels and views.
// The "machine map" views (Engine Room / Topology) and Chats
// drop the rails and span the full body width; the top-bar caution stays visible so an
// alarm is never hidden.
export type CockpitView =
  'operations' | 'files' | 'engine' | 'memory' | 'topology' | 'hangar' | 'knowledge' | 'chats';

const VIEWS: { id: CockpitView; label: string }[] = [
  { id: 'chats', label: 'Chats' },
  { id: 'operations', label: 'Operations' },
  { id: 'knowledge', label: 'Knowledge' },
  { id: 'files', label: 'File Viewer' },
];


// The File Viewer is kept mounted across view switches too (hidden via display, never
// unmounted) so its repo/scope selection, open file, expanded trees, and view-mode survive a tab
// switch instead of resetting — same rationale as `chatsLayer`, and the same layout, so it reuses it.
const filesLayer = chatsLayer;
// Operations' DetailPanel is kept mounted across view switches for the SAME reason: its drill state
// (the opened sub-task `openSlug`) lives inside DetailPanel, so unmounting it on a tab switch reset the
// view back to the master overview on return. Hidden-not-unmounted preserves the drilled leaf (and the
// rail's reported leaf key with it). Same layout as the other persistent layers, so it reuses it.
const operationsLayer = chatsLayer;
// The Engine Room layout is retained for injected initialView tests. It has no dashboard entry,
// outside the product bar and is not mounted or fetching while another view is open.
const engineLayer = chatsLayer;
// Hoisted rail-enter tween props: spread onto the two motion.aside rails. Module-level constants so
// the asides' props hold a stable identity across shell re-renders — fresh {initial/animate} +
// transition literals per render would make motion re-diff its animation config on every view
// switch (tab-switch CPU prop audit).
const RAIL_ENTER = { initial: { opacity: 0 }, animate: { opacity: 1 } };
const RAIL_ENTER_STILL = {};
const RAIL_TRANSITION = { duration: 0.18 };
// Memoization contract (tab-switch CPU): every persistent layer below
// — TopBar, both rail asides' panels, EngineRoom, DetailPanel, FileViewer, DocumentChat,
// the notes reader — is a React.memo component whose props are either state/store slices or
// useCallback-stable. A view switch then re-renders ONLY the shell's own chrome (grid/display
// flips + ModeBar) instead of reconciling the whole tree; the layers keep updating from their own
// store subscriptions. Measured residual before this: a uniform ~150–250 ms reconcile per setView.
// Cockpit wires the live SSE streams, then renders the presentational shell. The shell is split
// out so the dev gallery (/dev/bench) renders the exact same surface against fixture state.
function clearReaderHash(): void {
  // MIK-R73 rule 9: drop the reader's shareable `#knowledge?…` address when the reader is left.
  if (typeof window === 'undefined') return;
  if (!parseReaderHash(window.location.hash)) return;
  const { pathname, search } = window.location;
  window.history.replaceState(window.history.state, '', pathname + search);
}

export function Cockpit() {
  useEffect(() => connectState(), []);
  useEffect(
    () =>
      connectEvents(
        (line) => dashboardStore.getState().pushEvent(line),
        '',
        (ready) => dashboardStore.getState().markEventsHydrated(ready?.lastEventTs),
      ),
    [],
  );
  // A shared knowledge-reader URL (`#knowledge?…`, MIK-R29) opens on the Knowledge view.
  return (
    <CockpitShell initialView={parseReaderHash(window.location.hash) ? 'knowledge' : undefined} />
  );
}

interface CockpitShellState {
  view: CockpitView;
  selectedId: string | null;
  changeSet: ChangeSetTarget | null;
  notes: NotesReaderTarget | null;
  notesOpen: boolean;
  railView: 'river' | 'chat';
  leftRailWidth: number;
  rightRailWidth: number;
  animate: boolean;
  viewedLeafKey: string | undefined;
  viewedTask: ViewedTaskContext | undefined;
  tasks: TaskEntry[];
  engineProcesses: EngineProcessNode[];
  fullBleed: boolean;
  takeover: boolean;
  // A review target renders inside the railed grid beside the chat column, not as a takeover
  // (MIK-R89 v2 rule 12). It stays part of Operations (W23).
  review: ChangeSetTarget | null;
  reviewShown: boolean;
  // The shared sidebar toggles' state (rule 8): Operations' two sidebars, and the review page's
  // chat column (its tree side is the review surface's own).
  opsLeft: SidebarState;
  opsRight: SidebarState;
  reviewChat: ReviewChatColumn;
  railEnter: Record<string, unknown>;
  // The bell's way into an agent's chat (L190-R1-F09): the shell names the exact seat, and the
  // Chats view shows it. The rail itself owns no chat surface, so it only forwards this.
  agentChat: AgentChatTarget | null;
  setView: (view: CockpitView) => void;
  setSelectedId: (id: string | null) => void;
  setChangeSet: (target: ChangeSetTarget | null) => void;
  setNotes: (
    target:
      NotesReaderTarget | null | ((cur: NotesReaderTarget | null) => NotesReaderTarget | null),
  ) => void;
  setNotesOpen: (open: boolean) => void;
  setChatRail: (value: boolean) => void;
  setLeftRailWidth: (width: number) => void;
  setRightRailWidth: (width: number) => void;
  setViewedTask: (context: ViewedTaskContext | undefined) => void;
  setAgentChat: (target: AgentChatTarget | null) => void;
}

const FULL_BLEED_VIEWS: ReadonlySet<CockpitView> = new Set<CockpitView>(['files', 'knowledge', 'engine', 'topology', 'chats']);

// Which screen the shell shows. A change-set target with a review is the review page inside the
// railed grid; any other change-set target, and the notes reader, still take the screen over.
function shellFlags(view: CockpitView, changeSet: ChangeSetTarget | null, notesOpen: boolean) {
  const review = changeSet?.review ? changeSet : null;
  return {
    fullBleed: FULL_BLEED_VIEWS.has(view),
    takeover: Boolean(changeSet && !review) || notesOpen,
    review,
    reviewShown: review !== null && view === 'operations',
  };
}

function useCockpitShellState(initialView: CockpitView): CockpitShellState {
  const [view, setViewState] = useState<CockpitView>(() => parseReviewHash(window.location.hash) ? 'operations' : initialView);
  // MIK-R73 rule 9: the reader's address lives in the URL hash (`#knowledge?…`), and the
  // reader is never unmounted. Leaving the view clears the hash, so a later switch does not
  // resurrect the address it was left on -- the way `#review` is cleared when it is left.
  const setView = useCallback((next: CockpitView) => {
    if (next !== 'knowledge') clearReaderHash();
    setViewState(next);
  }, []);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [notes, setNotes] = useState<NotesReaderTarget | null>(null);
  const [notesOpen, setNotesOpen] = useState(false);
  const [changeSet, setChangeSet] = useReviewHash(setView, setNotesOpen);
  const [chatRail, setChatRail] = usePersistedFlag('cockpit.rail-chat', false);
  const railView: 'river' | 'chat' = chatRail ? 'chat' : 'river';
  const [leftRailWidth, setLeftRailWidth] = usePersistedNumber('cockpit.rail-left-w', 340);
  const [rightRailWidth, setRightRailWidth] = usePersistedNumber('cockpit.rail-right-w', 300);
  const animate = useShouldAnimate();
  const [viewedTask, setViewedTask] = useState<ViewedTaskContext | undefined>(undefined);
  // The chat the attention bell asked for (L190-R1-F09). One request object per click: the Chats
  // pane applies it, and only a new click (or the developer's own selection) moves it again.
  const [agentChat, setAgentChat] = useState<AgentChatTarget | null>(null);
  const viewedLeafKey = viewedTask?.leafKey;
  const taskMap = useDashboard((s) => s.tasks);
  const tasks = useMemo(() => Object.values(taskMap), [taskMap]);
  const engineProcesses = useDashboard(
    (s) => s.analytics?.engineProcesses ?? EMPTY_ENGINE_PROCESSES,
  );
  const { fullBleed, takeover, review, reviewShown } = shellFlags(view, changeSet, notesOpen);
  const opsLeft = useSidebar('operations', 'left');
  const opsRight = useSidebar('operations', 'right');
  const reviewChat = useReviewChat(reviewShown ? review : null);
  const railEnter = animate ? RAIL_ENTER : RAIL_ENTER_STILL;
  return {
    view,
    selectedId,
    changeSet,
    notes,
    notesOpen,
    railView,
    leftRailWidth,
    rightRailWidth,
    animate,
    viewedLeafKey,
    viewedTask,
    tasks,
    engineProcesses,
    fullBleed,
    takeover,
    review,
    reviewShown,
    opsLeft,
    opsRight,
    reviewChat,
    railEnter,
    agentChat,
    setView,
    setSelectedId,
    setChangeSet,
    setNotes,
    setNotesOpen,
    setChatRail,
    setLeftRailWidth,
    setRightRailWidth,
    setViewedTask,
    setAgentChat,
  };
}

// The Operations page's own way in: a row's document key, and the view a document is read in.
function useOpenDocument(
  setChangeSet: (target: ChangeSetTarget | null) => void,
  setNotesOpen: (open: boolean) => void,
  setSelectedId: (id: string | null) => void,
  setView: (view: CockpitView) => void,
): (id: string) => void {
  return useCallback(
    (id: string) => {
      setChangeSet(null);
      setNotesOpen(false);
      setSelectedId(id);
      setView('operations');
    },
    [setChangeSet, setNotesOpen, setSelectedId, setView],
  );
}

// The bell's way into a chat (L190-R1-F09). It is the shell's one agent navigation: the row's
// exact seat becomes the Chats view's request, and the view it needs is Chats — through the same
// `changeView` every tab uses. The request is what the Chats pane's frame target is built from;
// nothing here writes the observed-chat channel, so the chat on screen changes only when the
// frame answers that it shows the agent.
function useOpenAgentChat(
  changeView: (next: CockpitView) => void,
  setAgentChat: (target: AgentChatTarget | null) => void,
): (seat: AttentionAgentTarget) => void {
  // One identity for the whole session: the handler crosses two memo gates (the rail's list and
  // the bell panel), so a function rebuilt per render would re-render both on every shell state
  // change — the tab-switch cost the 260721 memo gate exists to prevent.
  const latest = useRef({ changeView, setAgentChat });
  latest.current = { changeView, setAgentChat };
  return useCallback((seat: AttentionAgentTarget) => {
    latest.current.setAgentChat(seatChatTarget(seat));
    latest.current.changeView('chats');
  }, []);
}

function useCockpitShellActions(
  setChatRail: (value: boolean) => void,
  setChangeSet: (target: ChangeSetTarget | null) => void,
  setNotes: (
    target:
      NotesReaderTarget | null | ((cur: NotesReaderTarget | null) => NotesReaderTarget | null),
  ) => void,
  setNotesOpen: (open: boolean) => void,
  setView: (view: CockpitView) => void,
  setSelectedId: (id: string | null) => void,
  setAgentChat: (target: AgentChatTarget | null) => void,
): {
  setRailView: (next: 'river' | 'chat') => void;
  open: (id: string) => void;
  openAgentChat: (seat: AttentionAgentTarget) => void;
  changeView: (next: CockpitView) => void;
  openChangeSet: (target: ChangeSetTarget) => void;
  openNotes: (target: NotesReaderTarget) => void;
  closeChangeSet: () => void;
  closeNotes: () => void;
  selectNote: (path: string) => void;
} {
  const setRailView = useCallback(
    (next: 'river' | 'chat') => setChatRail(next === 'chat'),
    [setChatRail],
  );
  const open = useOpenDocument(setChangeSet, setNotesOpen, setSelectedId, setView);
  const changeView = useCallback(
    (next: CockpitView) => {
      setChangeSet(null);
      setNotesOpen(false);
      setView(next);
    },
    [setChangeSet, setNotesOpen, setView],
  );
  // The bell's way into a chat (L190-R1-F09).
  const openAgentChat = useOpenAgentChat(changeView, setAgentChat);
  const openChangeSet = useCallback(
    (target: ChangeSetTarget) => {
      setNotesOpen(false);
      setChangeSet(target);
    },
    [setChangeSet, setNotesOpen],
  );
  const openNotes = useCallback(
    (target: NotesReaderTarget) => {
      setChangeSet(null);
      setNotes(target);
      setNotesOpen(true);
    },
    [setChangeSet, setNotes, setNotesOpen],
  );
  const closeChangeSet = useCallback(() => setChangeSet(null), [setChangeSet]);
  const closeNotes = useCallback(() => setNotesOpen(false), [setNotesOpen]);
  const selectNote = useCallback(
    (path: string) =>
      setNotes((cur) => {
        // A note selection leaves the folder-open state: the reader shows the note.
        if (cur?.kind === 'notes') return { ...cur, path, folder: false };
        if (cur?.kind === 'requirements') return { ...cur, path };
        return cur;
      }),
    [setNotes],
  );
  return {
    setRailView,
    open,
    openAgentChat,
    changeView,
    openChangeSet,
    openNotes,
    closeChangeSet,
    closeNotes,
    selectNote,
  };
}

function ChangeSetTakeover({
  target,
  onBack,
  onOpenChangeSet,
}: {
  target: ChangeSetTarget;
  onBack: () => void;
  // Re-targeting this one takeover is how a leaf becomes reachable FROM the master's net listing
  // (ICR-R33.3): the row names a leaf, the cockpit opens that leaf's own committed change-set, and
  // the reader stays in the same screen instead of having to find the leaf somewhere else.
  onOpenChangeSet: (target: ChangeSetTarget) => void;
}) {
  // A review target never reaches this takeover: it is the review page inside the railed grid.
  return (
    <div className={cx(bodyGrid({ bleed: true }), takeoverBody, 'shell__body')} data-fullbleed={true}>
      <main className={cx(viewport, 'viewport')} data-view="changeset">
        <ChangeSetViewer {...target} onBack={onBack} onOpenLeaf={onOpenChangeSet} />
      </main>
    </div>
  );
}

function NotesTakeover({
  target,
  hidden,
  onSelectNote,
  onBack,
}: {
  target: NotesReaderTarget;
  hidden: boolean;
  onSelectNote: (path: string) => void;
  onBack: () => void;
}) {
  return (
    <div
      className={cx(bodyGrid({ bleed: true }), 'shell__body')}
      data-fullbleed={true}
      style={hidden ? { display: 'none' } : undefined}
      aria-hidden={hidden ? true : undefined}
    >
      <main
        className={cx(viewport, 'viewport')}
        data-view={
          target.kind === 'notes'
            ? 'notes-reader'
            : target.kind === 'requirements'
              ? 'requirements-reader'
              : target.kind === 'decisions'
                ? 'decisions-reader'
                : 'report-reader'
        }
      >
        <NotesReaderViewer {...target} onSelectNote={onSelectNote} onBack={onBack} />
      </main>
    </div>
  );
}

function LeftRail({
  hidden,
  takeover,
  railEnter,
  leftRailWidth,
  selectedId,
  onSelect,
  onOpenAgent,
  onResize,
}: {
  // A full-bleed view, the review page (its own tree is its left column) or the developer's
  // toggle hides the rail; it stays mounted, so its scroll and its open groups survive.
  hidden: boolean;
  takeover: boolean;
  railEnter: Record<string, unknown>;
  leftRailWidth: number;
  selectedId: string | null;
  onSelect: (id: string) => void;
  /** The shell's way into an agent's chat: the bell's row names the exact seat (L190-R1-F09). */
  onOpenAgent: (seat: AttentionAgentTarget) => void;
  onResize: (width: number) => void;
}) {
  return (
    <motion.aside
      key="rail-left"
      id={LEFT_RAIL_ID}
      className={cx(rail, 'rail rail--left')}
      transition={RAIL_TRANSITION}
      style={{ display: hidden ? 'none' : 'flex' }}
      aria-hidden={hidden}
      {...railEnter}
    >
      {/* The native task tree fills the rail; its attention bell opens a layer over it. The bell's
          agent rows are the rail's way into a chat, so the shell's handler travels with them. */}
      <LifecycleList
        selectedId={selectedId}
        onSelect={onSelect}
        onOpenAgent={onOpenAgent}
        active={!hidden && !takeover}
      />
      <RailResizeHandle side="left" width={leftRailWidth} onResize={onResize} />
    </motion.aside>
  );
}

interface RightRailProps {
  fullBleed: boolean;
  railEnter: Record<string, unknown>;
  railView: 'river' | 'chat';
  rightRailWidth: number;
  setRailView: (next: 'river' | 'chat') => void;
  viewedTask: ViewedTaskContext | undefined;
  tasks: TaskEntry[];
  onResize: (width: number) => void;
  /** Operations' right sidebar is hidden by its toggle (MIK-R89 v2 rule 8). */
  hidden?: boolean;
  /** The review page's chat column (rule 12): this same rail, with the review's task and width. */
  column?: ReviewChatColumn | null;
}

/**
 * How the rail lies on its page. On Operations: shown unless full-bleed or its toggle hid it, the
 * River|Chat switch, and the viewed task document's chat. On the review page (`column`): the task's
 * chat only, open or closed by the review's toggle, preloaded while closed, at the review's width.
 */
function rightRailView({ fullBleed, hidden, railView, rightRailWidth, onResize, viewedTask }: RightRailProps) {
  const shown = !fullBleed && !hidden;
  return {
    shown,
    chat: railView === 'chat',
    river: railView === 'river',
    style: { display: shown ? 'flex' : 'none' } as CSSProperties,
    className: cx(rail, 'rail rail--right'),
    width: rightRailWidth,
    onResize,
    handle: { label: undefined, testId: undefined },
    chatProps: { taskDocumentRef: viewedTask?.taskDocumentRef, preload: undefined, unbound: undefined },
  };
}

export function RightRail(props: RightRailProps) {
  const { railEnter, railView, setRailView, tasks, column = null } = props;
  const view = column ? reviewRailView(props.fullBleed, column) : rightRailView(props);
  return (
    <motion.aside
      key="rail-right"
      id={RIGHT_RAIL_ID}
      className={view.className}
      transition={RAIL_TRANSITION}
      style={view.style}
      aria-hidden={!view.shown}
      data-page={column ? 'review' : undefined}
      {...railEnter}
    >
      <RailResizeHandle side="right" width={view.width} onResize={view.onResize} {...view.handle} />
      {/* The review page's column is the task's chat only: no River|Chat switch, no river. */}
      {column ? null : <RailToggle value={railView} onChange={setRailView} />}
      {/* MIK-R122: the river keeps its remove-while-chat-shows behaviour, but the chat is
          never unmounted — only hidden — so the single frame stays loaded and keeps the
          developer's draft and scroll position across the toggle. */}
      {view.river ? <EventRiver /> : null}
      <ReviewChatClose column={column} />
      <div
        style={{
          display: view.chat ? 'flex' : 'none',
          flex: '1',
          minHeight: '0',
          minWidth: '0',
        }}
        aria-hidden={!view.chat}
        data-testid="rail-chat-keepalive"
      >
        <DocumentChat active={view.shown && view.chat} tasks={tasks} {...view.chatProps} />
      </div>
    </motion.aside>
  );
}

function ViewLayer({
  visible,
  className,
  children,
}: {
  visible: boolean;
  className: string;
  children: ReactNode;
}) {
  return (
    <div
      className={className}
      style={{ display: visible ? 'flex' : 'none' }}
      aria-hidden={!visible}
    >
      {children}
    </div>
  );
}

// Operations' two sidebar toggles (MIK-R89 v2 rule 8, W27): one line over the task document, the
// task list's toggle first and the right column's last, so each stays at the main column's edge
// while its sidebar is hidden. It sits outside the memoized DetailPanel, which gains no prop.
function OperationsSidebarStrip({ left, right }: { left: SidebarState; right: SidebarState }) {
  return (
    <div className={sidebarStrip} data-testid="operations-sidebar-strip">
      <SidebarToggle
        side="left"
        open={left.open}
        onToggle={left.toggle}
        name="Task list sidebar"
        controls={LEFT_RAIL_ID}
        testId="sidebar-toggle-operations-left"
      />
      <SidebarToggle
        side="right"
        open={right.open}
        onToggle={right.toggle}
        name="River and chat sidebar"
        controls={RIGHT_RAIL_ID}
        testId="sidebar-toggle-operations-right"
      />
    </div>
  );
}

function MainLayers({
  view,
  takeover,
  hidden,
  sidebars,
  selectedId,
  tasks,
  agentChat,
  onOpen,
  onOpenChangeSet,
  onOpenNotes,
  onViewTask,
}: {
  view: CockpitView;
  takeover: boolean;
  // The review page is shown in the grid instead: these layers stay mounted and hidden (W23).
  hidden: boolean;
  sidebars: { left: SidebarState; right: SidebarState };
  selectedId: string | null;
  tasks: TaskEntry[];
  /** The chat the shell was asked for by its exact agent (the attention bell, L190-R1-F09). */
  agentChat: AgentChatTarget | null;
  onOpen: (id: string) => void;
  onOpenChangeSet: (target: ChangeSetTarget) => void;
  onOpenNotes: (target: NotesReaderTarget) => void;
  onViewTask: (context: ViewedTaskContext | undefined) => void;
}) {
  return (
    <main
      className={cx(viewport, 'viewport')}
      data-view={view}
      style={hidden ? { display: 'none' } : undefined}
      aria-hidden={hidden || undefined}
    >
      {/* Retained pages have no dashboard entry; initialView tests can still mount their layouts. */}
      {['memory', 'topology', 'hangar'].includes(view) && <ViewBody view={view} onOpen={onOpen} />}
      {view === 'engine' ? (
        <div className={engineLayer}>
          <EngineRoom />
        </div>
      ) : null}
      {/* Operations' DetailPanel is never unmounted — only hidden — so the drilled-open sub-task
          survives a view switch instead of resetting to the master overview (see `operationsLayer`). */}
      <ViewLayer visible={view === 'operations'} className={operationsLayer}>
        <OperationsSidebarStrip left={sidebars.left} right={sidebars.right} />
        <DetailPanel
          active={view === 'operations' && !takeover && !hidden}
          selectedId={selectedId}
          onOpenLifecycle={onOpen}
          onOpenChangeSet={onOpenChangeSet}
          onOpenNotes={onOpenNotes}
          onViewTask={onViewTask}
        />
      </ViewLayer>
      {/* The File Viewer is never unmounted — only hidden — so its repo/scope selection, open
          file, expanded trees, and view-mode survive a view switch instead of resetting. Its
          boot catalog read is deferred to the first actual showing (`active`). */}
      <ViewLayer visible={view === 'files'} className={filesLayer}>
        <FileViewer active={view === 'files'} />
      </ViewLayer>
      <ViewLayer visible={view === 'knowledge'} className={filesLayer}>
        <KnowledgeReader active={view === 'knowledge'} />
      </ViewLayer>
      {/* Role chats stay mounted across dashboard view switches. */}
      <ViewLayer visible={view === 'chats'} className={chatsLayer}>
        <RoleChatsPane active={view === 'chats' && !takeover} tasks={tasks} openChat={agentChat} />
      </ViewLayer>
    </main>
  );
}

function RailedBody({
  state,
  actions,
}: {
  state: CockpitShellState;
  actions: ReturnType<typeof useCockpitShellActions>;
}) {
  const narrow = useNarrowCockpit();
  return (
    <div
      className={cx(bodyGrid({ bleed: state.fullBleed }), 'shell__body')}
      data-fullbleed={state.fullBleed}
      data-page={state.reviewShown ? 'review' : undefined}
      style={{
        ...(state.takeover ? { display: 'none' } : {}),
        ...railedColumns(state, narrow),
      }}
      aria-hidden={state.takeover ? true : undefined}
    >
      {/* The rails are never unmounted on a full-bleed view — only hidden — so re-entering a
          railed view is instant instead of a fresh LifecycleList mount, and rail
          state (scroll, collapsed groups) survives the switch (same keep-alive pattern
          as the files/knowledge/chats layers below). display:none drops them from the grid, so the
          full-bleed single column is unaffected. */}
      <LeftRail
        hidden={state.fullBleed || state.reviewShown || !state.opsLeft.open}
        takeover={state.takeover}
        railEnter={state.railEnter}
        leftRailWidth={state.leftRailWidth}
        selectedId={state.selectedId}
        onSelect={actions.open}
        onOpenAgent={actions.openAgentChat}
        onResize={state.setLeftRailWidth}
      />
      <MainLayers
        view={state.view}
        takeover={state.takeover}
        hidden={state.reviewShown}
        sidebars={{ left: state.opsLeft, right: state.opsRight }}
        selectedId={state.selectedId}
        tasks={state.tasks}
        agentChat={state.agentChat}
        onOpen={actions.open}
        onOpenChangeSet={actions.openChangeSet}
        onOpenNotes={actions.openNotes}
        onViewTask={state.setViewedTask}
      />
      {/* The review page's slot, just before the right rail: a null slot keeps the rail's place,
          so showing and leaving the review never moves the rail or its chat frame (rule 12). No
          wrapper and no key around the rail: either would build a new frame and load it again. */}
      {state.reviewShown && state.review ? (
        <ReviewMain
          target={state.review}
          chat={{ open: state.reviewChat.open, toggle: state.reviewChat.toggle }}
          onBack={actions.closeChangeSet}
        />
      ) : null}
      <RightRail
        fullBleed={state.fullBleed}
        railEnter={state.railEnter}
        railView={state.railView}
        rightRailWidth={state.rightRailWidth}
        setRailView={actions.setRailView}
        viewedTask={state.viewedTask}
        tasks={state.tasks}
        onResize={state.setRightRailWidth}
        hidden={!state.opsRight.open}
        column={state.reviewShown ? state.reviewChat : null}
      />
    </div>
  );
}

// The railed grid's columns. Operations uses the persisted rail widths and gives a hidden
// sidebar's width to the task document (rule 8). The review page is its reading column (tree and
// reading, the review's own grid) and the chat track; a closed or overlying chat keeps a 0 px
// track and its gutter, which is what rule 11's measured widths assume (W26). Full-bleed keeps the
// cva's single column; at 40rem and narrower everything is one column.
function railedColumns(state: CockpitShellState, narrow: boolean): CSSProperties {
  if (state.fullBleed) return {};
  if (narrow) return { gridTemplateColumns: 'minmax(0, 1fr)' };
  if (state.reviewShown) {
    const chat = state.reviewChat;
    const track = chat.open && chat.placement === 'grid' ? chat.width : 0;
    return { gridTemplateColumns: `minmax(0, 1fr) ${track}px`, columnGap: `${chat.gutter}px`, position: 'relative' };
  }
  const left = state.opsLeft.open ? `${state.leftRailWidth}px ` : '';
  const right = state.opsRight.open ? ` ${state.rightRailWidth}px` : '';
  return { gridTemplateColumns: `${left}${left || right ? 'minmax(380px, 1fr)' : 'minmax(0, 1fr)'}${right}` };
}

// What the tray writes into a highlighted passage's provenance, and the repository a message to a
// chat without a task is filed under (MIK-R188 rules 3 and 9). The File Viewer names its own
// repository (`undefined` leaves it).
function shownPage(state: CockpitShellState): { page: string; repository: string | null | undefined } {
  if (state.reviewShown && state.review) return { page: 'Review', repository: state.review.repo };
  if (state.view === 'files') return { page: 'File Viewer', repository: undefined };
  if (state.view === 'operations')
    return { page: 'Operations', repository: state.viewedTask?.taskDocumentRef?.repository ?? null };
  return { page: VIEWS.find((item) => item.id === state.view)?.label ?? state.view, repository: null };
}

export function CockpitShell({ initialView = 'chats' }: { initialView?: CockpitView } = {}) {
  // A visible cockpit means someone is MONITORING — hold the screen wake lock (the same
  // display-required request video playback uses) so watching agents work counts as activity
  // against OS idle/lock timers. Auto-releases while the tab is hidden.
  useEffect(() => startScreenWakeLock(), []);
  const state = useCockpitShellState(initialView);
  const actions = useCockpitShellActions(
    state.setChatRail,
    state.setChangeSet,
    state.setNotes,
    state.setNotesOpen,
    state.setView,
    state.setSelectedId,
    state.setAgentChat,
  );
  const page = shownPage(state);
  usePublishShownPage(page.page, page.repository);

  return (
    <IntentEntryRevalidation shown={state.view === 'operations' && !state.takeover && !state.reviewShown}>
      <div className={cx(shell, 'cockpit--shell')}>
        <TopBar />
        {state.changeSet && !state.review ? (
          <ChangeSetTakeover
            target={state.changeSet}
            onBack={actions.closeChangeSet}
            onOpenChangeSet={actions.openChangeSet}
          />
        ) : null}
        {/* The Notes Reader takeover: mounted once opened and kept mounted (hidden via display) even
            after Back — like the File Viewer — so its listing + open note survive back/forward. Its own
            `notesOpen` toggles visibility; a change-set takeover (if both were somehow set) wins the screen. */}
        {state.notes ? (
          <NotesTakeover
            target={state.notes}
            hidden={Boolean(state.changeSet) || !state.notesOpen}
            onSelectNote={actions.selectNote}
            onBack={actions.closeNotes}
          />
        ) : null}
        <RailedBody state={state} actions={actions} />
        {/* The Tray button sits beside the view tabs, outside their group: the bar keeps its four
            views. The tray's page-wide part lies outside the railed body, which keeps its columns. */}
        <div className={trayBarRow}>
          <ModeBar items={VIEWS} value={state.view} onChange={actions.changeView} label="Views" />
          <TrayBarSlot />
        </div>
        <TrayLayer />
      </div>
    </IntentEntryRevalidation>
  );
}

function ViewBody({ view, onOpen }: { view: CockpitView; onOpen: (id: string) => void }) {
  switch (view) {
    case 'memory':
      return <MemoryMirror />;
    case 'topology':
      return <Topology onSelect={onOpen} />;
    case 'hangar':
      return <Hangar onSelect={onOpen} />;
    // "operations", "files", "knowledge", and "chats" are intentionally not here — all four are kept
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
      ? ' · client bundle differs from the serving build — reload to update'
      : '';
  return (
    <span
      className={dim}
      data-testid="serving-build"
      data-client-build-current={clientCurrent === null ? 'unknown' : String(clientCurrent)}
      title={`Serving build v${build.version}${commitLabel ? ` @ ${commitLabel}` : ''}${build.dirty ? ' (dirty — serving uncommitted code)' : ''} · process up since ${build.bootedAt}${clientMismatchNote}`}
    >
      {commitLabel ?? `v${build.version}`} · up {bootLabel}
    </span>
  );
}

// Memoized (tab-switch CPU): the top bar takes no props — every update it shows arrives via
// its own store subscriptions, so a shell re-render (view switch) never needs to reconcile it.
const TopBar = memo(function TopBar() {
  const conn = useDashboard((s) => s.conn);
  const taskCounts = useDashboard((s) => s.taskCounts);
  const generatedAt = useDashboard((s) => s.generatedAt);
  const queue = useDashboard(selectQueue);
  const topSeverity = queue[0]?.severity ?? 'clear'; // queue is severity-sorted server-side
  // With the host observation unknown, works and paused are not known: this line is the one
  // place that says so.
  const hostUnknown = useDashboard(selectHostActivityUnknown);
  const counts: Array<[TaskStatusKey, number]> = [
    ['planned', taskCounts.planned],
    ['started', taskCounts.started],
    ['completed', taskCounts.completed],
    ['aborted', taskCounts.aborted],
    ['works', taskCounts.works],
    ['paused', taskCounts.paused],
  ];
  const shown = hostUnknown
    ? counts.filter(([key]) => key !== 'works' && key !== 'paused')
    : counts;

  return (
    <header className={topbar}>
      <h1 className={title}>AGENTS REMEMBER · MISSION CONTROL</h1>
      <div className={statusRow}>
        <span className={dim} data-testid="task-counts">
          {[
            ...shown.map(([key, value]) => `${value} ${statusLabel(key)}`),
            ...(hostUnknown ? ['host activity unavailable'] : []),
          ].join(' · ')}
        </span>
        {/* A reassurance zero wearing an alarm glyph is a lie: render the waiting chip only
            when something actually waits. An empty queue simply shows nothing (absence = clear). */}
        {queue.length > 0 ? (
          <span className={caution({ sev: topSeverity })} data-testid="caution">
            ⚠ {queue.length} waiting
          </span>
        ) : null}
        {generatedAt ? <span className={dim}>@ {generatedAt.slice(11, 19)}</span> : null}
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
  value: 'river' | 'chat';
  onChange: (next: 'river' | 'chat') => void;
}) {
  return (
    <div
      className={railToggle}
      role="radiogroup"
      aria-label="Right rail view"
      data-testid="rail-toggle"
    >
      <button
        type="button"
        role="radio"
        aria-checked={value === 'river'}
        className={railToggleButton({ on: value === 'river' })}
        onClick={() => onChange('river')}
        data-testid="rail-toggle-river"
      >
        River
      </button>
      <button
        type="button"
        role="radio"
        aria-checked={value === 'chat'}
        className={railToggleButton({ on: value === 'chat' })}
        onClick={() => onChange('chat')}
        data-testid="rail-toggle-chat"
      >
        Chat
      </button>
    </div>
  );
});

function ConnBadge({ conn }: { conn: ConnState }) {
  const label =
    conn === 'live' ? '● LIVE' : conn === 'signal-lost' ? '✶ SIGNAL LOST' : '… CONNECTING';
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
    () => typeof document === 'undefined' || document.documentElement.dataset.effects !== 'off',
  );
  const toggle = () => {
    const next = !on;
    setOn(next);
    if (next) {
      delete document.documentElement.dataset.effects;
      window.localStorage.removeItem('calm-cockpit');
    } else {
      document.documentElement.dataset.effects = 'off';
      window.localStorage.setItem('calm-cockpit', '1');
    }
  };
  return (
    <button
      type="button"
      className={effectsToggle({ on })}
      onClick={toggle}
      aria-pressed={on}
      data-testid="effects-toggle"
      title={
        on
          ? 'Effects on — click to calm (freeze motion + backdrop)'
          : 'Calm — click to enable motion + backdrop'
      }
    >
      {on ? '✦ Effects' : '❄ Calm'}
    </button>
  );
}

import {
  memo,
  useCallback,
  useEffect,
  useRef,
  useState,
  type KeyboardEvent,
  type MouseEvent,
  type RefObject,
} from "react";

import {
  Header,
  ListBox,
  ListBoxItem,
  ListBoxSection,
  ToggleButton,
  ToggleButtonGroup,
} from "react-aria-components";

import { css, cva, cx } from "../../../styled-system/css";
import { dismissOperatorInboxEntry } from "../../data/operatorInbox";
import { hasLiveWorktree, type Pivot } from "../../data/selectors";
import { servedAgeSeconds, useNowMs } from "../../data/servedAges";
import { type OpenSession, useSessions } from "../../data/sessions";
import { useDashboard } from "../../data/store";
import {
  isOrchestrationDoc,
  masterCommandNames,
  orchestratorParentKey,
  pathDir,
  pathStem,
  taskDocHierarchyLabel,
  taskDocParentKey,
} from "../../data/taskHierarchy";
import {
  findLifecycleEnclosure,
  groupEnclosuresByLifecycle,
  lifecycleSelectionKey,
  parseTaskSelection,
  seriesSelectionKey,
  taskDocumentRefForDoc,
  taskDocSelectionKey,
  taskLabel,
} from "../../data/taskIdentity";
import { Dot } from "../../grammar/Dot";
import { Panel } from "../../grammar/Panel";
import { columnTabButton } from "../../grammar/viewTabLook";
import {
  PHASES,
  type AgentPickupNode,
  type Analytics,
  type EnclosureNode,
  type LifecycleProjection,
  type SeriesNode,
  type TaskDocNode,
} from "../../types/projection";
import { AttentionQueue } from "../AttentionQueue";
import { summarizeChatActivity, type ChatActivityIdentity } from "../ChatActivityIndicator";
import { statusLabel } from "../detail-panel/pageParts";
import { dim } from "../detail-panel/pageStyles";
import { TaskGroupDisclosure } from "../TaskGroupDisclosure";
import { useCollapsedTaskGroups } from "../useCollapsedTaskGroups";
import { caretCell } from "./columnLook";
import { seriesProgress, subTaskProgress, taskStepProgress } from "./progress";
import {
  masterIdsByFolder,
  progressFigure,
  rowHover,
  rowState,
  rowWord,
  shortTaskId,
  type RowHoverFacts,
} from "./rowState";
import { compareRows, isFoldKey, treeItems, type TreeItem, type TreeRow, type TreeView } from "./treeRows";

// The single unit list (note 01: the lifecycle is THE unit; note 06 IA). A BY REPO | BY PHASE pivot
// (React Aria ToggleButtonGroup) over every lifecycle (fleeting + persistent), presented as a React
// Aria ListBox so the 30+ rows are arrow-navigable + type-aheadable from the keyboard; selecting a
// row drives the centre detail.
//
// The look is the one the developer approved (MIK-R144 rule 3b): a row is one line without a
// frame (caret, mark, id, title, word, figure), the tree is indented by padding, and nothing
// leaves the column. The head of the column carries the bell of the attention queue (rule 3a),
// so the tree has the column's whole height. The measures are the approved pages' measures; see
// columnLook.ts for how they are written.
const sizing = css({ flex: "1 1 0", minWidth: "0", overflowX: "hidden" });
// The head is one line at the column's own width. A rail that was dragged narrower gets the
// switch and the bell on a line of their own under the title, and the bell under the switch at
// the narrowest: nothing of the head leaves the column, and the bell stays in reach.
const headRow = css({
  display: "flex",
  flexWrap: "wrap",
  alignItems: "center",
  justifyContent: "space-between",
  columnGap: "0.4375rem",
  rowGap: "0.2625rem",
  minWidth: "0",
});
const headTitle = css({ margin: "0", whiteSpace: "nowrap" });
const headTools = css({
  display: "flex",
  flexWrap: "wrap",
  alignItems: "stretch",
  columnGap: "0.35rem",
  rowGap: "0.2625rem",
  minWidth: "0",
});
const pivotBar = css({ display: "flex", gap: "0.2188rem" });
const listBox = css({
  listStyle: "none",
  display: "grid",
  gridTemplateColumns: "minmax(0, 1fr)",
  gap: "0.4813rem",
  width: "100%",
  minWidth: "0",
  margin: "0",
  padding: "0",
  outline: "none",
});
const section = css({
  display: "grid",
  gridTemplateColumns: "minmax(0, 1fr)",
  width: "100%",
  minWidth: "0",
});
// A group of the tree, a repository or a phase: a small muted label with its count.
const groupHeader = css({
  display: "flex",
  alignItems: "baseline",
  gap: "0.4375rem",
  minWidth: "0",
  marginBottom: "0.0875rem",
  color: "muted",
  fontSize: "0.595rem",
  lineHeight: "1.4",
  letterSpacing: "0.09em",
  textTransform: "uppercase",
  whiteSpace: "nowrap",
});
const groupCount = css({ marginLeft: "auto", letterSpacing: "0.02em" });
// One line, no frame: the row of the reviewer's tree (MIK-R86). It reaches into the panel's
// padding on both sides, so a wash and a focus line have room around the text. The scroll margin
// keeps a row that is brought into view clear of the panel's sticky head.
const rowItem = css({
  minWidth: "0",
  marginInline: "-0.2625rem",
  fontSize: "0.7rem",
  lineHeight: "1.5",
  cursor: "pointer",
  outline: "none",
  scrollMarginTop: "3rem",
  scrollMarginBottom: "0.35rem",
  "&:hover:not([data-selected], [data-chosen])": {
    background: "color-mix(in oklab, token(colors.amber) 8%, transparent)",
  },
  "&:is([data-selected], [data-chosen])": {
    background: "color-mix(in oklab, token(colors.amber) 16%, transparent)",
    "& [data-part='title']": { color: "amber" },
  },
  _focusVisible: { outline: "1px solid token(colors.amber)", outlineOffset: "-1px" },
});
// The line of a row. It fills the row, so its hover text is the row's: a React Aria list item
// passes no `title` on.
const rowLine = css({
  display: "flex",
  alignItems: "baseline",
  gap: "0.3063rem",
  minWidth: "0",
  paddingBlock: "0.1225rem",
  paddingRight: "0.2625rem",
  whiteSpace: "nowrap",
});
// One cell for both marks. Dot brings a margin for running text; here the row's gap spaces it.
const markCell = css({
  flex: "none",
  width: "1ch",
  textAlign: "center",
  userSelect: "none",
  "& > span": { marginRight: "0" },
});
// What the document declares: the task page's status dot, in the task page's four colours
// (detail-panel/pageStyles `statusWord`), at the column's size.
const statusDot = cva({
  base: {
    display: "inline-block",
    width: "0.4375rem",
    height: "0.4375rem",
    borderRadius: "50%",
    background: "currentColor",
    verticalAlign: "0.02em",
    color: "muted",
  },
  variants: {
    tone: {
      work: { color: "amber" },
      wait: {},
      done: { color: "mint" },
      gone: { color: "dormant" },
    },
  },
});
const rowIdCell = cva({
  base: { flex: "none", color: "muted" },
  variants: { long: { true: { fontSize: "0.6125rem" } } },
});
const rowTitle = cva({
  base: { flex: "1 1 0", minWidth: "8ch", overflow: "hidden", textOverflow: "ellipsis" },
  variants: {
    weight: { parent: { fontWeight: "600" }, fold: { color: "muted" } },
  },
});
const rowRepo = css({
  flex: "0 1 auto",
  minWidth: "0",
  overflow: "hidden",
  textOverflow: "ellipsis",
  fontSize: "0.6125rem",
  color: "muted",
});
// The word: what asks for the developer is coloured, a state that asks for nothing is muted.
const rowWordCell = cva({
  base: {
    flex: "0 1 auto",
    minWidth: "0",
    maxWidth: "15ch",
    overflow: "hidden",
    textOverflow: "ellipsis",
    fontSize: "0.6125rem",
  },
  variants: {
    tone: {
      ask: { color: "amber" },
      fault: { color: "alarm" },
      run: { color: "cyan" },
      live: { color: "mint" },
      rest: { color: "muted" },
    },
  },
});
const rowFigure = css({
  flex: "none",
  minWidth: "3ch",
  textAlign: "right",
  fontSize: "0.6125rem",
});
const pickupDismiss = css({
  flex: "none",
  font: "inherit",
  fontSize: "0.6125rem",
  color: "muted",
  background: "transparent",
  borderStyle: "none",
  paddingBlock: "0",
  paddingInline: "0.0875rem",
  cursor: "pointer",
  _hover: { color: "ink" },
});
// The indent of the tree: 0.7438rem a level, by padding inside the row.
function indent(depth: number): { paddingLeft: string } {
  return { paddingLeft: `${(0.2625 + depth * 0.74375).toFixed(4)}rem` };
}

function LifecycleListImpl({
  selectedId,
  onSelect,
  active = true,
}: {
  selectedId: string | null;
  onSelect: (id: string) => void;
  active?: boolean;
}) {
  const [pivot, setPivot] = useState<Pivot>("repo");
  const { collapsedKeys, toggleCollapsed } = useCollapsedTaskGroups();
  const { openFolds, toggleFold } = useOpenFolds();
  // An entry of the bell's layer opens its task: the tree chooses it and brings its row into view.
  const [reveal, setReveal] = useState<RevealRequest>({ id: "", count: 0 });
  const openFromBell = useCallback(
    (id: string) => {
      setReveal((last) => ({ id, count: last.count + 1 }));
      onSelect(id);
    },
    [onSelect],
  );
  const lifecycles = useDashboard((s) => s.lifecycles);
  const enclosures = useDashboard((s) => s.enclosures);
  const analytics = useDashboard((s) => s.analytics);
  const sessions = useSessions((state) => state.sessions);
  // Row staleness advances locally between emissions — the change gate no longer
  // re-serves a lifecycle every tick just because its age moved. A hidden kept-alive rail freezes
  // this clock so its React Aria list does no background reconstruction.
  const nowMs = useNowMs(10_000, active);
  return (
    <LifecycleListRender
      active={active}
      selectedId={selectedId}
      onSelect={onSelect}
      onOpenFromBell={openFromBell}
      reveal={reveal}
      pivot={pivot}
      setPivot={setPivot}
      collapsedKeys={collapsedKeys}
      toggleCollapsed={toggleCollapsed}
      openFolds={openFolds}
      toggleFold={toggleFold}
      lifecycles={lifecycles}
      enclosures={enclosures}
      analytics={analytics}
      sessions={sessions}
      nowMs={nowMs}
    />
  );
}

// The lines of completed ones the developer opened. Not remembered: the column opens with all of
// them closed, and a line that holds the chosen document is open by itself.
function useOpenFolds(): { openFolds: ReadonlySet<string>; toggleFold: (key: string) => void } {
  const [openFolds, setOpenFolds] = useState<ReadonlySet<string>>(() => new Set());
  const toggleFold = useCallback((key: string) => {
    setOpenFolds((current) => {
      const next = new Set(current);
      if (!next.delete(key)) next.add(key);
      return next;
    });
  }, []);
  return { openFolds, toggleFold };
}

// What the bell's layer asked to see: the id it opened, and which request this is.
interface RevealRequest {
  id: string;
  count: number;
}

// Brings the chosen row into view and gives it the keyboard's focus, once for every request of
// the bell's layer. `request` is the number of the request whose lifecycle is the one chosen now,
// 0 while that choice has not arrived. `present` says that the tree holds a row for the choice: a
// lifecycle without a row has nothing to show, and a row that appears later moves nothing.
function useRevealChosen(
  listRef: RefObject<HTMLElement | null>,
  chosenKey: string | null,
  request: number,
  present: boolean,
): void {
  const shown = useRef(0);
  useEffect(() => {
    const list = listRef.current;
    if (request === 0 || shown.current === request) return;
    if (!list || !present) {
      shown.current = request;
      return;
    }
    const show = (): boolean => {
      const options = list.querySelectorAll<HTMLElement>("[role='option']");
      const chosen = [...options].find((option) => option.dataset.key === chosenKey);
      if (!chosen) return false;
      shown.current = request;
      // jsdom has no scrollIntoView.
      chosen.scrollIntoView?.({ block: "nearest" });
      // The layer closes with the keyboard's focus inside it. The focus goes on to the row that
      // was asked for, so the arrow keys continue from there.
      chosen.focus({ preventScroll: true });
      return true;
    };
    if (show()) return;
    // The row of a parent that was closed is not in the page yet: React Aria builds its list in
    // a pass of its own, after this one.
    const observer = new MutationObserver(() => {
      if (show()) observer.disconnect();
    });
    observer.observe(list, { childList: true, subtree: true });
    return () => observer.disconnect();
  }, [listRef, request, chosenKey, present]);
}

// → opens and ← closes the parent row that has the keyboard's focus: the one addition to what
// the column does (MIK-R144 rule 5). A vertical list gives these two keys no other meaning. A
// parent that the chosen document holds open does not close.
function treeKey(event: KeyboardEvent<HTMLElement>, toggleCollapsed: (key: string) => void): void {
  const arrow = plainArrow(event);
  if (!arrow) return;
  const row =
    event.target instanceof Element ? event.target.closest<HTMLElement>("[data-expanded]") : null;
  const key = row?.dataset.key;
  if (!row || !key) return;
  event.preventDefault();
  event.stopPropagation();
  const open = row.dataset.expanded === "true";
  if (row.dataset.held === undefined && open !== (arrow === "open")) toggleCollapsed(key);
}

// What an arrow key asks of a parent row. With a modifier the two keys are the browser's (back,
// forward) and the system's, and the row leaves them alone.
function plainArrow(event: KeyboardEvent<HTMLElement>): "open" | "close" | null {
  if (event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return null;
  if (event.key === "ArrowRight") return "open";
  return event.key === "ArrowLeft" ? "close" : null;
}

interface LifecycleListRenderProps {
  active: boolean;
  selectedId: string | null;
  onSelect: (id: string) => void;
  onOpenFromBell: (id: string) => void;
  reveal: RevealRequest;
  pivot: Pivot;
  setPivot: (pivot: Pivot) => void;
  collapsedKeys: ReadonlySet<string>;
  toggleCollapsed: (key: string) => void;
  openFolds: ReadonlySet<string>;
  toggleFold: (key: string) => void;
  lifecycles: Record<string, LifecycleProjection>;
  enclosures: Record<string, EnclosureNode>;
  analytics: Analytics | null;
  sessions: OpenSession[];
  nowMs: number;
}

const LifecycleListRender = memo(
  function LifecycleListRender(props: LifecycleListRenderProps) {
    const { selectedId, onSelect, pivot, lifecycles, enclosures, analytics, toggleFold } = props;
    const rows = operationRows({
      lifecycles: Object.values(lifecycles),
      lifecycleById: lifecycles,
      enclosures,
      enclosuresByLifecycle: groupEnclosuresByLifecycle(Object.values(enclosures)),
      docs: analytics ? analytics.taskDocuments : [],
      series: analytics ? analytics.series : [],
      agentPickups: analytics ? analytics.agentPickups : [],
      sessions: props.sessions,
      nowMs: props.nowMs,
    });
    const choice = readChoice(
      rows,
      parseTaskSelection(selectedId, lifecycles, analytics),
      selectedId,
      props.reveal,
    );
    const groups = groupRows(rows, pivot, {
      collapsedKeys: props.collapsedKeys,
      openFolds: props.openFolds,
      selectedKey: choice.chosenKey,
    });
    const heldFolds = heldFoldKeys(groups);
    const showRepo = pivot === "phase" && new Set(rows.map((item) => item.repo)).size > 1;
    const listRef = useRef<HTMLDivElement>(null);
    useRevealChosen(listRef, choice.chosenKey, choice.request, choice.present);

    return (
      <Panel
        testid="lifecycle-list"
        head={
          <TaskPivotBar
            count={rows.length}
            pivot={pivot}
            onPivot={props.setPivot}
            onOpenFromBell={props.onOpenFromBell}
          />
        }
        className={sizing}
      >
        {rows.length === 0 ? (
          <p className="muted">No tasks.</p>
        ) : (
          <div
            ref={listRef}
            role="presentation"
            onKeyDownCapture={(event) => treeKey(event, props.toggleCollapsed)}
          >
            <ListBox
              className={listBox}
              aria-label="Tasks"
              selectionMode="single"
              selectedKeys={choice.selectedKey ? [choice.selectedKey] : []}
              onSelectionChange={(keys) => {
                const id = [...keys][0];
                if (typeof id !== "string") return;
                // A line of completed ones is no document: choosing it opens or closes it.
                if (!isFoldKey(id)) onSelect(id);
                else if (!heldFolds.has(id)) toggleFold(id);
              }}
            >
              {groups.map((group) => (
                <TaskGroupSection
                  key={group.key}
                  group={group}
                  flat={pivot === "phase"}
                  showRepo={showRepo}
                  markedKey={choice.markedKey}
                  onToggleCollapsed={props.toggleCollapsed}
                />
              ))}
            </ListBox>
          </div>
        )}
      </Panel>
    );
  },
  // Persist the last visible React Aria tree while the rail is display:none. Store subscriptions
  // still update the cheap controller above, but changed catalog/projection props do not rebuild
  // the hidden collection. Re-showing passes active=true and renders once from current truth.
  (_previous, next) => !next.active,
);

// Memoized (tab-switch CPU): a persistent rail panel — the shell re-renders on every view
// switch with unchanged props, and the memo gate skips this subtree then; the list's own store
// subscriptions still drive its updates.
export const LifecycleList = memo(LifecycleListImpl);

interface OperationRowsInput {
  lifecycles: LifecycleProjection[];
  lifecycleById: Record<string, LifecycleProjection>;
  enclosures: Record<string, EnclosureNode>;
  enclosuresByLifecycle: Map<string, EnclosureNode>;
  docs: TaskDocNode[];
  series: SeriesNode[];
  agentPickups: AgentPickupNode[];
  sessions: OpenSession[];
  nowMs: number; // the age-display clock — served staleness advances locally
}

// The command tier: "orchestration" for a master doc carrying `orchestrates`, "management" for a
// master commanded by one. Unset everywhere else. It shows as the row's place in the tree and the
// weight of its title; the row keeps it as `data-tier`.
type CommandTier = "orchestration" | "management";

// One row of the column: its place in the tree and every fact its line and its hover text say.
interface OperationRow extends TreeRow, RowHoverFacts {
  // What type-ahead finds and what the caret's label names.
  label: string;
  name: string;
  // The document's id: whole (BY PHASE, where no master stands above the row) and inside its
  // master (BY REPO). A sprint, a master and a row without a document show none.
  docId: string;
  shortId: string;
  phase: string;
  chatIdentity: ChatActivityIdentity;
  tier?: CommandTier;
}

interface OperationGroup {
  key: string;
  label: string;
  // The rows of the group, also those a closed parent or a closed line hides.
  count: number;
  items: TreeItem<OperationRow>[];
}

function operationRows(input: OperationRowsInput): OperationRow[] {
  const representedLifecycleIds = new Set<string>();
  // The identity rule: one task entry per enclosureId. A doc row that resolved through an
  // enclosure CLAIMS that leaf; a lifecycle bound to the same enclosure annotates the claimed row
  // (via the lifecycleForEnclosure fallback below) instead of rendering a second entry.
  const representedEnclosureIds = new Set<string>();
  const docPaths = new Set(input.docs.map((doc) => doc.docPath));
  const docsByLifecycle = groupDocs(input.docs);
  const pickupsByLifecycle = groupPickups(input.agentPickups);
  const enclosureList = Object.values(input.enclosures);
  // The visibility rule: a leaf is active while its worktree physically exists — the
  // projection's stat'ed truth, never a cleanup-state proxy. Completed/abandoned worktrees are
  // gone (hidden, as before), and a reopened contract (cleanup=reopened) has none until
  // worktree_start recreates them, so it stays hidden like any other planned leaf.
  const activeEnclosureList = enclosureList.filter(hasLiveWorktree);
  const activeEnclosures = Object.fromEntries(
    activeEnclosureList.map((item) => [item.enclosure, item]),
  );
  const activeEnclosuresByLifecycle = groupEnclosuresByLifecycle(activeEnclosureList);
  const rows: OperationRow[] = [];

  appendDocRows(
    { input, docPaths, masterIds: masterIdsByFolder(input.docs) },
    rows,
    representedLifecycleIds,
    representedEnclosureIds,
    activeEnclosureList,
    enclosureList,
    pickupsByLifecycle,
  );
  appendSeriesRows(
    input,
    rows,
    representedLifecycleIds,
    docPaths,
    enclosureList,
    pickupsByLifecycle,
  );
  appendLifecycleRows(
    input,
    rows,
    representedLifecycleIds,
    representedEnclosureIds,
    docsByLifecycle,
    pickupsByLifecycle,
    activeEnclosures,
    activeEnclosuresByLifecycle,
    docPaths,
  );

  return rows
    .map((item) => ({
      ...item,
      chatActivity: summarizeChatActivity(input.sessions, item.chatIdentity),
    }))
    .sort(compareRows);
}

// What every document row is built from.
interface DocRowContext {
  input: OperationRowsInput;
  docPaths: Set<string>;
  masterIds: ReadonlyMap<string, string>;
}

function appendDocRows(
  context: DocRowContext,
  rows: OperationRow[],
  representedLifecycleIds: Set<string>,
  representedEnclosureIds: Set<string>,
  activeEnclosureList: EnclosureNode[],
  enclosureList: EnclosureNode[],
  pickupsByLifecycle: ReturnType<typeof groupPickups>,
): void {
  const { input } = context;
  for (const doc of input.docs) {
    const enclosure = enclosureForDoc(doc, activeEnclosureList);
    if (!isRootTaskDoc(doc) && !enclosure) continue;
    const lifecycle =
      runtimeForDoc(doc, input.lifecycleById, enclosureList) ??
      (enclosure
        ? lifecycleForEnclosure(enclosure, input.lifecycles, input.lifecycleById)
        : undefined);
    if (lifecycle) representedLifecycleIds.add(lifecycle.id);
    if (enclosure) representedEnclosureIds.add(enclosure.enclosureId);
    rows.push(
      docRow(
        doc,
        lifecycle,
        pickupForLifecycle(lifecycle, pickupsByLifecycle),
        Boolean(enclosure),
        context,
      ),
    );
  }
}

function appendSeriesRows(
  input: OperationRowsInput,
  rows: OperationRow[],
  representedLifecycleIds: Set<string>,
  docPaths: Set<string>,
  enclosureList: EnclosureNode[],
  pickupsByLifecycle: ReturnType<typeof groupPickups>,
): void {
  for (const series of input.series) {
    if (docPaths.has(series.docPath)) continue;
    const lifecycle = runtimeForDoc(series, input.lifecycleById, enclosureList);
    if (lifecycle) representedLifecycleIds.add(lifecycle.id);
    rows.push(
      seriesRow(
        series,
        lifecycle,
        pickupForLifecycle(lifecycle, pickupsByLifecycle),
        input.docs,
        input.nowMs,
      ),
    );
  }
}

function appendLifecycleRows(
  input: OperationRowsInput,
  rows: OperationRow[],
  representedLifecycleIds: Set<string>,
  representedEnclosureIds: Set<string>,
  docsByLifecycle: ReturnType<typeof groupDocs>,
  pickupsByLifecycle: ReturnType<typeof groupPickups>,
  activeEnclosures: Record<string, EnclosureNode>,
  activeEnclosuresByLifecycle: Map<string, EnclosureNode>,
  docPaths: Set<string>,
): void {
  for (const lifecycle of input.lifecycles) {
    if (representedLifecycleIds.has(lifecycle.id)) continue;
    const docs = docsByLifecycle.get(lifecycle.id) ?? [];
    const enclosure = findLifecycleEnclosure(
      lifecycle,
      activeEnclosures,
      activeEnclosuresByLifecycle,
    );
    // No live-worktree enclosure -> no task entry; an already-claimed enclosureId -> the doc row
    // above IS this leaf's single entry (the lifecycle already annotates it).
    if (!enclosure || representedEnclosureIds.has(enclosure.enclosureId)) continue;
    representedEnclosureIds.add(enclosure.enclosureId);
    rows.push(
      lifecycleRow(
        lifecycle,
        docs,
        enclosure,
        pickupForLifecycle(lifecycle, pickupsByLifecycle),
        input.series,
        docPaths,
        input.nowMs,
      ),
    );
  }
}

function TaskPivotBar({
  count,
  pivot,
  onPivot,
  onOpenFromBell,
}: {
  count: number;
  pivot: Pivot;
  onPivot: (pivot: Pivot) => void;
  onOpenFromBell: (id: string) => void;
}) {
  return (
    <div className={headRow}>
      <h2 className={headTitle}>Tasks · {count}</h2>
      <div className={headTools}>
        <ToggleButtonGroup
          className={pivotBar}
          selectionMode="single"
          disallowEmptySelection
          selectedKeys={[pivot]}
          onSelectionChange={(keys) => {
            const next = [...keys][0];
            if (next === "repo" || next === "phase") onPivot(next);
          }}
          aria-label="Group tasks by"
        >
          <ToggleButton id="repo" className={columnTabButton}>
            BY REPO
          </ToggleButton>
          <ToggleButton id="phase" className={columnTabButton}>
            BY PHASE
          </ToggleButton>
        </ToggleButtonGroup>
        <AttentionQueue onSelect={onOpenFromBell} />
      </div>
    </div>
  );
}

function TaskGroupSection({
  group,
  flat,
  showRepo,
  markedKey,
  onToggleCollapsed,
}: {
  group: OperationGroup;
  flat: boolean;
  showRepo: boolean;
  // The row a chosen lifecycle stands on, where that is not the list's own selection.
  markedKey: string | null;
  onToggleCollapsed: (key: string) => void;
}) {
  return (
    <ListBoxSection className={section}>
      <Header className={groupHeader}>
        {group.label}
        <span className={cx(groupCount, dim)}>{group.count}</span>
      </Header>
      {group.items.map((item) =>
        item.kind === "fold" ? (
          <FoldRow key={item.key} item={item} />
        ) : (
          <TaskRow
            key={item.row.key}
            item={item}
            flat={flat}
            showRepo={showRepo}
            marked={item.row.key === markedKey}
            onToggleCollapsed={onToggleCollapsed}
          />
        ),
      )}
    </ListBoxSection>
  );
}

type RowItem = Extract<TreeItem<OperationRow>, { kind: "row" }>;
type FoldItem = Extract<TreeItem<OperationRow>, { kind: "fold" }>;

// The caret of a parent row. BY PHASE has no tree and no caret; in the tree a row without
// children keeps the caret's room.
function RowCaret({
  item,
  flat,
  onToggleCollapsed,
}: {
  item: RowItem;
  flat: boolean;
  onToggleCollapsed: (key: string) => void;
}) {
  if (flat) return null;
  if (!item.hasChildren) return <span className={caretCell} aria-hidden="true" />;
  return (
    <TaskGroupDisclosure
      label={item.row.label}
      collapsed={item.collapsed}
      held={item.held}
      onToggle={() => onToggleCollapsed(item.row.key)}
    />
  );
}

// The mark: the task page's status dot for what the document declares, the dashboard's state
// mark for what a lifecycle reports (rowState.ts).
function RowMark({ row }: { row: OperationRow }) {
  const state = rowState(row);
  return (
    <span
      className={markCell}
      aria-label={`Task progress: ${state.word}; phase: ${row.phase}`}
      data-testid="task-state"
    >
      {state.mark.kind === "status" ? (
        <span className={statusDot({ tone: state.mark.tone })} aria-hidden="true" />
      ) : (
        <Dot variant={state.mark.variant} />
      )}
    </span>
  );
}

function TaskRow({
  item,
  flat,
  showRepo,
  marked,
  onToggleCollapsed,
}: {
  item: RowItem;
  flat: boolean;
  showRepo: boolean;
  marked: boolean;
  onToggleCollapsed: (key: string) => void;
}) {
  const row = item.row;
  const id = flat ? row.docId : row.shortId;
  return (
    <ListBoxItem
      id={row.key}
      textValue={typedText(row, id)}
      className={rowItem}
      data-depth={row.depth}
      data-parent-key={row.parentKey}
      data-tier={row.tier}
      data-expanded={item.hasChildren && !flat ? String(!item.collapsed) : undefined}
      data-held={item.held ? "" : undefined}
      // The mark is for the eye; the reveal gives the row the keyboard's focus. (A React Aria
      // list item passes no `aria-current` on.)
      data-chosen={marked ? "" : undefined}
    >
      <div className={rowLine} style={indent(row.depth)} title={rowHover(row)}>
        <RowCaret item={item} flat={flat} onToggleCollapsed={onToggleCollapsed} />
        <RowMark row={row} />
        {id ? <span className={rowIdCell({ long: flat })}>{id}</span> : null}
        <span
          className={rowTitle({ weight: row.kind === "task" ? undefined : "parent" })}
          data-part="title"
        >
          {row.name}
        </span>
        {showRepo ? <span className={rowRepo}>{row.repo}</span> : null}
        <RowWord row={row} />
        <span className={cx(rowFigure, dim)}>{progressFigure(row.progress)}</span>
      </div>
    </ListBoxItem>
  );
}

// What typing finds a row by: typing matches the beginning of this text. A task numbered in its
// master is typed by its id as the row writes it, then its title. A row whose text was its title
// alone (a sprint, a master, a task that stands alone) is still typed by that title.
function typedText(row: OperationRow, id: string): string {
  return id && row.label !== row.name ? `${id} ${row.name}` : row.name;
}

// The one word of the row (rowState.ts). A chat's word keeps the mark the chat tests look for.
function RowWord({ row }: { row: OperationRow }) {
  const word = rowWord(row);
  return (
    <>
      <span
        className={rowWordCell({ tone: word.tone })}
        data-testid={word.source === "chat" ? "chat-activity" : "task-word"}
      >
        {word.text}
      </span>
      {word.dismissPickup && row.pickup ? (
        <PickupDismiss entryId={row.pickup.entryId} named={word.source !== "pickup"} />
      ) : null}
    </>
  );
}

// "check chat": the acknowledgment of a hand-over is overdue. × dismisses the warning. Beside
// another word (an open gate comes first and stays the word) the control is `named`: it says
// "chat", so that it cannot be read as a dismissal of that word.
function PickupDismiss({ entryId, named }: { entryId: string; named: boolean }) {
  const [dismissing, setDismissing] = useState(false);
  const dismiss = (event: MouseEvent<HTMLButtonElement>) => {
    event.stopPropagation();
    if (dismissing) return;
    setDismissing(true);
    void dismissOperatorInboxEntry(entryId).finally(() => setDismissing(false));
  };
  return (
    <button
      type="button"
      className={pickupDismiss}
      // A press that reached the row would choose it: the list chooses on the press, not the click.
      onPointerDown={(event) => event.stopPropagation()}
      onMouseDown={(event) => event.stopPropagation()}
      onKeyDown={(event) => event.stopPropagation()}
      onClick={dismiss}
      disabled={dismissing}
      aria-label={named ? "Dismiss pickup warning (check chat)" : "Dismiss pickup warning"}
      title={
        named ? "Dismiss pickup warning (check chat). The gate stays open." : "Dismiss pickup warning"
      }
      data-testid="agent-pickup-dismiss"
    >
      {named ? "chat ×" : "×"}
    </button>
  );
}

function isAbandoned(row: OperationRow): boolean {
  const { mark } = rowState(row);
  return mark.kind === "status" && mark.tone === "gone";
}

// The line the rows that are over stand behind: their count, and the rows themselves once it
// is opened. It opens in place and is open by itself while it holds the chosen document.
function foldText(item: FoldItem): string {
  const abandoned = item.rows.filter(isAbandoned).length;
  const completed = `${item.rows.length - abandoned} completed`;
  return abandoned > 0 ? `${completed}, ${abandoned} abandoned` : completed;
}

function foldHover(item: FoldItem): string {
  const lead = item.held
    ? "Stays open: the chosen document is inside"
    : `${item.open ? "Hide" : "Show"} the ${item.rows.length} completed`;
  const names = item.rows.map((row) => (row.shortId ? `${row.shortId} ${row.name}` : row.name));
  return [`${lead}:`, ...names].join("\n");
}

function FoldRow({ item }: { item: FoldItem }) {
  const text = foldText(item);
  return (
    <ListBoxItem
      id={item.key}
      textValue={text}
      // The caret is a sign for the eye; the name says the same in words.
      aria-label={`${text}, ${item.open ? "shown" : "hidden"}`}
      className={rowItem}
      data-depth={item.depth}
      data-fold={item.open ? "open" : "closed"}
    >
      <div className={rowLine} style={indent(item.depth)} title={foldHover(item)}>
        <span className={caretCell} aria-hidden="true">
          {item.open ? "▾" : "▸"}
        </span>
        <span className={markCell}>
          <span className={statusDot({ tone: "done" })} aria-hidden="true" />
        </span>
        <span className={rowTitle({ weight: "fold" })}>{text}</span>
      </div>
    </ListBoxItem>
  );
}

// The command facts for a master-shaped row: an orchestration doc IS the sprint tier; a master
// named in some orchestration doc's `orchestrates` takes the management tier and nests under it.
// Docs commanded by nothing carry neither — the tiers vanish in a flat run.
function commandFacts(
  doc: Pick<TaskDocNode, "kind" | "docPath" | "id" | "title" | "orchestrates">,
  allDocs: TaskDocNode[],
): { tier?: CommandTier; parentKey?: string } {
  if (doc.kind !== "master") return {};
  if (isOrchestrationDoc(doc)) return { tier: "orchestration" };
  const commander = orchestratorParentKey(masterCommandNames(doc), allDocs, doc.docPath);
  return commander ? { tier: "management", parentKey: commander } : {};
}

function taskRepo(doc: TaskDocNode, lifecycle: LifecycleProjection | undefined): string {
  return doc.repository || lifecycle?.repoId || "—";
}

function taskPhase(
  doc: TaskDocNode,
  lifecycle: LifecycleProjection | undefined,
): string {
  return lifecycle?.phase ?? doc.status;
}

function taskGate(lifecycle: LifecycleProjection | undefined): string {
  return gateHint(lifecycle?.gate?.kind);
}

function taskChatIdentity(
  doc: TaskDocNode,
  lifecycle: LifecycleProjection | undefined,
): ChatActivityIdentity {
  return {
    ...(taskDocumentRefForDoc(doc) ? { taskDocumentRef: taskDocumentRefForDoc(doc) } : {}),
    ...(lifecycle ? { lifecycleId: lifecycle.id } : {}),
  };
}

// What a document row is called: a task carries its id, a sprint and a master their title alone.
function docNames(
  doc: TaskDocNode,
  tier: CommandTier | undefined,
  masterIds: ReadonlyMap<string, string>,
): Pick<OperationRow, "heading" | "name" | "docId" | "shortId" | "kind"> {
  if (doc.kind === "master") {
    const kind = tier === "orchestration" ? "sprint" : "master";
    return { heading: doc.title, name: doc.title, docId: "", shortId: "", kind };
  }
  return {
    heading: doc.id ? `${doc.id}. ${doc.title}` : doc.title,
    name: doc.title,
    docId: doc.id,
    shortId: shortTaskId(doc, masterIds),
    kind: "task",
  };
}

// The figure of a document row: the tasks completed of a master, the steps done of a task.
function docProgress(doc: TaskDocNode): Pick<OperationRow, "progress" | "progressNoun"> {
  if (doc.kind !== "master") {
    return { progress: taskStepProgress(doc), progressNoun: "Steps done" };
  }
  return {
    progress: { ...subTaskProgress(doc.subTasks), discarded: doc.discardedCount ?? 0 },
    progressNoun: "Tasks completed",
  };
}

function docRow(
  doc: TaskDocNode,
  lifecycle: LifecycleProjection | undefined,
  pickup: AgentPickupNode | undefined,
  worktree: boolean,
  context: DocRowContext,
): OperationRow {
  const { input, docPaths, masterIds } = context;
  const command = commandFacts(doc, input.docs);
  return {
    key: taskDocSelectionKey(doc.docPath),
    label: taskDocHierarchyLabel(doc, input.series),
    ...docNames(doc, command.tier, masterIds),
    repo: taskRepo(doc, lifecycle),
    phase: taskPhase(doc, lifecycle),
    status: doc.status,
    lifecycle,
    gate: taskGate(lifecycle),
    pickup,
    chatIdentity: taskChatIdentity(doc, lifecycle),
    ...docProgress(doc),
    currentStep: doc.currentStep,
    worktree,
    ageSeconds: servedAgeSeconds(lifecycle, lifecycle?.staleSeconds, input.nowMs),
    createdAt: doc.createdAt,
    fallbackOrder: doc.docPath,
    parentKey: command.parentKey ?? taskDocParentKey(doc, input.series, docPaths),
    depth: 0,
    tier: command.tier,
  };
}

function seriesRow(
  series: SeriesNode,
  lifecycle: LifecycleProjection | undefined,
  pickup: AgentPickupNode | undefined,
  allDocs: TaskDocNode[],
  nowMs: number,
): OperationRow {
  // A folder-keyed series fallback row is still a master seat: it answers to its seriesId (the
  // task folder), its title, or its doc folder when an orchestration doc names it.
  const commander = seriesCommanderParent(series, allDocs);
  return {
    key: seriesSelectionKey(series.seriesId),
    label: series.title,
    heading: series.title,
    name: series.title,
    docId: "",
    shortId: "",
    kind: "master",
    repo: series.repository ?? lifecycle?.repoId ?? "—",
    phase: lifecycle?.phase ?? series.status,
    status: series.status,
    lifecycle,
    gate: taskGate(lifecycle),
    pickup,
    chatIdentity: lifecycle ? { lifecycleId: lifecycle.id } : {},
    progress: { ...seriesProgress(series), discarded: series.discardedCount },
    progressNoun: "Tasks completed",
    worktree: false,
    ageSeconds: servedAgeSeconds(lifecycle, lifecycle?.staleSeconds, nowMs),
    createdAt: series.createdAt,
    fallbackOrder: series.docPath,
    parentKey: commander,
    depth: 0,
    tier: commander ? "management" : undefined,
  };
}

function seriesCommanderParent(
  series: SeriesNode,
  allDocs: TaskDocNode[],
): string | undefined {
  return orchestratorParentKey(
    [
      series.seriesId,
      series.title,
      pathDir(series.docPath).split("/").filter(Boolean).pop() ?? "",
    ].filter(Boolean),
    allDocs,
  );
}

function lifecycleRow(
  lifecycle: LifecycleProjection,
  docs: TaskDocNode[],
  enclosure: EnclosureNode | undefined,
  pickup: AgentPickupNode | undefined,
  seriesList: SeriesNode[],
  masterDocPaths: Set<string>,
  nowMs: number,
): OperationRow {
  const label = taskLabel(lifecycle, docs, enclosure);
  // One task document gives the row its figure and step; several are a series of slices, which
  // the hover counts.
  const only = docs.length === 1 ? docs[0] : undefined;
  return {
    key: lifecycleSelectionKey(lifecycle.id),
    label,
    heading: label,
    name: label,
    docId: "",
    shortId: "",
    kind: "task",
    repo: lifecycle.repoId ?? "—",
    phase: lifecycle.phase,
    // No document stands behind this row: its lifecycle says its state once it has reported,
    // and until then the row says "waiting" (rowState.ts).
    status: "",
    lifecycle,
    gate: gateHint(lifecycle.gate?.kind),
    pickup,
    chatIdentity: {
      ...(only && taskDocumentRefForDoc(only)
        ? { taskDocumentRef: taskDocumentRefForDoc(only) }
        : {}),
      lifecycleId: lifecycle.id,
    },
    progress: only ? taskStepProgress(only) : undefined,
    progressNoun: "Steps done",
    documents: docs.length > 1 ? docs.length : undefined,
    currentStep: only?.currentStep,
    worktree: Boolean(enclosure),
    ageSeconds: servedAgeSeconds(lifecycle, lifecycle.staleSeconds, nowMs),
    createdAt: lifecycle.startedAt,
    fallbackOrder: lifecycle.id,
    // A lifecycle with an enclosure but no matching doc (e.g. a reopened/orphaned leaf) still
    // belongs under its master; carry the parent so it nests instead of floating top-level.
    parentKey: masterParentKeyForEnclosure(enclosure, seriesList, masterDocPaths),
    depth: 0,
  };
}

// The master a worktree enclosure belongs to: its `taskRoot` is the master task folder and the
// master series doc lives directly in it. Mirrors taskHierarchy's parentSelectionKey so an orphaned
// lifecycle row nests under the same master node a normal subtask doc would.
function masterParentKeyForEnclosure(
  enclosure: EnclosureNode | undefined,
  seriesList: SeriesNode[],
  masterDocPaths: Set<string>,
): string | undefined {
  if (!enclosure) return undefined;
  const series = seriesList.find((item) => pathDir(item.docPath) === enclosure.taskRoot);
  if (!series) return undefined;
  return masterDocPaths.has(series.docPath)
    ? taskDocSelectionKey(series.docPath)
    : seriesSelectionKey(series.seriesId);
}

// BY PHASE: the phases in the order of the work, then the statuses of documents that have no
// lifecycle. A phase this build does not know stands last.
const PHASE_ORDER: readonly string[] = [...PHASES, "inProgress", "planning", "Completed", "abandoned"];

function comparePhases(left: string, right: string): number {
  const rank = (phase: string) => {
    const index = PHASE_ORDER.indexOf(phase);
    return index < 0 ? PHASE_ORDER.length : index;
  };
  return rank(left) - rank(right) || left.localeCompare(right);
}

// BY REPO is the tree: parents before children, to any depth, with the order and the line of
// completed ones of treeRows.ts. BY PHASE has no tree: every row of a phase in creation order.
function groupRows(
  rows: OperationRow[],
  pivot: Pivot,
  view: Omit<TreeView, "scope">,
): OperationGroup[] {
  const byKey = new Map<string, OperationRow[]>();
  for (const item of rows) {
    const key = pivot === "repo" ? item.repo : item.phase;
    const group = byKey.get(key);
    if (group) group.push(item);
    else byKey.set(key, [item]);
  }
  const byRepo = pivot === "repo";
  return [...byKey.entries()]
    .sort(([a], [b]) => (byRepo ? a.localeCompare(b) : comparePhases(a, b)))
    .map(([key, members]) => ({
      key,
      // A status takes the task page's word; a phase keeps its own.
      label: byRepo ? key : statusLabel(key),
      count: members.length,
      items: byRepo
        ? treeItems(members, (item) => rowWord(item).band, { ...view, scope: key })
        : members.sort(compareRows).map(flatItem),
    }));
}

function flatItem(row: OperationRow): TreeItem<OperationRow> {
  return { kind: "row", row, hasChildren: false, collapsed: false, held: false };
}

function heldFoldKeys(groups: OperationGroup[]): Set<string> {
  const held = new Set<string>();
  for (const group of groups) {
    for (const item of group.items) if (item.kind === "fold" && item.held) held.add(item.key);
  }
  return held;
}

// The choice has a key of its own, and a row it stands on. For a lifecycle that a task document
// carries the two differ: the list's selection stays the lifecycle (no row has its key, as
// before), and the document's row is marked, held open and brought into view. A click on that
// row then chooses the document, as a click on it always did.
interface Choice {
  // The list's own selection.
  selectedKey: string | null;
  // The row the choice stands on.
  chosenKey: string | null;
  // That row, where it is not the list's own selection.
  markedKey: string | null;
  // The number of the bell's request that this choice answers, 0 for none.
  request: number;
  // Whether the tree holds a row for the choice.
  present: boolean;
}

function readChoice(
  rows: OperationRow[],
  selection: ReturnType<typeof parseTaskSelection>,
  selectedId: string | null,
  reveal: RevealRequest,
): Choice {
  const selectedKey = selection ? selectionKey(selection) : selectedId;
  const chosenKey = chosenRowKey(rows, selection, selectedId);
  // The bell's layer names a lifecycle; the shell stores the choice as that lifecycle's selection.
  const asked = selection?.kind === "lifecycle" && selection.lifecycleId === reveal.id;
  return {
    selectedKey,
    chosenKey,
    markedKey: chosenKey !== selectedKey ? chosenKey : null,
    request: asked ? reveal.count : 0,
    present: rows.some((item) => item.key === chosenKey),
  };
}

// The row a selection stands on. An entry of the attention queue opens its lifecycle; where a
// task document carries that lifecycle, its row is the chosen one, so the tree opens down to it.
function chosenRowKey(
  rows: OperationRow[],
  selection: ReturnType<typeof parseTaskSelection>,
  selectedId: string | null,
): string | null {
  if (!selection) return selectedId;
  const key = selectionKey(selection);
  if (selection.kind !== "lifecycle" || rows.some((item) => item.key === key)) return key;
  const carriers = rows.filter((item) => item.lifecycle?.id === selection.lifecycleId);
  return (carriers.find((item) => item.kind === "task") ?? carriers[0])?.key ?? key;
}

function selectionKey(selection: ReturnType<typeof parseTaskSelection>): string | null {
  if (!selection) return null;
  if (selection.kind === "taskdoc") return taskDocSelectionKey(selection.docPath);
  if (selection.kind === "series") return seriesSelectionKey(selection.seriesId);
  return lifecycleSelectionKey(selection.lifecycleId);
}

// The gate a row says is the DURABLE gate kind only. The wait-loop-era fallback to the lifecycle's bare
// `ask` payload (the question string, else the literal "ask") was retired with notify-and-continue: the
// attention queue carries the notification and GateResponder owns durable gates, so a bare `ask` no
// longer renders a gate in the tasks row.
function gateHint(kind: string | undefined): string {
  return kind ?? "";
}

function groupDocs(docs: TaskDocNode[]): Map<string, TaskDocNode[]> {
  const byLifecycle = new Map<string, TaskDocNode[]>();
  for (const doc of docs) {
    if (!doc.lifecycleId) continue;
    const list = byLifecycle.get(doc.lifecycleId);
    if (list) list.push(doc);
    else byLifecycle.set(doc.lifecycleId, [doc]);
  }
  return byLifecycle;
}

function groupPickups(pickups: AgentPickupNode[]): Map<string, AgentPickupNode[]> {
  const byLifecycle = new Map<string, AgentPickupNode[]>();
  for (const pickup of pickups) {
    if (!pickup.lifecycleId) continue;
    const list = byLifecycle.get(pickup.lifecycleId);
    if (list) list.push(pickup);
    else byLifecycle.set(pickup.lifecycleId, [pickup]);
  }
  return byLifecycle;
}

function pickupForLifecycle(
  lifecycle: LifecycleProjection | undefined,
  byLifecycle: Map<string, AgentPickupNode[]>,
): AgentPickupNode | undefined {
  if (!lifecycle) return undefined;
  return byLifecycle.get(lifecycle.id)?.[0];
}

function runtimeForDoc(
  doc: Pick<TaskDocNode, "docPath" | "lifecycleId">,
  lifecycles: Record<string, LifecycleProjection>,
  enclosures: EnclosureNode[],
): LifecycleProjection | undefined {
  if (doc.lifecycleId) return lifecycles[doc.lifecycleId];
  const dir = pathDir(doc.docPath);
  const enclosure = enclosures.find(
    (item) =>
      item.taskRoot === dir &&
      (item.lifecycleId === item.taskId || item.lifecycleId === item.taskName),
  );
  return enclosure ? lifecycles[enclosure.lifecycleId] : undefined;
}

function isRootTaskDoc(doc: Pick<TaskDocNode, "kind" | "docPath">): boolean {
  return doc.kind === "master" || pathStem(doc.docPath) === "task";
}

function enclosureForDoc(
  doc: Pick<TaskDocNode, "id" | "docPath" | "lifecycleId">,
  enclosures: EnclosureNode[],
): EnclosureNode | undefined {
  const dir = pathDir(doc.docPath);
  // Enclosure leaf ids are lowercase directory names while doc ids are
  // uppercase: every leafId comparison here is case-insensitive. Exact joins only: since
  // task_reopen, reopening a leaf reuses its EXACT leaf id, so the old `-rN`
  // suffix admission heuristic is gone.
  const stem = pathStem(doc.docPath).toLowerCase();
  const docId = doc.id ? doc.id.toLowerCase() : undefined;
  return enclosures.find((enclosure) => {
    if (enclosure.taskRoot !== dir) return false;
    const leafId = enclosure.leafId.toLowerCase();
    return leafId === stem || (docId !== undefined && leafId === docId);
  });
}

// The lifecycle bound to an enclosure, following the cross-ref in either direction: the
// contract's recorded lifecycleId, or a live lifecycle still anchored to the enclosure
// (lifecycle.enclosure). This is the annotation source for a doc row whose own lifecycleId is
// unset or stale: the bound lifecycle's gate/staleness enrich the leaf's single row instead
// of rendering a duplicate lifecycle card for the same enclosureId.
function lifecycleForEnclosure(
  enclosure: EnclosureNode,
  lifecycles: LifecycleProjection[],
  lifecycleById: Record<string, LifecycleProjection>,
): LifecycleProjection | undefined {
  if (enclosure.lifecycleId && lifecycleById[enclosure.lifecycleId]) {
    return lifecycleById[enclosure.lifecycleId];
  }
  // Anchor fallback: several lifecycles may anchor one enclosure with no contract
  // lifecycleId — the most recently active one annotates the row (ISO timestamps
  // compare lexicographically), never whichever happened to project first.
  return lifecycles
    .filter((lifecycle) => lifecycle.enclosure === enclosure.enclosure)
    .reduce<LifecycleProjection | undefined>(
      (latest, lifecycle) =>
        !latest || lifecycle.lastEventTs > latest.lastEventTs ? lifecycle : latest,
      undefined,
    );
}

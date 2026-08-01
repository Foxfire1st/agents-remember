import { memo, useState, type CSSProperties } from "react";

import {
  Header,
  ListBox,
  ListBoxItem,
  ListBoxSection,
  ToggleButton,
  ToggleButtonGroup,
} from "react-aria-components";

import { css, cva } from "../../styled-system/css";
import { fmtWait, hasLiveWorktree, type Pivot } from "../data/selectors";
import { servedAgeSeconds, useNowMs } from "../data/servedAges";
import { type OpenSession, useSessions } from "../data/sessions";
import { useDashboard } from "../data/store";
import {
  isOrchestrationDoc,
  masterCommandNames,
  orchestratorParentKey,
  pathDir,
  pathStem,
  taskDocHierarchyLabel,
  taskDocParentKey,
} from "../data/taskHierarchy";
import {
  findLifecycleEnclosure,
  groupEnclosuresByLifecycle,
  lifecycleSelectionKey,
  parseTaskSelection,
  qualifiedLeafKey,
  seriesSelectionKey,
  taskDocSelectionKey,
  taskLabel,
} from "../data/taskIdentity";
import { Dot } from "../grammar/Dot";
import { Panel } from "../grammar/Panel";
import { RankBadge, type RankTier } from "../grammar/RankBadge";
import type {
  AgentPickupNode,
  Analytics,
  EnclosureNode,
  LifecycleProjection,
  SeriesNode,
  TaskDocNode,
} from "../types/projection";
import { AgentPickupIndicator } from "./AgentPickupIndicator";
import {
  ChatActivityIndicator,
  summarizeChatActivity,
  type ChatActivityIdentity,
  type ChatActivitySummary,
} from "./ChatActivityIndicator";
import { TaskGroupDisclosure } from "./TaskGroupDisclosure";
import { useCollapsedTaskGroups } from "./useCollapsedTaskGroups";

// The single unit list (note 01: the lifecycle is THE unit; note 06 IA). A BY REPO | BY PHASE pivot
// (React Aria ToggleButtonGroup) over every lifecycle (fleeting + persistent), presented as a React
// Aria ListBox so the 30+ rows are arrow-navigable + type-aheadable from the keyboard; selecting a
// row drives the centre detail. A task-progress hint surfaces the work each lifecycle carries.
const sizing = css({ flex: "1 1 0", minWidth: "0", overflowX: "hidden" });
const headRow = css({
  display: "flex",
  alignItems: "center",
  justifyContent: "space-between",
  gap: "0.5rem",
  minWidth: "0",
});
const headTitle = css({ margin: "0" });
const pivotBar = css({ display: "flex", gap: "0.25rem" });
const pivotBtn = css({
  font: "inherit",
  fontSize: "0.7rem",
  letterSpacing: "0.06em",
  paddingInline: "0.45rem",
  paddingBlock: "0.2rem",
  background: "bg",
  color: "muted",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "grid",
  borderRadius: "2px",
  cursor: "pointer",
  _selected: { color: "amber", borderColor: "amber" },
  _focusVisible: { outline: "1px solid token(colors.amber)", outlineOffset: "1px" },
});
const listBox = css({
  listStyle: "none",
  display: "grid",
  gridTemplateColumns: "minmax(0, 1fr)",
  gap: "0.45rem",
  width: "100%",
  minWidth: "0",
  margin: "0",
  padding: "0",
  outline: "none",
});
const section = css({
  display: "grid",
  gridTemplateColumns: "minmax(0, 1fr)",
  gap: "0.12rem",
  width: "100%",
  minWidth: "0",
});
const groupHeader = css({
  color: "amber",
  fontSize: "0.74rem",
  letterSpacing: "0.06em",
  textTransform: "uppercase",
  paddingBlock: "0.1rem",
  minWidth: "0",
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
});
const row = cva({
  base: {
    position: "relative", // anchors the tier fold-corner pseudo-element
    display: "flex",
    alignItems: "baseline",
    gap: "0.3rem",
    width: "100%",
    maxWidth: "100%",
    minWidth: "0",
    background: "bg",
    borderLeftWidth: "2px",
    borderLeftStyle: "solid",
    borderLeftColor: "cyan",
    paddingInline: "0.4rem",
    paddingBlock: "0.25rem",
    cursor: "pointer",
    outline: "none",
    _selected: { outline: "1px solid token(colors.amber)" },
    _focusVisible: { outline: "1px solid token(colors.amber)" },
  },
  variants: {
    fleeting: { true: { borderLeftStyle: "dashed", opacity: "0.85" } },
    nested: {
      true: {
        paddingLeft: "1rem",
        borderLeftColor: "grid",
      },
    },
    // The command treatment: a folded corner top-left, a tier ghost wash fading
    // into the row bg, and — orchestration only — a gold top hairline. Renders ONLY on rows whose
    // tier is set, i.e. when an orchestration task exists; flat runs never see it.
    tier: {
      orchestration: {
        borderTopWidth: "1px",
        borderTopStyle: "solid",
        borderTopColor: "goldDim",
        backgroundImage:
          "linear-gradient(90deg, token(colors.goldGhost), token(colors.bg) 34%)",
        _before: {
          content: '""',
          position: "absolute",
          top: "0",
          left: "0",
          borderStyle: "solid",
          borderWidth: "13px 13px 0 0",
          borderColor: "token(colors.gold) transparent transparent transparent",
        },
      },
      management: {
        backgroundImage:
          "linear-gradient(90deg, token(colors.purpleGhost), token(colors.bg) 30%)",
        _before: {
          content: '""',
          position: "absolute",
          top: "0",
          left: "0",
          borderStyle: "solid",
          borderWidth: "13px 13px 0 0",
          borderColor: "token(colors.purpleDim) transparent transparent transparent",
        },
      },
    },
  },
});
const rowId = css({
  minWidth: "0",
  flex: "1 1 0",
  fontWeight: "600",
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
});
const rowSec = css({
  flex: "0 1 2.6rem",
  minWidth: "0",
  maxWidth: "2.6rem",
  color: "cyan",
  fontSize: "0.76rem",
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
});
const rowGate = css({
  minWidth: "0",
  flex: "0 1 4rem",
  maxWidth: "4rem",
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
  fontSize: "0.66rem",
  color: "amber",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "amber",
  borderRadius: "2px",
  paddingInline: "0.28rem",
  paddingBlock: "0.04rem",
});
const rowMeta = css({
  flex: "0 1 4rem",
  minWidth: "2.5rem",
  maxWidth: "4rem",
  color: "muted",
  fontSize: "0.72rem",
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
});
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
      pivot={pivot}
      setPivot={setPivot}
      collapsedKeys={collapsedKeys}
      toggleCollapsed={toggleCollapsed}
      lifecycles={lifecycles}
      enclosures={enclosures}
      analytics={analytics}
      sessions={sessions}
      nowMs={nowMs}
    />
  );
}

interface LifecycleListRenderProps {
  active: boolean;
  selectedId: string | null;
  onSelect: (id: string) => void;
  pivot: Pivot;
  setPivot: (pivot: Pivot) => void;
  collapsedKeys: ReadonlySet<string>;
  toggleCollapsed: (key: string) => void;
  lifecycles: Record<string, LifecycleProjection>;
  enclosures: Record<string, EnclosureNode>;
  analytics: Analytics | null;
  sessions: OpenSession[];
  nowMs: number;
}

const LifecycleListRender = memo(
  function LifecycleListRender({
    selectedId,
    onSelect,
    pivot,
    setPivot,
    collapsedKeys,
    toggleCollapsed,
    lifecycles,
    enclosures,
    analytics,
    sessions,
    nowMs,
  }: LifecycleListRenderProps) {
    const docs = analytics?.taskDocuments ?? [];
    const series = analytics?.series ?? [];
    const agentPickups = analytics?.agentPickups ?? [];
    const enclosuresByLifecycle = groupEnclosuresByLifecycle(Object.values(enclosures));
    const rows = operationRows({
      lifecycles: Object.values(lifecycles),
      lifecycleById: lifecycles,
      enclosures,
      enclosuresByLifecycle,
      docs,
      series,
      agentPickups,
      sessions,
      nowMs,
    });
    const groups = groupRows(rows, pivot);
    const selectedSelection = parseTaskSelection(selectedId, lifecycles, analytics);
    const selectedKey = selectedSelection ? selectionKey(selectedSelection) : selectedId;

    const head = (
      <div className={headRow}>
        <h2 className={headTitle}>Tasks · {rows.length}</h2>
        <ToggleButtonGroup
          className={pivotBar}
          selectionMode="single"
          disallowEmptySelection
          selectedKeys={[pivot]}
          onSelectionChange={(keys) => {
            const next = [...keys][0];
            if (next === "repo" || next === "phase") setPivot(next);
          }}
          aria-label="Group tasks by"
        >
          <ToggleButton id="repo" className={pivotBtn}>
            BY REPO
          </ToggleButton>
          <ToggleButton id="phase" className={pivotBtn}>
            BY PHASE
          </ToggleButton>
        </ToggleButtonGroup>
      </div>
    );

    return (
      <Panel testid="lifecycle-list" head={head} className={sizing}>
        {rows.length === 0 ? (
          <p className="muted">No tasks.</p>
        ) : (
          <ListBox
            className={listBox}
            aria-label="Tasks"
            selectionMode="single"
            selectedKeys={selectedKey ? [selectedKey] : []}
            onSelectionChange={(keys) => {
              const id = [...keys][0];
              if (typeof id === "string") onSelect(id);
            }}
          >
            {groups.map((group) => {
              const descendantKeys = descendantBearingKeys(group.rows);
              const visibleRows =
                pivot === "repo" ? visibleHierarchyRows(group.rows, collapsedKeys) : group.rows;
              return (
                <ListBoxSection key={group.key} className={section}>
                  <Header className={groupHeader}>{group.label}</Header>
                  {visibleRows.map((item) => {
                    const secondary = pivot === "repo" ? item.secondary : item.repo;
                    const hasDescendants =
                      pivot === "repo" &&
                      item.secondary === "master" &&
                      descendantKeys.has(item.key);
                    const collapsed = collapsedKeys.has(item.key);
                    return (
                      <ListBoxItem
                        key={item.key}
                        id={item.key}
                        textValue={item.label}
                        className={row({
                          fleeting: item.fleeting,
                          // Tier rows carry the command treatment and indent by margin (below); non-tier
                          // nesting keeps today's leaf look untouched (the flat-run regression rule).
                          nested: item.depth > 0 && !item.tier,
                          tier: item.tier,
                        })}
                        style={indentStyle(item)}
                        data-depth={item.depth}
                        data-parent-key={item.parentKey}
                        data-tier={item.tier}
                      >
                        {hasDescendants ? (
                          <TaskGroupDisclosure
                            label={item.label}
                            collapsed={collapsed}
                            onToggle={() => toggleCollapsed(item.key)}
                          />
                        ) : null}
                        <span
                          aria-label={`Task progress: ${item.variant}; phase: ${item.phase}`}
                          title={`Task progress: ${item.variant}; phase: ${item.phase}`}
                          data-testid="task-state"
                        >
                          <Dot variant={item.variant} />
                        </span>
                        {item.tier ? <RankBadge tier={item.tier} size="row" /> : null}
                        <span className={rowId} title={item.title}>
                          {item.label}
                        </span>
                        <span className={rowSec}>{secondary}</span>
                        <ChatActivityIndicator summary={item.chatActivity} />
                        <AgentPickupIndicator pickup={item.pickup} />
                        {item.gate ? <span className={rowGate}>{item.gate}</span> : null}
                        <span className={rowMeta}>
                          {item.meta}
                          {item.inferred ? " · inf" : ""}
                        </span>
                      </ListBoxItem>
                    );
                  })}
                </ListBoxSection>
              );
            })}
          </ListBox>
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

interface OperationRow {
  key: string;
  label: string;
  title: string;
  repo: string;
  phase: string;
  secondary: string;
  variant: string;
  meta: string;
  gate: string;
  pickup?: AgentPickupNode;
  chatIdentity: ChatActivityIdentity;
  chatActivity?: ChatActivitySummary;
  createdAt: string;
  fallbackOrder: string;
  parentKey?: string;
  depth: number;
  // The command tier: "orchestration" for a master doc carrying `orchestrates`,
  // "management" for a master commanded by one. Unset (no insignia) everywhere else.
  tier?: RankTier;
  fleeting: boolean;
  inferred: boolean;
}

// The 22px indent grammar: tier rows indent by their full depth; a non-tier row's
// first level of nesting is today's `nested` padding (unchanged — the flat-run regression rule),
// so only levels beyond it add margin (a leaf under a commanded master sits one step further).
function indentStyle(item: Pick<OperationRow, "depth" | "tier">): CSSProperties | undefined {
  const steps = item.tier ? item.depth : Math.max(0, item.depth - 1);
  return steps > 0 ? { marginLeft: `${steps * 22}px` } : undefined;
}

interface OperationGroup {
  key: string;
  label: string;
  rows: OperationRow[];
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
        input.series,
        docPaths,
        pickupForLifecycle(lifecycle, pickupsByLifecycle),
        input.docs,
        input.nowMs,
      ),
    );
  }

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

  return rows
    .map((item) => ({
      ...item,
      chatActivity: summarizeChatActivity(input.sessions, item.chatIdentity),
    }))
    .sort(compareRows);
}

// The command facts for a master-shaped row: an orchestration doc IS the gold tier; a master
// named in some orchestration doc's `orchestrates` takes the purple tier and nests under it. Docs
// commanded by nothing carry neither — the whole treatment vanishes in a flat run.
function commandFacts(
  doc: Pick<TaskDocNode, "kind" | "docPath" | "id" | "title" | "orchestrates">,
  allDocs: TaskDocNode[],
): { tier?: RankTier; parentKey?: string } {
  if (doc.kind !== "master") return {};
  if (isOrchestrationDoc(doc)) return { tier: "orchestration" };
  const commander = orchestratorParentKey(masterCommandNames(doc), allDocs, doc.docPath);
  return commander ? { tier: "management", parentKey: commander } : {};
}

function docRow(
  doc: TaskDocNode,
  lifecycle: LifecycleProjection | undefined,
  seriesList: SeriesNode[],
  masterDocPaths: Set<string>,
  pickup: AgentPickupNode | undefined,
  allDocs: TaskDocNode[],
  nowMs: number,
): OperationRow {
  const progress = doc.kind === "master" ? subTaskProgress(doc.subTasks) : topLevelStepProgress(doc);
  const label = taskDocHierarchyLabel(doc, seriesList);
  const repo = doc.repository || lifecycle?.repoId || "—";
  const phase = lifecycle?.phase ?? doc.status;
  const variant = lifecycle?.state ?? statusVariant(doc.status);
  const gate = gateHint(lifecycle?.gate?.kind);
  const command = commandFacts(doc, allDocs);
  return {
    key: taskDocSelectionKey(doc.docPath),
    label,
    title: taskTitle({
      label,
      lifecycle,
      state: variant,
      phase,
      repo,
      gate,
      currentStep: doc.currentStep,
    }),
    repo,
    phase,
    secondary: doc.kind,
    variant,
    meta: rowMetaText(
      progressHint(progress),
      doc.status,
      servedAgeSeconds(lifecycle, lifecycle?.staleSeconds, nowMs),
    ),
    gate,
    pickup,
    chatIdentity: {
      leafKey: qualifiedLeafKey(doc),
      ...(lifecycle ? { lifecycleId: lifecycle.id } : {}),
    },
    createdAt: doc.createdAt ?? "",
    fallbackOrder: doc.docPath,
    parentKey: command.parentKey ?? taskDocParentKey(doc, seriesList, masterDocPaths),
    depth: 0,
    tier: command.tier,
    fleeting: lifecycle?.fleeting ?? false,
    inferred: lifecycle?.inferred ?? false,
  };
}

function seriesRow(
  series: SeriesNode,
  lifecycle: LifecycleProjection | undefined,
  pickup: AgentPickupNode | undefined,
  allDocs: TaskDocNode[],
  nowMs: number,
): OperationRow {
  const repo = series.repository || lifecycle?.repoId || "—";
  const phase = lifecycle?.phase ?? series.status;
  const variant = lifecycle?.state ?? statusVariant(series.status);
  const gate = gateHint(lifecycle?.gate?.kind);
  // A folder-keyed series fallback row is still a master seat: it answers to its seriesId (the
  // task folder), its title, or its doc folder when an orchestration doc names it.
  const commander = orchestratorParentKey(
    [
      series.seriesId,
      series.title,
      pathDir(series.docPath).split("/").filter(Boolean).pop() ?? "",
    ].filter(Boolean),
    allDocs,
  );
  return {
    key: seriesSelectionKey(series.seriesId),
    label: series.title,
    title: taskTitle({
      label: series.title,
      lifecycle,
      state: variant,
      phase,
      repo,
      gate,
    }),
    repo,
    phase,
    secondary: "master",
    variant,
    meta: rowMetaText(
      progressHint({ done: series.doneCount, total: series.totalCount }),
      series.status,
      servedAgeSeconds(lifecycle, lifecycle?.staleSeconds, nowMs),
    ),
    gate,
    pickup,
    chatIdentity: lifecycle ? { lifecycleId: lifecycle.id } : {},
    createdAt: series.createdAt ?? "",
    fallbackOrder: series.docPath,
    parentKey: commander,
    depth: 0,
    tier: commander ? "management" : undefined,
    fleeting: lifecycle?.fleeting ?? false,
    inferred: lifecycle?.inferred ?? false,
  };
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
  const repo = lifecycle.repoId ?? "—";
  const gate = gateHint(lifecycle.gate?.kind);
  const currentStep = docs.length === 1 ? docs[0].currentStep : undefined;
  return {
    key: lifecycleSelectionKey(lifecycle.id),
    label,
    title: taskTitle({
      label,
      lifecycle,
      state: lifecycle.state,
      phase: lifecycle.phase,
      repo,
      gate,
      currentStep,
    }),
    repo,
    phase: lifecycle.phase,
    secondary: lifecycle.phase,
    variant: lifecycle.state,
    meta: rowMetaText(taskHint(docs), "", servedAgeSeconds(lifecycle, lifecycle.staleSeconds, nowMs)),
    gate,
    pickup,
    chatIdentity: {
      leafKey: docs.length === 1 ? qualifiedLeafKey(docs[0]) : undefined,
      lifecycleId: lifecycle.id,
    },
    createdAt: lifecycle.startedAt,
    fallbackOrder: lifecycle.id,
    // A lifecycle with an enclosure but no matching doc (e.g. a reopened/orphaned leaf) still
    // belongs under its master; carry the parent so it nests instead of floating top-level.
    parentKey: masterParentKeyForEnclosure(enclosure, seriesList, masterDocPaths),
    depth: 0,
    fleeting: lifecycle.fleeting,
    inferred: lifecycle.inferred,
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

function groupRows(rows: OperationRow[], pivot: Pivot): OperationGroup[] {
  const byKey = new Map<string, OperationRow[]>();
  for (const item of rows) {
    const key = pivot === "repo" ? item.repo : item.phase;
    const group = byKey.get(key);
    if (group) group.push(item);
    else byKey.set(key, [item]);
  }
  return [...byKey.entries()]
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([key, members]) => ({
      key,
      label: key,
      rows: pivot === "repo" ? hierarchyRows(members) : members.sort(compareRows),
    }));
}

// Depth-first hierarchy flatten. Historically two levels (master > leaf); the orchestration
// tier adds a third (orchestration > master > leaf), so this walks parent links to any depth.
// A `seen` guard plus the trailing sweep keep pathological parent data (a cycle, e.g. two
// orchestration docs naming each other) from dropping rows: unreachable rows append top-level.
function hierarchyRows(rows: OperationRow[]): OperationRow[] {
  const byParent = new Map<string, OperationRow[]>();
  const byKey = new Map(rows.map((item) => [item.key, item]));
  for (const item of rows) {
    if (!item.parentKey || !byKey.has(item.parentKey) || item.parentKey === item.key) continue;
    const children = byParent.get(item.parentKey);
    if (children) children.push(item);
    else byParent.set(item.parentKey, [item]);
  }
  const roots = rows
    .filter((item) => !item.parentKey || !byKey.has(item.parentKey) || item.parentKey === item.key)
    .sort(compareRows);
  const out: OperationRow[] = [];
  const seen = new Set<string>();
  const visit = (item: OperationRow, depth: number) => {
    if (seen.has(item.key)) return;
    seen.add(item.key);
    out.push({ ...item, depth });
    for (const child of (byParent.get(item.key) ?? []).sort(compareRows)) {
      visit(child, depth + 1);
    }
  };
  for (const root of roots) visit(root, 0);
  for (const item of [...rows].sort(compareRows)) {
    if (!seen.has(item.key)) visit(item, 0);
  }
  return out;
}

function descendantBearingKeys(rows: OperationRow[]): Set<string> {
  const rowKeys = new Set(rows.map((item) => item.key));
  return new Set(
    rows
      .map((item) => item.parentKey)
      .filter((key): key is string => key !== undefined && rowKeys.has(key)),
  );
}

// hierarchyRows is depth-first, so collapsed depths form the complete ancestor stack for each row.
// Hidden parents are still visited here, preserving their independent collapse state for later.
function visibleHierarchyRows(
  rows: OperationRow[],
  collapsedKeys: ReadonlySet<string>,
): OperationRow[] {
  const collapsedByDepth: boolean[] = [];
  return rows.filter((item) => {
    collapsedByDepth.length = item.depth;
    const hidden = collapsedByDepth.includes(true);
    collapsedByDepth[item.depth] = collapsedKeys.has(item.key);
    return !hidden;
  });
}

function selectionKey(selection: ReturnType<typeof parseTaskSelection>): string | null {
  if (!selection) return null;
  if (selection.kind === "taskdoc") return taskDocSelectionKey(selection.docPath);
  if (selection.kind === "series") return seriesSelectionKey(selection.seriesId);
  return lifecycleSelectionKey(selection.lifecycleId);
}

// The row's gate chip is the DURABLE gate kind only. The wait-loop-era fallback to the lifecycle's bare
// `ask` payload (the question string, else the literal "ask") was retired with notify-and-continue: the
// attention queue carries the notification and GateResponder owns durable gates, so a bare `ask` no
// longer renders a gate affordance in the tasks row.
function gateHint(kind: string | undefined): string {
  return kind ?? "";
}

function taskTitle(facts: {
  label: string;
  lifecycle?: LifecycleProjection;
  state: string;
  phase: string;
  repo: string;
  gate: string;
  currentStep?: string;
}): string {
  const lines = [`Title: ${facts.label}`];
  if (facts.lifecycle) lines.push(`Lifecycle: ${facts.lifecycle.id}`);
  lines.push(`State: ${facts.lifecycle?.state ?? facts.state}`);
  lines.push(`Phase: ${facts.lifecycle?.phase ?? facts.phase}`);
  if (facts.repo && facts.repo !== "—") lines.push(`Repo: ${facts.repo}`);
  if (facts.gate) lines.push(`Gate: ${facts.gate}`);
  if (facts.currentStep) lines.push(`Current step: ${facts.currentStep}`);
  return lines.join("\n");
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

function taskHint(docs: TaskDocNode[]): string {
  if (docs.length > 1) return `series ${docs.length}`; // a multi-task series (subtask slices)
  if (docs.length === 1) return progressHint(topLevelStepProgress(docs[0])); // single task progress
  return "";
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
  // uppercase: every leafId comparison here is case-insensitive, matching the
  // normalization RailChat and the change-set bar already use. Exact joins only: since
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

function topLevelStepProgress(doc: TaskDocNode): { done: number; total: number } {
  return {
    done: doc.steps.filter((step) => step.status === "done").length,
    total: doc.steps.length,
  };
}

function subTaskProgress(items: TaskDocNode["subTasks"]): { done: number; total: number } {
  return {
    done: items.filter((item) => item.status.toLowerCase() === "completed").length,
    total: items.length,
  };
}

function progressHint(progress: { done: number; total: number }): string {
  return progress.total > 0 ? `${progress.done}/${progress.total}` : "";
}

function rowMetaText(progress: string, status: string, staleSeconds: number | undefined): string {
  const wait = staleSeconds == null ? "" : fmtWait(staleSeconds);
  const parts = [progress, status, wait].filter(Boolean);
  return parts.length > 0 ? parts.join(" · ") : "—";
}

// The DOCUMENT status → dot variant, for rows with no live lifecycle. Its whole input vocabulary
// is `tasks/document.py::DocStatus` = planning | inProgress | Completed, arriving verbatim through
// `TaskDocNode.status` / `SeriesNode.status` (`snapshots.py` assigns `status=doc.status`). So this
// maps exactly two outcomes and nothing else can arrive.
//
// It deliberately does NOT carry the lifecycle vocabulary. Both callers read
// `lifecycle?.state ?? statusVariant(...)`, so blocked/paused/abandoned/awaiting-developer reach
// `Dot` as the LIVE state on the left of the `??` — never through here. Arms for them were
// unreachable and untested (the suite is identical with or without them), which is the same class
// of permanently-dead branch this change removed from the engine-room renderer; adding one for
// `awaiting-developer` would have been the defect the change was written to delete. `blocked`,
// `paused` and `abandoned` were pre-existing instances of it and go with it rather than being left
// as the exception that proves the rule — deleting them is behaviour-identical (the suite is
// unchanged), because the server cannot reach them either.
function statusVariant(status: string): string {
  const normalized = status.toLowerCase();
  if (normalized === "completed") return "completed";
  return "running";
}

function compareRows(left: OperationRow, right: OperationRow): number {
  const created = left.createdAt.localeCompare(right.createdAt);
  return created || left.fallbackOrder.localeCompare(right.fallbackOrder);
}

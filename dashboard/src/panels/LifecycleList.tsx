import { useState } from "react";

import {
  Header,
  ListBox,
  ListBoxItem,
  ListBoxSection,
  ToggleButton,
  ToggleButtonGroup,
} from "react-aria-components";

import { css, cva } from "../../styled-system/css";
import { fmtWait, type Pivot } from "../data/selectors";
import { useDashboard } from "../data/store";
import {
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
  seriesSelectionKey,
  taskDocSelectionKey,
  taskLabel,
} from "../data/taskIdentity";
import { Dot } from "../grammar/Dot";
import { Panel } from "../grammar/Panel";
import type { EnclosureNode, LifecycleProjection, SeriesNode, TaskDocNode } from "../types/projection";

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

export function LifecycleList({
  selectedId,
  onSelect,
}: {
  selectedId: string | null;
  onSelect: (id: string) => void;
}) {
  const [pivot, setPivot] = useState<Pivot>("repo");
  const lifecycles = useDashboard((s) => s.lifecycles);
  const enclosures = useDashboard((s) => s.enclosures);
  const analytics = useDashboard((s) => s.analytics);
  const docs = analytics?.taskDocuments ?? [];
  const series = analytics?.series ?? [];
  const enclosuresByLifecycle = groupEnclosuresByLifecycle(Object.values(enclosures));
  const rows = operationRows({
    lifecycles: Object.values(lifecycles),
    lifecycleById: lifecycles,
    enclosures,
    enclosuresByLifecycle,
    docs,
    series,
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
          {groups.map((group) => (
            <ListBoxSection key={group.key} className={section}>
              <Header className={groupHeader}>{group.label}</Header>
              {group.rows.map((item) => {
                const secondary = pivot === "repo" ? item.secondary : item.repo;
                return (
                  <ListBoxItem
                    key={item.key}
                    id={item.key}
                    textValue={item.label}
                    className={row({ fleeting: item.fleeting, nested: item.depth > 0 })}
                    data-depth={item.depth}
                    data-parent-key={item.parentKey}
                  >
                    <Dot variant={item.variant} />
                    <span className={rowId} title={item.title}>
                      {item.label}
                    </span>
                    <span className={rowSec}>{secondary}</span>
                    {item.gate ? <span className={rowGate}>{item.gate}</span> : null}
                    <span className={rowMeta}>
                      {item.meta}
                      {item.inferred ? " · inf" : ""}
                    </span>
                  </ListBoxItem>
                );
              })}
            </ListBoxSection>
          ))}
        </ListBox>
      )}
    </Panel>
  );
}

interface OperationRowsInput {
  lifecycles: LifecycleProjection[];
  lifecycleById: Record<string, LifecycleProjection>;
  enclosures: Record<string, EnclosureNode>;
  enclosuresByLifecycle: Map<string, EnclosureNode>;
  docs: TaskDocNode[];
  series: SeriesNode[];
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
  createdAt: string;
  fallbackOrder: string;
  parentKey?: string;
  depth: number;
  fleeting: boolean;
  inferred: boolean;
}

interface OperationGroup {
  key: string;
  label: string;
  rows: OperationRow[];
}

function operationRows(input: OperationRowsInput): OperationRow[] {
  const representedLifecycleIds = new Set<string>();
  const docPaths = new Set(input.docs.map((doc) => doc.docPath));
  const docsByLifecycle = groupDocs(input.docs);
  const enclosureList = Object.values(input.enclosures);
  const activeEnclosureList = enclosureList.filter(isActiveEnclosure);
  const activeEnclosures = Object.fromEntries(
    activeEnclosureList.map((item) => [item.enclosure, item]),
  );
  const activeEnclosuresByLifecycle = groupEnclosuresByLifecycle(activeEnclosureList);
  const rows: OperationRow[] = [];

  for (const doc of input.docs) {
    const enclosure = enclosureForDoc(doc, activeEnclosureList);
    if (!isRootTaskDoc(doc) && !enclosure) continue;
    const lifecycle = runtimeForDoc(doc, input.lifecycleById, enclosureList);
    if (lifecycle) representedLifecycleIds.add(lifecycle.id);
    rows.push(docRow(doc, lifecycle, input.series, docPaths));
  }

  for (const series of input.series) {
    if (docPaths.has(series.docPath)) continue;
    const lifecycle = runtimeForDoc(series, input.lifecycleById, enclosureList);
    if (lifecycle) representedLifecycleIds.add(lifecycle.id);
    rows.push(seriesRow(series, lifecycle));
  }

  for (const lifecycle of input.lifecycles) {
    if (representedLifecycleIds.has(lifecycle.id)) continue;
    const docs = docsByLifecycle.get(lifecycle.id) ?? [];
    const enclosure = findLifecycleEnclosure(
      lifecycle,
      activeEnclosures,
      activeEnclosuresByLifecycle,
    );
    if (!enclosure) continue;
    rows.push(lifecycleRow(lifecycle, docs, enclosure));
  }

  return rows.sort(compareRows);
}

function docRow(
  doc: TaskDocNode,
  lifecycle: LifecycleProjection | undefined,
  seriesList: SeriesNode[],
  masterDocPaths: Set<string>,
): OperationRow {
  const progress = doc.kind === "master" ? subTaskProgress(doc.subTasks) : topLevelStepProgress(doc);
  const label = taskDocHierarchyLabel(doc, seriesList);
  const repo = doc.repository || lifecycle?.repoId || "—";
  const phase = lifecycle?.phase ?? doc.status;
  const variant = lifecycle?.state ?? statusVariant(doc.status);
  const gate = gateHint(lifecycle?.gate?.kind, lifecycle?.ask);
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
    meta: rowMetaText(progressHint(progress), doc.status, lifecycle?.staleSeconds),
    gate,
    createdAt: doc.createdAt ?? "",
    fallbackOrder: doc.docPath,
    parentKey: taskDocParentKey(doc, seriesList, masterDocPaths),
    depth: 0,
    fleeting: lifecycle?.fleeting ?? false,
    inferred: lifecycle?.inferred ?? false,
  };
}

function seriesRow(series: SeriesNode, lifecycle: LifecycleProjection | undefined): OperationRow {
  const repo = series.repository || lifecycle?.repoId || "—";
  const phase = lifecycle?.phase ?? series.status;
  const variant = lifecycle?.state ?? statusVariant(series.status);
  const gate = gateHint(lifecycle?.gate?.kind, lifecycle?.ask);
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
      lifecycle?.staleSeconds,
    ),
    gate,
    createdAt: series.createdAt ?? "",
    fallbackOrder: series.docPath,
    depth: 0,
    fleeting: lifecycle?.fleeting ?? false,
    inferred: lifecycle?.inferred ?? false,
  };
}

function lifecycleRow(
  lifecycle: LifecycleProjection,
  docs: TaskDocNode[],
  enclosure: EnclosureNode | undefined,
): OperationRow {
  const label = taskLabel(lifecycle, docs, enclosure);
  const repo = lifecycle.repoId ?? "—";
  const gate = gateHint(lifecycle.gate?.kind, lifecycle.ask);
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
    meta: rowMetaText(taskHint(docs), "", lifecycle.staleSeconds),
    gate,
    createdAt: lifecycle.startedAt,
    fallbackOrder: lifecycle.id,
    depth: 0,
    fleeting: lifecycle.fleeting,
    inferred: lifecycle.inferred,
  };
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

function hierarchyRows(rows: OperationRow[]): OperationRow[] {
  const byParent = new Map<string, OperationRow[]>();
  const byKey = new Map(rows.map((item) => [item.key, item]));
  for (const item of rows) {
    if (!item.parentKey || !byKey.has(item.parentKey)) continue;
    const children = byParent.get(item.parentKey);
    if (children) children.push(item);
    else byParent.set(item.parentKey, [item]);
  }
  const roots = rows
    .filter((item) => !item.parentKey || !byKey.has(item.parentKey))
    .sort(compareRows);
  return roots.flatMap((root) => [
    { ...root, depth: 0 },
    ...(byParent.get(root.key) ?? []).sort(compareRows).map((child) => ({ ...child, depth: 1 })),
  ]);
}

function selectionKey(selection: ReturnType<typeof parseTaskSelection>): string | null {
  if (!selection) return null;
  if (selection.kind === "taskdoc") return taskDocSelectionKey(selection.docPath);
  if (selection.kind === "series") return seriesSelectionKey(selection.seriesId);
  return lifecycleSelectionKey(selection.lifecycleId);
}

function gateHint(kind: string | undefined, ask: Record<string, unknown> | undefined): string {
  if (kind) return kind;
  const question = ask?.question;
  if (typeof question === "string" && question.trim()) return question;
  return ask ? "ask" : "";
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
  doc: Pick<TaskDocNode, "docPath">,
  enclosures: EnclosureNode[],
): EnclosureNode | undefined {
  const dir = pathDir(doc.docPath);
  const stem = pathStem(doc.docPath);
  return enclosures.find((enclosure) => enclosure.taskRoot === dir && enclosure.leafId === stem);
}

function isActiveEnclosure(enclosure: Pick<EnclosureNode, "cleanup">): boolean {
  return enclosure.cleanup !== "completed";
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

function statusVariant(status: string): string {
  const normalized = status.toLowerCase();
  if (normalized === "completed") return "completed";
  if (normalized === "abandoned") return "abandoned";
  if (normalized === "blocked") return "blocked";
  if (normalized === "paused") return "paused";
  return "running";
}

function compareRows(left: OperationRow, right: OperationRow): number {
  const created = left.createdAt.localeCompare(right.createdAt);
  return created || left.fallbackOrder.localeCompare(right.fallbackOrder);
}

// Pure doc-selection helpers for the DetailPanel reader family: which doc a selection displays,
// how a series renders as a master view, and the small key/label utilities shared by the reader
// components. No JSX and no store access — every function is a pure mapping over projection data.
import {
  masterCommandNames,
  orderedByCreation,
  orchestratorParentKey,
  parentTaskLinkForDoc,
  pathDir,
  stripExt,
} from '../../data/taskHierarchy';
import { requirementAddressFromReference } from '../../data/requirements';
import {
  sameTaskDocumentRef,
  taskDocSelectionKey,
  taskDocumentRefForDoc,
  taskDocsForLifecycle,
  type TaskSelection,
} from '../../data/taskIdentity';
import type {
  LifecycleProjection,
  SeriesNode,
  SubTaskRow,
  TaskDecisionNode,
  TaskDocNode,
  TaskDocumentRef,
  TaskExecutionGraphView,
  TaskHeaderNoteNode,
  TaskReviewStateNode,
  TaskSectionNode,
  TaskStepNode,
} from '../../types/projection';

export const dirName = (docPath: string): string =>
  pathDir(docPath).split('/').filter(Boolean).pop() ?? '';

// Decisions newest-first by their recorded times (MIK-R126 rule 6 under the time-order
// ruling): writers stamp `at` as ISO-8601, the finalizer prepends its newest record, and
// storage order carries no invariant — so both the preview and the whole-log page order
// by parsed time. An unparseable stamp keeps its input position among its peers (stable),
// never inventing an order.
export function orderDecisionsNewestFirst<T extends Pick<TaskDecisionNode, 'at'>>(items: T[]): T[] {
  const stamped = items.map((item, index) => ({ item, index, time: Date.parse(item.at) }));
  stamped.sort((left, right) => {
    const leftBad = Number.isNaN(left.time);
    const rightBad = Number.isNaN(right.time);
    if (leftBad && rightBad) return left.index - right.index;
    if (leftBad) return 1;
    if (rightBad) return -1;
    if (right.time !== left.time) return right.time - left.time;
    return left.index - right.index;
  });
  return stamped.map((entry) => entry.item);
}

// The change-set bar shown on a task-document READER (master or leaf), with identity taken from
// the doc node — so it appears with NO active enclosure (previously the change-set buttons only
// lived on the live enclosure spine). A master gets the SERIES net button; a leaf gets COMMITTED
// (always — its landed delta) plus WORKING (only while its enclosure is live — the uncommitted
// delta). Liveness is read from the store here, so callers thread only `onOpen`.

export const sliceSlug = (doc: TaskDocNode): string => stripExt(doc.docPath.split('/').pop() ?? '');
export const sliceForSlug = (sliceDocs: TaskDocNode[], slug: string): TaskDocNode | undefined =>
  sliceDocs.find((doc) => sliceSlug(doc) === slug);
export const sliceForRef = (sliceDocs: TaskDocNode[], ref: SubTaskRow): TaskDocNode | undefined =>
  ref.file ? sliceForSlug(sliceDocs, stripExt(ref.file)) : undefined;
export const seriesSliceDocs = (sliceDocs: TaskDocNode[], seriesDocPath: string): TaskDocNode[] =>
  sliceDocs.filter((doc) => pathDir(doc.docPath) === pathDir(seriesDocPath));

// The docPath a typed `masterRef` (L14-R1) points at, resolved against the FULL projected pool —
// a sprint's commanded master lives in another folder, so `sliceDocs` can never answer this.
// Undefined when the target is not in the projected pool at all (another repo's docs, or a
// document the projection withheld), which is the caller's signal to fall back to the row's
// older behaviors. No document-count bound can hide a master any more (260916-TDPU removed it).
export const docPathForTaskRef = (docs: TaskDocNode[], ref: TaskDocumentRef): string | undefined =>
  docs.find((doc) => sameTaskDocumentRef(taskDocumentRefForDoc(doc), ref))?.docPath;

function seriesSliceDoc(
  allDocs: TaskDocNode[],
  seriesDocPath: string,
  openSlug: string | null,
): TaskDocNode | undefined {
  const seriesSlices = seriesSliceDocs(allDocs, seriesDocPath);
  return openSlug ? sliceForSlug(seriesSlices, openSlug) : undefined;
}

function overviewDocOrLeaf(
  docs: TaskDocNode[],
  masterPreferred: boolean,
  selectedSeriesDoc: TaskDocNode | undefined,
  master: TaskDocNode | undefined,
  selectedSeries: SeriesNode | undefined,
): TaskDocNode | undefined {
  if (masterPreferred) {
    if (selectedSeriesDoc) return selectedSeriesDoc;
    if (master) return master;
  } else if (selectedSeries || master) {
    return undefined; // a master / series overview shows no single leaf
  }
  const nonMaster = docs.filter((doc) => doc.kind !== 'master');
  return nonMaster.length === 1 ? nonMaster[0] : undefined;
}

// The live-lifecycle resolution shared by both reader variants: the enclosing master's slice
// set (or the non-master docs), an opened slug's slice, and the master/series overview doc.
function lifecycleReaderDoc(
  allDocs: TaskDocNode[],
  selectedTaskDoc: TaskDocNode | undefined,
  lifecycle: LifecycleProjection,
  selectedSeries: SeriesNode | undefined,
  openSlug: string | null,
  masterPreferred: boolean,
): TaskDocNode | undefined {
  const docs =
    selectedTaskDoc?.lifecycleId === lifecycle.id
      ? [selectedTaskDoc]
      : taskDocsForLifecycle(lifecycle, allDocs);
  const master = docs.find((doc) => doc.kind === 'master');
  const slices = master
    ? seriesSliceDocs(allDocs, master.docPath)
    : docs.filter((doc) => doc.kind !== 'master');
  const contentSlices = selectedSeries ? seriesSliceDocs(allDocs, selectedSeries.docPath) : slices;
  const openDoc = openSlug ? sliceForSlug(contentSlices, openSlug) : undefined;
  if (openDoc) return openDoc;
  const selectedSeriesDoc = selectedSeries
    ? allDocs.find((doc) => doc.docPath === selectedSeries.docPath)
    : undefined;
  return overviewDocOrLeaf(docs, masterPreferred, selectedSeriesDoc, master, selectedSeries);
}

export function displayedReaderDoc({
  allDocs,
  selectedTaskDoc,
  lifecycle,
  selectedSeries,
  openSlug,
}: {
  allDocs: TaskDocNode[];

  selectedTaskDoc: TaskDocNode | undefined;
  lifecycle: LifecycleProjection | undefined;
  selectedSeries: SeriesNode | undefined;
  openSlug: string | null;
}): TaskDocNode | undefined {
  if (selectedTaskDoc && !lifecycle) {
    if (selectedTaskDoc.kind === 'master') {
      const sliceDocs = seriesSliceDocs(allDocs, selectedTaskDoc.docPath);
      return openSlug ? sliceForSlug(sliceDocs, openSlug) : selectedTaskDoc;
    }
    return selectedTaskDoc;
  }
  if (!lifecycle && selectedSeries) {
    const seriesSlices = seriesSliceDocs(allDocs, selectedSeries.docPath);
    return openSlug
      ? sliceForSlug(seriesSlices, openSlug)
      : allDocs.find((doc) => doc.docPath === selectedSeries.docPath);
  }
  if (!lifecycle) return undefined;
  return lifecycleReaderDoc(allDocs, selectedTaskDoc, lifecycle, selectedSeries, openSlug, true);
}

// The leaf doc the panel renders a full reader for — the single source of the viewed-leaf key.
// Mirrors the DetailPanel render branches exactly: a drilled sub-task (openSlug), a directly
// opened leaf doc, or a lone slice; a master/series overview or the empty state yields undefined.
export function displayedLeafDoc({
  allDocs,
  selectedTaskDoc,
  lifecycle,
  selectedSeries,
  openSlug,
}: {
  selection: TaskSelection | null;
  allDocs: TaskDocNode[];
  selectedTaskDoc: TaskDocNode | undefined;
  lifecycle: LifecycleProjection | undefined;
  selectedSeries: SeriesNode | undefined;
  openSlug: string | null;
}): TaskDocNode | undefined {
  // Branch 1: a task-doc selection with no live lifecycle.
  if (selectedTaskDoc && !lifecycle) {
    if (selectedTaskDoc.kind === 'master') {
      return seriesSliceDoc(allDocs, selectedTaskDoc.docPath, openSlug);
    }
    return selectedTaskDoc;
  }
  // Branch 2: nothing resolved -> empty state.
  if (!lifecycle && !selectedSeries) return undefined;
  // Branch 3: a master-less series selection.
  if (!lifecycle && selectedSeries) {
    return seriesSliceDoc(allDocs, selectedSeries.docPath, openSlug);
  }
  // Branch 4: a live lifecycle.
  if (!lifecycle) return undefined;
  return lifecycleReaderDoc(allDocs, selectedTaskDoc, lifecycle, selectedSeries, openSlug, false);
}

export const taskStepProgress = (doc: TaskDocNode): { done: number; total: number } => ({
  done: doc.stepsDone,
  total: doc.stepsTotal,
});

// A master doc OR a series rendered as one. `subTasks` is widened to the union because the two
// sources send genuinely different rows: a task-doc master sends `TaskSubTaskRefNode` (may carry a
// cross-series `linkedLifecycleId`), a series sends `SeriesSubTaskNode` (carries `createdAt`, never
// a cross-link). Picking `TaskDocNode["subTasks"]` for both is what hid that difference.

export type MasterDocView = Pick<
  TaskDocNode,
  | 'id'
  | 'kind'
  | 'title'
  | 'status'
  | 'objective'
  | 'sections'
  | 'decisions'
  | 'references'
  | 'discardedCount'
  | 'discardedSubTasks'
  | 'masterLifecycleId'
  | 'executionGraphView'
  | 'requirements'
  | 'codeExamples'
  | 'openQuestions'
  | 'statusNote'
  | 'statusNoteTruncated'
  | 'headerNotes'
  | 'headerNotesTotal'
  | 'reviewState'
  | 'design'
  | 'nonGoals'
  | 'acceptance'
  | 'completedAt'
  | 'waitsFor'
  | 'type'
  | 'form'
  | 'executionNature'
  | 'integrationBranch'
  | 'createdAt'
  | 'orchestrates'
  | 'docPath'
  | 'repository'
> & { subTasks: SubTaskRow[] };

// What makes two selections the same document: its file, or repository and id
// where a series stands in for a document that has no file.
export const docIdentity = (doc: Pick<TaskDocNode, 'docPath' | 'repository' | 'id'>): string =>
  doc.docPath || `${doc.repository}:${doc.id}`;

export const seriesAsMasterDoc = (seriesNode: SeriesNode): MasterDocView => ({
  kind: 'master',
  id: seriesNode.seriesId,
  title: seriesNode.title,
  status: seriesNode.status,
  createdAt: seriesNode.createdAt,
  orchestrates: [],
  objective: seriesNode.objective,
  // Creation order belongs HERE, on the only source whose rows actually carry `createdAt`.
  // `SubTaskIndex` used to run this over its union of row types, where it could never do
  // anything for a task-doc master's rows (they have no such field). Kept as a safety net
  // even though `snapshots.py::_series_subtask_nodes` already sorts these server-side.
  subTasks: orderedByCreation(seriesNode.subTasks),
  sections: seriesNode.sections,
  decisions: seriesNode.decisions,
  // A series without a task document models no references; the master page then shows none.
  references: [],
  // A series without a task document models none of these either: the skeleton draws
  // what the series carries and leaves the rest absent (MIK-R127 rule 2/4/6).
  requirements: [],
  codeExamples: [],
  openQuestions: [],
  statusNote: undefined,
  statusNoteTruncated: false,
  headerNotes: [],
  headerNotesTotal: 0,
  reviewState: undefined,
  design: undefined,
  type: '',
  form: 'master',
  executionNature: undefined,
  integrationBranch: undefined,
  discardedCount: seriesNode.discardedCount,
  discardedSubTasks: seriesNode.discardedSubTasks,
  docPath: seriesNode.docPath,
  repository: seriesNode.repository,
});

// The lifecycle's bound task documents. A `master` (contract-paired, no lifecycleId of its
// own) shows its overview + a clickable sub-task index; clicking a slice drills into its full
// reader with a breadcrumb back. A master-less series lists clickable slices; a lone doc reads
// directly. Sub-tasks never enter the sidebar — they are reached here, from the series.

export function subTaskKey(ref: SubTaskRow, index: number): string {
  return `${ref.file || ref.name}:${index}`;
}

// Fallback for a series with no master yet: the slice list, now clickable into each reader.

export function labelWithId(id: string, title: string): string {
  return id ? `${id} — ${title}` : title;
}

// ---------------------------------------------------------------------------
// MIK-R127 fixed skeletons: one skeleton per form (rule 1), bounds with views
// (rule 6), further notes (rule 7), nothing twice (rule 8). All pure mappings
// over projection data; views and navigation stay in the reader components.
// ---------------------------------------------------------------------------

export type DocForm = 'sprint' | 'master' | 'task';

// The page form of one document (rule 1): the projection's own word when it
// carries one, else derived by the model's rule so an older cache still draws
// (a master commanding masters is a sprint; any other master is a master).
export function docForm(
  doc: Pick<TaskDocNode, 'kind' | 'orchestrates'> & { form?: string },
): DocForm {
  if (doc.form === 'sprint' || doc.form === 'master' || doc.form === 'task') return doc.form;
  if (doc.kind !== 'master') return 'task';
  return doc.orchestrates.length > 0 ? 'sprint' : 'master';
}

export type StatusGroup = 'work' | 'waiting' | 'done' | 'gone';

// One sub-task index row grouped by its declared status. Unknown statuses join
// "waiting": every row stays listed, never hidden and never an error.
export function groupSubTaskRows(rows: SubTaskRow[]): Record<StatusGroup, SubTaskRow[]> {
  const groups: Record<StatusGroup, SubTaskRow[]> = { work: [], waiting: [], done: [], gone: [] };
  for (const row of rows) {
    if (row.status === 'inProgress') groups.work.push(row);
    else if (row.status === 'Completed') groups.done.push(row);
    else if (row.status === 'abandoned') groups.gone.push(row);
    else groups.waiting.push(row);
  }
  return groups;
}

// Whether an index block has anything to stand on: a row in any group, the
// discarded-before-start audit, or introducing prose. A bare page keeps no
// empty Now/Tasks/Masters heading.
export function hasIndexContent(
  groups: { work: unknown[]; waiting: unknown[]; done: unknown[]; gone: unknown[] },
  discarded: number,
  intro: string | undefined,
): boolean {
  return (
    groups.work.length > 0 ||
    groups.waiting.length > 0 ||
    groups.done.length > 0 ||
    groups.gone.length > 0 ||
    discarded > 0 ||
    !!intro
  );
}

export function goneTasksLabel(gone: number, discarded: number): string {
  if (gone > 0 && discarded > 0) return `${gone} abandoned, ${discarded} discarded before start`;
  if (gone > 0) return `${gone} abandoned ${gone === 1 ? 'task' : 'tasks'}`;
  return `${discarded} discarded before start`;
}

export function goneMastersLabel(gone: number, discarded: number): string {
  const base = `${gone} abandoned ${gone === 1 ? 'master' : 'masters'}`;
  return discarded > 0 ? `${base}, ${discarded} discarded before start` : base;
}

// A commanded master's completed/total task counts from its own index rows.
// Abandoned rows are neither done nor part of the total (the series rule).
export function masterTaskCounts(master: Pick<TaskDocNode, 'subTasks'>): {
  done: number;
  total: number;
} {
  const live = master.subTasks.filter((row) => row.status !== 'abandoned');
  return {
    done: live.filter((row) => row.status === 'Completed').length,
    total: live.length,
  };
}

// The first step that is not done (rule 5 marks it); undefined when all are done.
export function firstOpenStepId(steps: Pick<TaskDocNode, 'steps'>['steps']): string | undefined {
  return steps.find((step) => step.status !== 'done')?.id;
}

// The review line of a Now row or Review block (rules 4-5): the round and the
// open findings, or null where the document records no review. The open count is
// the projection's count of the whole round: a summary carries only the first
// findings, so the open flags it holds are not the round's.
export function reviewSummary(review: TaskReviewStateNode | undefined): string | null {
  if (!review) return null;
  const findings =
    review.totalFindings > 0
      ? `, ${review.openFindings} of ${review.totalFindings} findings open`
      : '';
  return `round ${review.round}${findings}`;
}

// The intro prose of a structured (`subTasks` / `sharedDecisions`) section: it
// stands with the block it introduces (rule 7), never as a second list.
export function sectionIntro(
  sections: TaskSectionNode[],
  kind: 'subTasks' | 'sharedDecisions',
): string | undefined {
  const body = sections.find((section) => section.kind === kind)?.body;
  return body ? body : undefined;
}

export interface RegisterTable {
  heading: string;
  cols: string[];
  rows: string[][];
}

const TABLE_SEPARATOR = /^\|\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/;

function tableCells(line: string): string[] {
  return line
    .trim()
    .replace(/^\||\|$/g, '')
    .split('|')
    .map((cell) => cell.trim());
}

// The first markdown table of a section body in written order, or null. A table
// is a run of `|` lines; the second line may be the `---` separator.
export function parseMarkdownTable(body: string): { cols: string[]; rows: string[][] } | null {
  const lines = body.replace(/\r/g, '').split('\n');
  for (let start = 0; start < lines.length; start += 1) {
    if (!lines[start].trim().startsWith('|')) continue;
    const run: string[] = [];
    for (let index = start; index < lines.length; index += 1) {
      if (!lines[index].trim().startsWith('|')) break;
      run.push(lines[index]);
    }
    if (run.length === 0) continue;
    const cols = tableCells(run[0]);
    const rest = run.slice(1);
    const data = rest.length > 0 && TABLE_SEPARATOR.test(rest[0].trim()) ? rest.slice(1) : rest;
    if (cols.length === 0 || data.length === 0) {
      start += run.length - 1;
      continue;
    }
    return { cols, rows: data.map(tableCells) };
  }
  return null;
}

// A section is a register when its heading names it one and its body parses a
// table with at least one data row (rule 3: the Judgment and Priority registers;
// a "register" section of prose stays a Further note). Canonical registers
// stand in fixed slots — judgment first, priority second — independent of
// author section order (the proposal's sprint register order); any other
// recognized register follows in written order.
function registerSlot(heading: string): number {
  if (/judgment/i.test(heading)) return 0;
  if (/priority/i.test(heading)) return 1;
  return 2;
}

export function registerTables(sections: TaskSectionNode[]): RegisterTable[] {
  const tables: RegisterTable[] = [];
  for (const section of sections) {
    if (!/register/i.test(section.heading)) continue;
    const table = parseMarkdownTable(section.body);
    if (table) tables.push({ heading: section.heading, cols: table.cols, rows: table.rows });
  }
  return tables
    .map((table, index) => ({ table, index }))
    .sort(
      (left, right) =>
        registerSlot(left.table.heading) - registerSlot(right.table.heading) ||
        left.index - right.index,
    )
    .map((entry) => entry.table);
}

export interface FurtherSection {
  heading: string;
  body: string;
  chars: number;
  tables: number;
  rows: number;
}

function countBodyTables(body: string): { tables: number; rows: number } {
  const lines = body.replace(/\r/g, '').split('\n');
  let tables = 0;
  let rows = 0;
  for (let index = 0; index < lines.length; index += 1) {
    if (!lines[index].trim().startsWith('|')) continue;
    let run = 0;
    while (index < lines.length && lines[index].trim().startsWith('|')) {
      run += 1;
      index += 1;
    }
    tables += 1;
    rows += Math.max(
      0,
      run - (run > 1 && TABLE_SEPARATOR.test(lines[index - run + 1].trim()) ? 2 : 1),
    );
  }
  return { tables, rows };
}

// Every free-form section that is not one of the blocks above (rule 7): neither
// a structured section (its intro stands with the block), nor a register (drawn
// as a table), nor a byte-for-byte repeat of the objective (rule 8: not twice).
export function furtherSections(
  doc: Pick<TaskDocNode, 'objective' | 'sections'>,
  registers: RegisterTable[],
): FurtherSection[] {
  const registerHeadings = new Set(registers.map((register) => register.heading));
  return doc.sections
    .filter((section) => section.kind === 'freeform')
    .filter((section) => !registerHeadings.has(section.heading))
    .filter((section) => section.body !== doc.objective)
    .map((section) => ({
      heading: section.heading,
      body: section.body,
      chars: section.body.length,
      ...countBodyTables(section.body),
    }));
}

// The predecessor titles of one commanded master from the sprint graph view:
// what the master waits for on the Waiting rows. Empty where the graph names none.
export function masterPredecessorTitles(
  masterRef: TaskDocumentRef,
  view: TaskExecutionGraphView | undefined,
  allDocs: TaskDocNode[],
): string[] {
  if (!view) return [];
  const titles: string[] = [];
  for (const node of view.nodes) {
    const nodeDoc = allDocs.find((doc) =>
      sameTaskDocumentRef(taskDocumentRefForDoc(doc), node.masterRef),
    );
    if (!nodeDoc || !sameTaskDocumentRef(taskDocumentRefForDoc(nodeDoc), masterRef)) continue;
    for (const predecessor of node.predecessors) titles.push(predecessor.predecessorTitle);
  }
  return [...new Set(titles)];
}

// Which status the sprint page shows for one index row (open truth gap 3): the
// commanded master's own document where it resolves, else the row as written.
// The rows view always shows both side by side.
export function sprintRowStatus(row: SubTaskRow, masterDoc: TaskDocNode | undefined): string {
  return masterDoc?.status ?? row.status;
}

export interface ChainLink {
  /** Which form the parent has: the word that stands before its link. */
  form: 'sprint' | 'master';
  /** What the link shows: the parent's document id, or the series title where no document resolves. */
  text: string;
  title: string;
  targetKey: string;
}

// The chain of parents for the head strip (rule 2): sprint → master for a
// task, the commanding sprint for a master, nothing for a sprint. Each link
// jumps where the existing crumbs jump; an unresolvable parent is absent,
// never an error.
export function docChain(
  doc: Pick<TaskDocNode, 'kind' | 'docPath' | 'id' | 'title' | 'orchestrates'>,
  allDocs: TaskDocNode[],
  seriesList: SeriesNode[],
): ChainLink[] {
  if (doc.kind === 'master') {
    const key = orchestratorParentKey(masterCommandNames(doc), allDocs, doc.docPath);
    if (!key) return [];
    const sprint = allDocs.find((item) => taskDocSelectionKey(item.docPath) === key);
    return sprint ? [{ form: 'sprint', text: sprint.id, title: sprint.title, targetKey: key }] : [];
  }
  const master = allDocs.find((item) => item.docPath === `${pathDir(doc.docPath)}/task.json`);
  if (master) {
    return [
      ...docChain(master, allDocs, seriesList),
      {
        form: 'master',
        text: master.id,
        title: master.title,
        targetKey: taskDocSelectionKey(master.docPath),
      },
    ];
  }
  const parent = parentTaskLinkForDoc(doc, allDocs, seriesList);
  return parent
    ? [{ form: 'master', text: parent.title, title: parent.title, targetKey: parent.targetKey }]
    : [];
}

// A document's id as its parent's page shows it: the part after the parent's
// own id ("260928-MIK-L127" under "260928-MIK" reads "L127"). An id that does
// not begin with the parent's stays whole.
export function shortDocId(id: string, parentId: string | undefined): string {
  if (parentId && id.startsWith(`${parentId}-`) && id.length > parentId.length + 1) {
    return id.slice(parentId.length + 1);
  }
  return id;
}

export interface PrimaryRequirement {
  /** The requirement's id and version, e.g. "MIK-R127@v1". */
  label: string;
  /** The address of its packet inside the task folder, where a line names one. */
  address?: string;
  /** The line it was read from, for the reader who points at the link. */
  text: string;
}

const LEADING_LINK = /^\[([^\]]+)\]\(([^)\s]+)\)/;
const REQUIREMENT_ID = /\b[A-Z][A-Z0-9]*(?:-[A-Z0-9]+)*@v\d+\b/;

// The primary requirement of a document for the head strip (rule 2): its id as
// a link to its packet, never its text. The header note that names it goes
// first; a task without that note is read from its first requirement line. A
// line that names no requirement id gives no fact: its text stands once, under
// Requirements.
export function primaryRequirement(
  doc: Pick<TaskDocNode, 'requirements'> & {
    headerNotes?: TaskHeaderNoteNode[];
    form?: string;
    kind: string;
    orchestrates: string[];
  },
): PrimaryRequirement | undefined {
  const note = (doc.headerNotes ?? []).find((entry) => entry.label === 'Primary requirement');
  const first = doc.requirements[0];
  const source = note?.value ?? (docForm(doc) === 'task' ? first : undefined);
  if (!source) return undefined;
  const text = first ?? source;
  const linked = LEADING_LINK.exec(source.trim());
  if (linked) return { label: linked[1], address: linked[2], text };
  const id = REQUIREMENT_ID.exec(source)?.[0];
  if (!id) return undefined;
  const address = doc.requirements
    .map((line) => requirementAddressFromReference(line))
    .find((found) => found !== undefined);
  return { label: id, address, text };
}

// Long prose (rule 6): more than eight lines, or more characters than a screen
// holds — a single paragraph can exceed any viewport without one newline.
export const LONG_TEXT_LINES = 8;
export const LONG_TEXT_CHARS = 1000;

export interface BoundedText {
  long: boolean;
  /** The whole text where it is short, else its first lines cut at a word. */
  preview: string;
  /** What the link to the whole text names: "all 12 lines" or "21,021 characters". */
  extent: string;
}

export function boundedText(text: string): BoundedText {
  const lines = text.split('\n');
  const long = lines.length > LONG_TEXT_LINES || text.length > LONG_TEXT_CHARS;
  if (!long) return { long, preview: text, extent: '' };
  let preview = lines.slice(0, LONG_TEXT_LINES).join('\n');
  if (preview.length > LONG_TEXT_CHARS) {
    const cut = preview.slice(0, LONG_TEXT_CHARS);
    const space = cut.lastIndexOf(' ');
    preview = `${cut.slice(0, space > 0 ? space : LONG_TEXT_CHARS)} …`;
  }
  const extent =
    lines.length > LONG_TEXT_LINES ? `all ${lines.length} lines` : `${text.length} characters`;
  return { long, preview, extent };
}

export type StepDisplayState = 'done' | 'current' | 'blocked' | 'next' | 'pending';

// The state a step shows (rule 5): done, in work, blocked, the first one that
// is not done ("next"), or pending.
export function stepDisplayState(
  step: Pick<TaskStepNode, 'id' | 'status'> & Partial<Pick<TaskStepNode, 'substeps'>>,
  firstOpen: string | undefined,
): StepDisplayState {
  if (step.status === 'done') return 'done';
  if (
    step.status === 'inProgress' ||
    step.substeps?.some((item) => item.status === 'inProgress' || item.status === 'done')
  )
    return 'current';
  if (step.status === 'blocked') return 'blocked';
  if (step.id === firstOpen) return 'next';
  return 'pending';
}

// The state a nested step shows: it is never the page's "next" step.
export function substepDisplayState(status: string): StepDisplayState {
  if (status === 'done') return 'done';
  if (status === 'inProgress') return 'current';
  return status === 'blocked' ? 'blocked' : 'pending';
}

// MIK-R128: one mark per major step, matching the producer's major-only count.
export function stepMarkStates(steps: TaskStepNode[]): StepDisplayState[] {
  const firstOpen = firstOpenStepId(steps);
  return steps.map((step) => stepDisplayState(step, firstOpen));
}

// The step a task in work stands at (rule 4): the one in progress, else the
// first that is not done. Undefined where every step is done or none exists.
export function standingStep(
  steps: Array<Pick<TaskStepNode, 'id' | 'status' | 'title'>>,
): { how: 'current' | 'next'; id: string; title: string } | undefined {
  const current = steps.find((step) => step.status === 'inProgress');
  if (current) return { how: 'current', id: current.id, title: current.title };
  const next = steps.find((step) => step.status !== 'done');
  return next ? { how: 'next', id: next.id, title: next.title } : undefined;
}

// A recorded time as the page prints it: day and minute as written, never
// moved into another zone. A stamp that is no ISO time stays as it is.
export function shortWhen(at: string): string {
  return /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}/.test(at) ? `${at.slice(0, 10)} ${at.slice(11, 16)}` : at;
}

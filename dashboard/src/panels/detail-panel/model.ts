// Pure doc-selection helpers for the DetailPanel reader family: which doc a selection displays,
// how a series renders as a master view, and the small key/label utilities shared by the reader
// components. No JSX and no store access — every function is a pure mapping over projection data.
import {
  orderedByCreation,
  pathDir,
  stripExt,
} from "../../data/taskHierarchy";
import {
  taskDocsForLifecycle,
  type TaskSelection,
} from "../../data/taskIdentity";
import type {
  LifecycleProjection,
  SeriesNode,
  SubTaskRow,
  TaskDocNode,
} from "../../types/projection";

export const dirName = (docPath: string): string => pathDir(docPath).split("/").filter(Boolean).pop() ?? "";

// The change-set bar shown on a task-document READER (master or leaf), with identity taken from
// the doc node — so it appears with NO active enclosure (previously the change-set buttons only
// lived on the live enclosure spine). A master gets the SERIES net button; a leaf gets COMMITTED
// (always — its landed delta) plus WORKING (only while its enclosure is live — the uncommitted
// delta). Liveness is read from the store here, so callers thread only `onOpen`.

export const sliceSlug = (doc: TaskDocNode): string => stripExt(doc.docPath.split("/").pop() ?? "");
export const sliceForSlug = (sliceDocs: TaskDocNode[], slug: string): TaskDocNode | undefined =>
  sliceDocs.find((doc) => sliceSlug(doc) === slug);
export const sliceForRef = (sliceDocs: TaskDocNode[], ref: SubTaskRow): TaskDocNode | undefined =>
  ref.file ? sliceForSlug(sliceDocs, stripExt(ref.file)) : undefined;
export const seriesSliceDocs = (sliceDocs: TaskDocNode[], seriesDocPath: string): TaskDocNode[] =>
  sliceDocs.filter((doc) => pathDir(doc.docPath) === pathDir(seriesDocPath));

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
  const nonMaster = docs.filter((doc) => doc.kind !== "master");
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
  const master = docs.find((doc) => doc.kind === "master");
  const slices = master
    ? seriesSliceDocs(allDocs, master.docPath)
    : docs.filter((doc) => doc.kind !== "master");
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
    if (selectedTaskDoc.kind === "master") {
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
    if (selectedTaskDoc.kind === "master") {
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
  | "kind"
  | "title"
  | "status"
  | "objective"
  | "sections"
  | "decisions"
  | "masterLifecycleId"

  | "docPath"
  | "repository"
> & { subTasks: SubTaskRow[]; seriesTokenTotal?: number };


export const seriesAsMasterDoc = (seriesNode: SeriesNode): MasterDocView => ({
  kind: "master",
  title: seriesNode.title,
  status: seriesNode.status,
  objective: seriesNode.objective,
  // Creation order belongs HERE, on the only source whose rows actually carry `createdAt`.
  // `SubTaskIndex` used to run this over its union of row types, where it could never do
  // anything for a task-doc master's rows (they have no such field). Kept as a safety net
  // even though `snapshots.py::_series_subtask_nodes` already sorts these server-side.
  subTasks: orderedByCreation(seriesNode.subTasks),
  sections: seriesNode.sections,
  decisions: seriesNode.decisions,
  docPath: seriesNode.docPath,
  repository: seriesNode.repository,
  seriesTokenTotal: seriesNode.seriesTokenTotal,
});


export const masterDocWithSeriesTokens = (doc: TaskDocNode, seriesList: SeriesNode[]): MasterDocView => ({
  ...doc,
  seriesTokenTotal: seriesList.find((seriesNode) => seriesNode.docPath === doc.docPath)?.seriesTokenTotal,
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

import type { SeriesNode, TaskDocNode, TaskSubTaskRefNode } from "../types/projection";
import { seriesSelectionKey, taskDocSelectionKey } from "./taskIdentity";

export interface ParentTaskMatch {
  series: SeriesNode;
  ref: TaskSubTaskRefNode;
  number: string;
}

export interface ParentTaskLink {
  title: string;
  targetKey: string;
}

export function findParentTaskMatch(
  doc: Pick<TaskDocNode, "kind" | "docPath"> & Partial<Pick<TaskDocNode, "id">>,
  seriesList: SeriesNode[],
): ParentTaskMatch | undefined {
  if (doc.kind === "master") return undefined;
  for (const series of seriesList) {
    const orderedRefs = orderedByCreation(series.subTasks);
    const index = orderedRefs.findIndex((ref) => refMatchesDoc(series.docPath, ref, doc.docPath));
    if (index >= 0) {
      return { series, ref: orderedRefs[index], number: doc.id || orderedRefs[index].number };
    }
  }
  return undefined;
}

export function taskDocHierarchyLabel(doc: TaskDocNode, seriesList: SeriesNode[]): string {
  const match = findParentTaskMatch(doc, seriesList);
  return match ? `${match.number}. ${doc.title}` : doc.title;
}

export function taskDocParentKey(
  doc: Pick<TaskDocNode, "kind" | "docPath">,
  seriesList: SeriesNode[],
  masterDocPaths: Set<string>,
): string | undefined {
  const match = findParentTaskMatch(doc, seriesList);
  if (!match) return undefined;
  return parentSelectionKey(match.series, masterDocPaths);
}

export function parentTaskLinkForDoc(
  doc: Pick<TaskDocNode, "kind" | "docPath">,
  allDocs: TaskDocNode[],
  seriesList: SeriesNode[],
): ParentTaskLink | undefined {
  const match = findParentTaskMatch(doc, seriesList);
  if (!match) return undefined;
  const masterDocPaths = new Set(
    allDocs.filter((item) => item.kind === "master").map((item) => item.docPath),
  );
  return {
    title: match.series.title,
    targetKey: parentSelectionKey(match.series, masterDocPaths),
  };
}

// --- the orchestration-command relation (L14) --------------------------------------------------
// An ORCHESTRATION TASK is a `kind:"master"` doc carrying a non-empty top-level `orchestrates`
// list — the master task NAMES it commands (owner data-model ruling; no new task kind). A master
// is "commanded" when any orchestration doc's list contains one of the names it answers to: its
// task folder (the durable series key), its doc id, or its title — forgiving but exact-string.
// Masters named nowhere stay top-level exactly as today (D3: flat runs stay flat).

export function isOrchestrationDoc(
  doc: Pick<TaskDocNode, "kind" | "orchestrates">,
): boolean {
  return doc.kind === "master" && (doc.orchestrates?.length ?? 0) > 0;
}

/** The names a master answers to when matched against an `orchestrates` list. */
export function masterCommandNames(
  doc: Pick<TaskDocNode, "docPath" | "id" | "title">,
): string[] {
  const folder = pathDir(doc.docPath).split("/").filter(Boolean).pop() ?? "";
  return [folder, doc.id, doc.title].filter(Boolean);
}

/**
 * The selection key of the orchestration doc commanding a master that answers to `names`,
 * or undefined when no orchestration task names it. A doc never commands itself.
 */
export function orchestratorParentKey(
  names: string[],
  docs: TaskDocNode[],
  selfDocPath?: string,
): string | undefined {
  const nameSet = new Set(names);
  const commander = docs.find(
    (doc) =>
      isOrchestrationDoc(doc) &&
      doc.docPath !== selfDocPath &&
      (doc.orchestrates ?? []).some((name) => nameSet.has(name)),
  );
  return commander ? taskDocSelectionKey(commander.docPath) : undefined;
}

export function pathDir(path: string): string {
  return path.split("/").slice(0, -1).join("/");
}

export function pathStem(path: string): string {
  return stripExt(path.split("/").pop() ?? "");
}

export function stripExt(name: string): string {
  return name.replace(/\.(md|json)$/i, "");
}

function orderedByCreation<T extends { createdAt?: string }>(items: T[]): T[] {
  if (!items.every((item) => item.createdAt)) return items;
  return [...items].sort((left, right) =>
    (left.createdAt as string).localeCompare(right.createdAt as string),
  );
}

function refMatchesDoc(seriesDocPath: string, ref: TaskSubTaskRefNode, docPath: string): boolean {
  if (!ref.file) return false;
  return stripExt(normalizePath(`${pathDir(seriesDocPath)}/${ref.file}`)) === stripExt(normalizePath(docPath));
}

function parentSelectionKey(series: SeriesNode, masterDocPaths: Set<string>): string {
  return masterDocPaths.has(series.docPath)
    ? taskDocSelectionKey(series.docPath)
    : seriesSelectionKey(series.seriesId);
}

function normalizePath(path: string): string {
  const absolute = path.startsWith("/");
  const parts: string[] = [];
  for (const part of path.split("/")) {
    if (!part || part === ".") continue;
    if (part === "..") parts.pop();
    else parts.push(part);
  }
  return `${absolute ? "/" : ""}${parts.join("/")}`;
}

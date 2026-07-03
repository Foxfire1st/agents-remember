import type {
  Analytics,
  EnclosureNode,
  LifecycleProjection,
  TaskDocNode,
} from "../types/projection";

export type TaskSelection =
  | { kind: "taskdoc"; docPath: string }
  | { kind: "series"; seriesId: string }
  | { kind: "lifecycle"; lifecycleId: string };

const TASKDOC_PREFIX = "taskdoc:";
const SERIES_PREFIX = "series:";
const LIFECYCLE_PREFIX = "lifecycle:";

export const taskDocSelectionKey = (docPath: string): string => `${TASKDOC_PREFIX}${docPath}`;
export const seriesSelectionKey = (seriesId: string): string => `${SERIES_PREFIX}${seriesId}`;
export const lifecycleSelectionKey = (lifecycleId: string): string =>
  `${LIFECYCLE_PREFIX}${lifecycleId}`;

export function parseTaskSelection(
  selectedId: string | null,
  lifecycles: Record<string, LifecycleProjection>,
  analytics: Analytics | null | undefined,
): TaskSelection | null {
  if (!selectedId) return null;
  if (selectedId.startsWith(TASKDOC_PREFIX)) {
    return { kind: "taskdoc", docPath: selectedId.slice(TASKDOC_PREFIX.length) };
  }
  if (selectedId.startsWith(SERIES_PREFIX)) {
    return { kind: "series", seriesId: selectedId.slice(SERIES_PREFIX.length) };
  }
  if (selectedId.startsWith(LIFECYCLE_PREFIX)) {
    return { kind: "lifecycle", lifecycleId: selectedId.slice(LIFECYCLE_PREFIX.length) };
  }
  if (lifecycles[selectedId]) return { kind: "lifecycle", lifecycleId: selectedId };
  if (analytics?.series.some((series) => series.seriesId === selectedId)) {
    return { kind: "series", seriesId: selectedId };
  }
  if (analytics?.taskDocuments.some((doc) => doc.docPath === selectedId)) {
    return { kind: "taskdoc", docPath: selectedId };
  }
  return null;
}

export function lifecycleIdForSelection(
  selectedId: string | null,
  lifecycles: Record<string, LifecycleProjection>,
  analytics: Analytics | null | undefined,
): string | undefined {
  const selection = parseTaskSelection(selectedId, lifecycles, analytics);
  if (selection?.kind === "lifecycle") return selection.lifecycleId;
  if (selection?.kind === "taskdoc") {
    return analytics?.taskDocuments.find((doc) => doc.docPath === selection.docPath)?.lifecycleId;
  }
  return undefined;
}

// The durable QUALIFIED LEAF ID for a task doc: `repo/master/leaf-id`, where master is the basename
// of the doc's directory (the series/contract folder) and leaf-id is the doc id. This is the chat<->leaf
// registry key (L5). Derived from the doc, NOT the enclosure, so it resolves with no live worktree and
// after finalize. Returns undefined when any part is missing (a master-less / pathless doc).
export function qualifiedLeafKey(
  doc: Pick<TaskDocNode, "repository" | "docPath" | "id">,
): string | undefined {
  const master = doc.docPath.split("/").slice(0, -1).filter(Boolean).pop() ?? "";
  if (!doc.repository || !master || !doc.id) return undefined;
  return `${doc.repository}/${master}/${doc.id}`;
}

// The selected leaf's qualified key, from the OPEN TASK DOC (taskdoc or lifecycle selection). A series
// selection has no single leaf, so it resolves to undefined. Mirrors lifecycleIdForSelection.
export function leafKeyForSelection(
  selectedId: string | null,
  lifecycles: Record<string, LifecycleProjection>,
  analytics: Analytics | null | undefined,
): string | undefined {
  const selection = parseTaskSelection(selectedId, lifecycles, analytics);
  if (!selection) return undefined;
  const docs = analytics?.taskDocuments ?? [];
  if (selection.kind === "taskdoc") {
    const doc = docs.find((item) => item.docPath === selection.docPath);
    return doc ? qualifiedLeafKey(doc) : undefined;
  }
  if (selection.kind === "lifecycle") {
    const doc = docs.find((item) => item.lifecycleId === selection.lifecycleId);
    return doc ? qualifiedLeafKey(doc) : undefined;
  }
  return undefined;
}

// The display title for a bound leaf: the matching task doc's title, else undefined (the caller falls
// back to the leaf id). Used to render "who works on what" on the Chats page (L5 S4).
export function leafTitleForKey(
  taskDocuments: TaskDocNode[],
  leafKey: string,
): string | undefined {
  return taskDocuments.find((doc) => qualifiedLeafKey(doc) === leafKey)?.title;
}

// The leaf id (last segment) of a qualified leaf key — the name-label fallback when no doc title resolves.
export function leafIdFromKey(leafKey: string): string {
  return leafKey.split("/").filter(Boolean).pop() ?? leafKey;
}

// One node of the task hierarchy the leaf-attach picker drills: either a MASTER (has `master`, drillable,
// `children` are its sub-tasks) or an attachable LEAF (has `leafKey`, no children). Nesting is real — a
// master can itself be a sub-task of another master — so this is a recursive tree, not a flat group.
export interface TaskTreeNode {
  key: string; // unique among siblings: master folder for a master, leaf key for a leaf
  title: string;
  master?: string; // the master folder, set iff this is a master node (drill-in + contextMaster match)
  leafKey?: string; // the qualified leaf id, set iff this is an attachable leaf
  children: TaskTreeNode[];
}

const masterFolderOf = (doc: Pick<TaskDocNode, "docPath">): string =>
  doc.docPath.split("/").slice(0, -1).filter(Boolean).pop() ?? "";

// Build the recursive master→…→leaf tree the picker drills. Masters nest under their parent master via
// `masterLifecycleId` (the cross-series parent link); leaves attach to the master in their own folder (or,
// failing that, their `masterLifecycleId` parent). Top-level masters and any orphan leaves are the roots.
// This scales to arbitrary nesting — "a master that is the leaf of another master" is just a master node
// sitting inside another master node.
export function buildTaskTree(taskDocuments: TaskDocNode[]): TaskTreeNode[] {
  const masterByFolder = new Map<string, TaskTreeNode>();
  const masterByLifecycle = new Map<string, TaskTreeNode>();
  const masterDocByFolder = new Map<string, TaskDocNode>();
  for (const doc of taskDocuments) {
    if (doc.kind !== "master") continue;
    const folder = masterFolderOf(doc);
    if (!folder || masterByFolder.has(folder)) continue;
    const node: TaskTreeNode = { key: folder, title: doc.title || folder, master: folder, children: [] };
    masterByFolder.set(folder, node);
    masterDocByFolder.set(folder, doc);
    if (doc.lifecycleId) masterByLifecycle.set(doc.lifecycleId, node);
  }

  const nestedMasters = new Set<TaskTreeNode>();
  for (const [folder, node] of masterByFolder) {
    const parentLifecycle = masterDocByFolder.get(folder)?.masterLifecycleId;
    const parent = parentLifecycle ? masterByLifecycle.get(parentLifecycle) : undefined;
    if (parent && parent !== node) {
      parent.children.push(node);
      nestedMasters.add(node);
    }
  }

  const orphanLeaves: TaskTreeNode[] = [];
  for (const doc of taskDocuments) {
    if (doc.kind !== "subTask") continue;
    const leafKey = qualifiedLeafKey(doc);
    if (!leafKey) continue;
    const leaf: TaskTreeNode = { key: leafKey, title: doc.title || leafIdFromKey(leafKey), leafKey, children: [] };
    const parent =
      masterByFolder.get(masterFolderOf(doc)) ??
      (doc.masterLifecycleId ? masterByLifecycle.get(doc.masterLifecycleId) : undefined);
    if (parent) parent.children.push(leaf);
    else orphanLeaves.push(leaf);
  }

  const rootMasters = [...masterByFolder.values()].filter((node) => !nestedMasters.has(node));
  return [...rootMasters, ...orphanLeaves];
}

// The path of master nodes from a root down to the master whose folder is `master` (for pre-drilling the
// picker to the in-context task). Empty when not found. Only master nodes are traversed (leaves are leaves).
export function findMasterPath(nodes: TaskTreeNode[], master: string): TaskTreeNode[] {
  for (const node of nodes) {
    if (node.master === master) return [node];
    const sub = findMasterPath(node.children, master);
    if (sub.length) return [node, ...sub];
  }
  return [];
}

// The master folder of whatever task is currently selected (a master, one of its leaves, or a doc
// resolved from a lifecycle) — drives the picker's in-context ordering so the relevant master comes first.
export function masterFolderForSelection(
  selectedId: string | null,
  lifecycles: Record<string, LifecycleProjection>,
  analytics: Analytics | null | undefined,
): string | undefined {
  const selection = parseTaskSelection(selectedId, lifecycles, analytics);
  if (!selection) return undefined;
  const docs = analytics?.taskDocuments ?? [];
  let doc: TaskDocNode | undefined;
  if (selection.kind === "taskdoc") doc = docs.find((item) => item.docPath === selection.docPath);
  else if (selection.kind === "lifecycle") {
    doc = docs.find((item) => item.lifecycleId === selection.lifecycleId);
  }
  return doc ? masterFolderOf(doc) || undefined : undefined;
}

export function groupEnclosuresByLifecycle(enclosures: EnclosureNode[]): Map<string, EnclosureNode> {
  const byLifecycle = new Map<string, EnclosureNode>();
  for (const enclosure of enclosures) {
    if (enclosure.lifecycleId) byLifecycle.set(enclosure.lifecycleId, enclosure);
  }
  return byLifecycle;
}

export function findLifecycleEnclosure(
  lifecycle: LifecycleProjection,
  enclosures: Record<string, EnclosureNode>,
  enclosuresByLifecycle: Map<string, EnclosureNode>,
): EnclosureNode | undefined {
  if (lifecycle.enclosure && enclosures[lifecycle.enclosure]) return enclosures[lifecycle.enclosure];
  return enclosuresByLifecycle.get(lifecycle.id);
}

export function taskLabel(
  lifecycle: LifecycleProjection,
  directDocs: TaskDocNode[],
  enclosure: EnclosureNode | undefined,
): string {
  if (enclosure) {
    if (lifecycle.id === enclosure.taskId || lifecycle.id === enclosure.taskName || directDocs.length > 1) {
      return enclosure.taskName || enclosure.taskId || lifecycleLabelFallback(lifecycle.id);
    }
    return (
      enclosure.leafId ||
      enclosure.enclosureId ||
      enclosure.taskName ||
      lifecycleLabelFallback(lifecycle.id)
    );
  }
  return taskDocumentLabel(directDocs, lifecycleLabelFallback(lifecycle.id));
}

export function taskDocsForLifecycle(
  lifecycle: LifecycleProjection,
  allDocs: TaskDocNode[],
): TaskDocNode[] {
  return allDocs.filter((doc) => doc.lifecycleId === lifecycle.id);
}

export function taskDocumentLabel(directDocs: TaskDocNode[], fallback: string): string {
  const masterDoc = directDocs.find((doc) => doc.kind === "master");
  if (masterDoc?.title) return masterDoc.title;
  if (directDocs.length === 1 && directDocs[0].title) return directDocs[0].title;
  return fallback;
}

function lifecycleLabelFallback(id: string): string {
  return id.includes("/") ? id.slice(id.indexOf("/") + 1) : id;
}

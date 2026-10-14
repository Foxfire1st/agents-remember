import type {
  Analytics,
  EnclosureNode,
  LifecycleProjection,
  TaskDocNode,
  TaskDocumentRef,
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

/** Canonical repository-qualified document reference for a projected task node. */
export function taskDocumentRefForDoc(
  doc: Pick<TaskDocNode, "repository" | "docPath">,
): TaskDocumentRef | undefined {
  if (!doc.repository || !doc.docPath) return undefined;
  const normalized = doc.docPath.replaceAll("\\", "/");
  const marker = `/tasks/${doc.repository}/`;
  const markerAt = normalized.lastIndexOf(marker);
  if (markerAt < 0) return undefined;
  const path = normalized.slice(markerAt + marker.length);
  return path.endsWith(".json") ? { repository: doc.repository, path } : undefined;
}

export function sameTaskDocumentRef(
  left: TaskDocumentRef | null | undefined,
  right: TaskDocumentRef | null | undefined,
): boolean {
  return Boolean(
    left && right && left.repository === right.repository && left.path === right.path,
  );
}

// The selected leaf's qualified key, from the OPEN TASK DOC (taskdoc or lifecycle selection). A series
// selection has no single leaf, so it resolves to undefined.
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

const masterFolderOf = (doc: Pick<TaskDocNode, "docPath">): string =>
  doc.docPath.split("/").slice(0, -1).filter(Boolean).pop() ?? "";

// The master folder of whatever task is currently selected (a master, one of its leaves, or a doc
// resolved from a lifecycle).
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

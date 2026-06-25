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
  const masterDoc = directDocs.find((doc) => doc.kind === "master");
  if (masterDoc?.title) return masterDoc.title;
  if (directDocs.length === 1 && directDocs[0].title) return directDocs[0].title;
  return lifecycleLabelFallback(lifecycle.id);
}

export function taskDocsForLifecycle(
  lifecycle: LifecycleProjection,
  allDocs: TaskDocNode[],
): TaskDocNode[] {
  return allDocs.filter((doc) => doc.lifecycleId === lifecycle.id);
}

function lifecycleLabelFallback(id: string): string {
  return id.includes("/") ? id.slice(id.indexOf("/") + 1) : id;
}

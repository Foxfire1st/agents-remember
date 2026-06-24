import type { EnclosureNode, LifecycleProjection, TaskDocNode } from "../types/projection";

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

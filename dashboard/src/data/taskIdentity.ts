import type { TaskEntry, TaskDocumentRef } from '../types/projection';

/** Exact canonical reference equality; no path or id heuristic. */
export function sameTaskDocumentRef(
  left: TaskDocumentRef | null | undefined,
  right: TaskDocumentRef | null | undefined,
): boolean {
  return Boolean(left && right && left.repository === right.repository && left.path === right.path);
}

/** The role-chat leaf registry names the document's master folder and durable task id. */
export function qualifiedLeafKey(entry: TaskEntry): string | undefined {
  if (!entry.reference) return undefined;
  const master = entry.reference.path.split('/').slice(0, -1).pop();
  return master && entry.id ? `${entry.reference.repository}/${master}/${entry.id}` : undefined;
}

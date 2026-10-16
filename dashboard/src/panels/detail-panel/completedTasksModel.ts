// Completion facts come from the native producer; only table ordering is local.
import type { TaskEntry } from '../../types/projection';
import { shortDocId } from './model';

export type CompletedSort = { column: 'number' | 'time'; direction: 'asc' | 'desc' };
export const NEWEST_COMPLETED: CompletedSort = { column: 'time', direction: 'desc' };
export interface CompletedEntry {
  entry: TaskEntry;
  position: number;
  id: string;
  at?: string;
}

export function completedEntries(
  parent: TaskEntry,
  tasks: Record<string, TaskEntry>,
): CompletedEntry[] {
  return parent.children.flatMap((reference, index) => {
    const entry = tasks[reference];
    return entry?.stage === 'completed'
      ? [
          {
            entry,
            position: index + 1,
            id: shortDocId(entry.id, parent.id),
            at: entry.summary?.completedAt ?? undefined,
          },
        ]
      : [];
  });
}

export function orderCompleted(entries: CompletedEntry[], sort: CompletedSort): CompletedEntry[] {
  const direction = sort.direction === 'asc' ? 1 : -1;
  return [...entries].sort((a, b) => {
    if (sort.column === 'number')
      return direction * a.id.localeCompare(b.id, 'en', { numeric: true });
    const first = a.at ? Date.parse(a.at) : NaN;
    const second = b.at ? Date.parse(b.at) : NaN;
    if (!Number.isFinite(first)) return Number.isFinite(second) ? 1 : 0;
    if (!Number.isFinite(second)) return -1;
    return direction * (first - second);
  });
}

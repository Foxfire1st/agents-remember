// MIK-R128 rule 16: completion facts and ephemeral table ordering, never a migration.
import type { SubTaskRow, TaskDocNode } from '../../types/projection';
import { shortDocId } from './model';
import { indexSlices } from './indexRows';

export type CompletedSort = { column: 'number' | 'time'; direction: 'asc' | 'desc' };
export const NEWEST_COMPLETED: CompletedSort = { column: 'time', direction: 'desc' };
export interface CompletedEntry {
  ref: SubTaskRow;
  position: number;
  id: string;
  name: string;
  at?: string;
}

export function completionTime(doc: TaskDocNode | undefined): string | undefined {
  if (!doc) return undefined;
  if (doc.completedAt) return doc.completedAt;
  // Legacy documents are not rewritten. Only the finalizer's exact record supplies the time.
  return doc.decisions
    .filter((item) => item.decision === 'Finalize task lifecycle.')
    .map((item) => item.at)
    .filter((at) => Number.isFinite(Date.parse(at)))
    .sort((a, b) => Date.parse(b) - Date.parse(a))[0];
}

export function completedEntries(
  refs: SubTaskRow[],
  docs: TaskDocNode[],
  parentId: string,
  positions: Map<SubTaskRow, number>,
  matchFor: (ref: SubTaskRow) => TaskDocNode | undefined = indexSlices(docs),
): CompletedEntry[] {
  return refs.map((ref) => {
    const match = matchFor(ref);
    return {
      ref,
      position: positions.get(ref) ?? 0,
      id: shortDocId(match?.id || ref.number, parentId),
      name: match?.title || ref.name,
      at: completionTime(match),
    };
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

// Navigation follows the single served entry; membership and identity need no document join.
import { taskEntrySelectionKey } from '../../data/taskReference';
import type { TaskEntry } from '../../types/projection';
import { childProgress } from '../lifecycle-list/docProgress';
import { ProgressBar } from './pageParts';

export interface RowTarget {
  testid: string;
  run?: () => void;
  title?: string;
}
export function resolveRowTarget(
  entry: TaskEntry,
  position: number,
  onJump: (key: string) => void,
): RowTarget {
  return {
    testid: `subtask-open-${position}`,
    title: entry.summary ? entry.title : 'not authored as a task document yet',
    run: entry.reference ? () => onJump(taskEntrySelectionKey(entry.key)) : undefined,
  };
}
export interface TaskIndexProps {
  entry: TaskEntry;
  tasks: Record<string, TaskEntry>;
  onJump: (key: string) => void;
}

export const liveChildCount = (entry: TaskEntry): number => childProgress(entry).total;

export function ChildProgress({ entry }: { entry: TaskEntry }) {
  const total = liveChildCount(entry);
  return total > 0 ? (
    <>
      <ProgressBar done={entry.counts.completed} work={entry.counts.started} total={total} />
      {entry.counts.completed}/{total}
    </>
  ) : null;
}

// Numeric progress for a document row; the producer owns the major-step count.
import type { TaskDocNode } from '../../types/projection';
import { subTaskProgress, taskStepProgress } from './progress';
import type { RowProgressFacts } from './rowState';

export function docProgress(doc: TaskDocNode): {
  progress: RowProgressFacts;
  progressNoun: string;
} {
  if (doc.kind !== 'master') return { progress: taskStepProgress(doc), progressNoun: 'Steps done' };
  return {
    progress: { ...subTaskProgress(doc.subTasks), discarded: doc.discardedCount ?? 0 },
    progressNoun: 'Tasks completed',
  };
}

// Numeric progress reads the single task entry, including the producer's major-step count.
import type { TaskEntry } from '../../types/projection';

export function docProgress(entry: TaskEntry): {
  progress: { done: number; total: number; discarded?: number; abandoned?: number };
  progressNoun: string;
} {
  if (entry.summary?.form === 'task' || !entry.children.length)
    return {
      progress: { done: entry.stepsDone, total: entry.stepsTotal },
      progressNoun: 'Steps done',
    };
  return {
    progress: childProgress(entry),
    progressNoun: 'Tasks completed',
  };
}

export function childProgress(entry: TaskEntry) {
  return {
    done: entry.counts.completed,
    total: entry.counts.planned + entry.counts.started + entry.counts.completed,
    abandoned: entry.counts.aborted,
    discarded: entry.summary?.discardedCount ?? 0,
  };
}

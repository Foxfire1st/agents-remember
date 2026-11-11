import type { TaskCounts } from '../../types/projection';

/** A master's own activity: `N works · N waiting · N paused`, the waiting part only while one waits. */
export function activityPhrase(counts: Pick<TaskCounts, 'works' | 'waiting' | 'paused'>): string {
  const waiting = counts.waiting ? `${counts.waiting} waiting · ` : '';
  return `${counts.works} works · ${waiting}${counts.paused} paused`;
}

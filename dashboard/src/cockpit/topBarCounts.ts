import { statusLabel, type TaskStatusKey } from '../panels/detail-panel/pageParts';
import type { TaskCounts } from '../types/projection';

const ACTIVITY_KEYS: ReadonlySet<TaskStatusKey> = new Set(['works', 'waiting', 'paused']);

/** The top bar's task-count line, `N planned · N started · …`.
 *
 * With the host observation unknown, works, waiting and paused are not known: their counts are left
 * out and the line says the host activity is unavailable, the one place that says so. Otherwise a
 * waiting count shows only when a task waits, so the line keeps its form while none does.
 */
export function topBarCountsLine(taskCounts: TaskCounts, hostUnknown: boolean): string {
  const counts: Array<[TaskStatusKey, number]> = [
    ['planned', taskCounts.planned],
    ['started', taskCounts.started],
    ['completed', taskCounts.completed],
    ['aborted', taskCounts.aborted],
    ['works', taskCounts.works],
    ['waiting', taskCounts.waiting],
    ['paused', taskCounts.paused],
  ];
  const shown = hostUnknown
    ? counts.filter(([key]) => !ACTIVITY_KEYS.has(key))
    : counts.filter(([key, value]) => key !== 'waiting' || value > 0);
  return [
    ...shown.map(([key, value]) => `${value} ${statusLabel(key)}`),
    ...(hostUnknown ? ['host activity unavailable'] : []),
  ].join(' · ');
}

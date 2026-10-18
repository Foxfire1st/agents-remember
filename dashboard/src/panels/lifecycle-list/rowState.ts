import type { TaskEntry } from '../../types/projection';
import { statusLabel, taskStatusKey } from '../detail-panel/pageParts';
import { docProgress } from './docProgress';

/** Own work precedes waiting work; authored order breaks ties in each band. */
export function rowBand(entry: TaskEntry): number {
  if (entry.activity === 'works') return 0;
  if (entry.stage === 'started') return 1;
  return entry.stage === 'planned' ? 2 : 3;
}

/** The host time a row carries, or null when the host recorded none (MIK-R174 rule 2).
 * A missing time is never turned into 0 or into now: it stays missing and orders nothing. */
function hostTime(entry: TaskEntry): number | null {
  if (!entry.eventAt) return null;
  const at = Date.parse(entry.eventAt);
  return Number.isNaN(at) ? null : at;
}

/** Order inside one band (MIK-R174 rule 1): own work first, then the started rows the host
 * timed, newest first, then the started rows it did not time, in the server's own order.
 * Every other band keeps the server's order, and the sort is stable so ties do. */
export function rowOrder(left: TaskEntry, right: TaskEntry): number {
  const band = rowBand(left) - rowBand(right);
  if (band !== 0 || rowBand(left) !== 1) return band;
  const leftAt = hostTime(left);
  const rightAt = hostTime(right);
  if (leftAt === rightAt) return 0;
  if (leftAt === null) return 1;
  if (rightAt === null) return -1;
  return rightAt - leftAt;
}

export function shortTaskId(entry: TaskEntry, tasks: Record<string, TaskEntry>): string {
  if (entry.summary?.kind === 'master') return '';
  const parent = entry.parent ? tasks[entry.parent] : undefined;
  return parent && entry.id.startsWith(`${parent.id}-`)
    ? entry.id.slice(parent.id.length + 1)
    : entry.id;
}

export function progressFigure(entry: TaskEntry): string {
  const { progress } = docProgress(entry);
  return progress.total > 0 ? `${progress.done}/${progress.total}` : '';
}

export function rowHover(entry: TaskEntry): string {
  const { progress, progressNoun } = docProgress(entry);
  const extra = [
    progress.abandoned ? `${progress.abandoned} abandoned` : '',
    progress.discarded ? `${progress.discarded} discarded` : '',
  ].filter(Boolean);
  const figure =
    progress.total > 0 || extra.length
      ? [`${progressNoun}: ${progress.done} of ${progress.total}`, ...extra].join(', ')
      : '';
  return [
    entry.title,
    entry.summary ? '' : 'This task document is not authored yet.',
    `State: ${statusLabel(taskStatusKey(entry))}`,
    `Stage: ${entry.stage}`,
    entry.reference ? `Repo: ${entry.reference.repository}` : '',
    entry.summary?.currentStep ? `Current step: ${entry.summary.currentStep}` : '',
    figure,
    entry.eventAt ? `Host event: ${entry.eventAt}` : '',
  ]
    .filter(Boolean)
    .join('\n');
}

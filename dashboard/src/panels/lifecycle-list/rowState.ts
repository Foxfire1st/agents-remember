import type { TaskEntry } from '../../types/projection';
import { statusLabel, taskStatusKey } from '../detail-panel/pageParts';
import { docProgress } from './docProgress';

/** Own work precedes waiting work; authored order breaks ties in each band. */
export function rowBand(entry: TaskEntry): number {
  if (entry.activity === 'works') return 0;
  if (entry.stage === 'started') return 1;
  return entry.stage === 'planned' ? 2 : 3;
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

import { cleanup } from '@testing-library/react';
import { afterEach,beforeEach,vi } from 'vitest';
import { dashboardStore } from '../../data/store';
import { projection,taskEntry,taskSummary } from '../../test/fixtures/wire';
import type { TaskEntry } from '../../types/projection';

export { projection,taskEntry,taskSummary };
export function task(id: string, over: Partial<TaskEntry> = {}): TaskEntry {
  return taskEntry({
    id,
    title: id,
    reference: { repository: 'repo', path: `master/${id}.json` },
    stage: 'started',
    activity: 'paused',
    worktrees: [],
    children: [],
    summary: taskSummary({ kind: 'subTask', form: 'task' }),
    ...over,
  });
}
export function taskMap(entries: TaskEntry[]): Record<string, TaskEntry> {
  return Object.fromEntries(entries.map((entry) => [entry.key, entry]));
}
export function seed(tasks: TaskEntry[]) {
  dashboardStore.getState().applySnapshot(projection({ tasks }));
}
export function hierarchy() {
  const leaf = task('M-L1');
  const master = task('M', {
    summary: taskSummary({ kind: 'master', form: 'master' }),
    reference: { repository: 'repo', path: 'master/task.json' },
    children: [leaf.key],
    counts: { planned: 0, started: 1, completed: 0, aborted: 0, works: 0, paused: 1, unknown: 0 },
  });
  const sprint = task('S', {
    summary: taskSummary({ kind: 'master', form: 'sprint' }),
    reference: { repository: 'repo', path: 'sprint/task.json' },
    children: [master.key],
    counts: { planned: 0, started: 1, completed: 0, aborted: 0, works: 0, paused: 1, unknown: 0 },
  });
  master.parent = sprint.key;
  leaf.parent = master.key;
  return { sprint, master, leaf, entries: [sprint, master, leaf] };
}
export function rowOf(title: HTMLElement): HTMLElement {
  const row = title.closest<HTMLElement>("[role='option']");
  if (!row) throw new Error('Missing task row');
  return row;
}
export function installListCleanup() {
  afterEach(() => {
    cleanup();
    dashboardStore.getState().reset();
    localStorage.clear();
    vi.unstubAllGlobals();
  });
  beforeEach(() => vi.stubGlobal('CSS', { escape: (value: string) => value }));
}

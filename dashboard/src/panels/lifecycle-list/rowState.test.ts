import { expect, it } from 'vitest';
import { formatLimitTime, taskStatusKey } from '../detail-panel/pageParts';
import type { ProviderLimit } from '../../types/projection';
import { progressFigure, rowHover, shortTaskId } from './rowState';
import { hierarchy, task, taskMap } from './test-utils';

const WINDOW: ProviderLimit = {
  until: '2026-10-21T12:00:00+02:00',
  seats: [
    {
      role: 'worker',
      agentId: '0f0c1d2e-0000-4000-8000-000000000001',
      source: 'Claude',
      account: 'Max 20x',
      window: 'Session',
      resetsAt: '2026-10-21T12:00:00+02:00',
    },
  ],
};

it('keeps the row state line and adds each limited seat under it', () => {
  const entry = task('L', { providerLimit: WINDOW });
  expect(taskStatusKey(entry)).toBe('limited');
  const lines = rowHover(entry).split('\n');
  const state = lines.indexOf('State: rate limited');
  expect(state).toBeGreaterThan(-1);
  // The limit joins the hover the row already had; it does not replace it.
  expect(lines).toContain('Stage: started');
  expect(lines.slice(state + 1, state + 2)).toEqual([
    `worker · 0f0c1d2e-0000-4000-8000-000000000001 · Claude · Max 20x · Session · resets ${formatLimitTime(WINDOW.until)}`,
  ]);
  // A task with no served limit keeps its hover exactly as it was.
  expect(rowHover(task('plain'))).toContain('State: paused');
  expect(rowHover(task('plain'))).not.toContain('resets ');
});

it('formats only served identity, counts and status', () => {
  const { master, leaf, entries } = hierarchy();
  expect(shortTaskId(leaf, taskMap(entries))).toBe('L1');
  expect(shortTaskId(master, taskMap(entries))).toBe('');
  expect(progressFigure(master)).toBe('0/1');
  expect(progressFigure(task('x', { stepsDone: 2, stepsTotal: 5 }))).toBe('2/5');
  expect(rowHover(leaf)).toContain('State: paused');
  master.counts = {
    planned: 0,
    started: 0,
    completed: 2,
    aborted: 1,
    works: 0,
    paused: 0,
    unknown: 0,
  };
  expect(progressFigure(master)).toBe('2/2');
  expect(rowHover(master)).toContain('Tasks completed: 2 of 2, 1 abandoned');
  master.counts.completed = 0;
  master.counts.aborted = 2;
  expect(progressFigure(master)).toBe('');
  expect(rowHover(master)).toContain('2 abandoned');
  expect(rowHover(task('x', { stage: 'completed', activity: undefined }))).toContain(
    'State: completed',
  );
});

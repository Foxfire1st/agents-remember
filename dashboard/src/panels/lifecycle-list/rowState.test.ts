import { expect, it } from 'vitest';
import { progressFigure, rowHover, shortTaskId } from './rowState';
import { hierarchy, task, taskMap } from './test-utils';

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

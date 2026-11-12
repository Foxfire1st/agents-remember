// MIK-R175: over a step dot only the step's own tooltip shows (rules 1 and 2), and the tooltip's
// id and status word keep their whole width on one line (rule 3). The style objects are pinned
// directly because jsdom evaluates no layout: a width assertion there would prove nothing.
import { render } from '@testing-library/react';
import { expect, it, vi } from 'vitest';

import { LifecycleList } from './LifecycleList';
import { installListCleanup, rowOf, seed, task, taskSummary } from './test-utils';
import {
  stepTipStyles,
  tipPartStyles,
  tipTitleStyles,
} from '../detail-panel/stepDotStyles';

installListCleanup();

function steps(count: number) {
  return Array.from({ length: count }, (_, index) => ({
    id: `S${index + 1}`,
    title: 'A step title long enough to squeeze the status word beside it',
    status: index < 2 ? 'done' : 'pending',
    substeps: [
      { id: `S${index + 1}.1`, title: 'A substep title long enough to squeeze it too', status: 'done' },
    ],
    noteTruncated: false,
    outcomeTruncated: false,
  }));
}

function entryOf(id: string, count: number) {
  return task(id, {
    stepsDone: 2,
    stepsTotal: 3,
    summary: taskSummary({ kind: 'subTask', form: 'task', steps: steps(count) }),
  });
}

it('leaves no native title on any ancestor of a step mark, and the row text keeps its hover', () => {
  const few = entryOf('few', 3);
  const many = entryOf('many', 13);
  seed([few, many]);
  const ui = render(<LifecycleList selectedId={null} onSelect={vi.fn()} />);
  for (const name of ['few', 'many']) {
    const row = rowOf(ui.getByText(name, { selector: '[data-part="title"]' }));
    // A few steps are dots and many steps are one bar; both are marks the browser must not title.
    const marks = row.querySelectorAll('[aria-hidden="true"]');
    expect(marks.length).toBeGreaterThan(0);
    for (const mark of marks) {
      for (let node = mark.parentElement; node && node !== row; node = node.parentElement) {
        expect(node.getAttribute('title')).toBeNull();
      }
    }
    const hover = row.querySelector('[data-part="title"]')?.getAttribute('title');
    expect(hover).toContain(name);
    // Rule 2: the title, the status word and the count figure keep the row's hover text.
    expect(row.querySelector('[data-part="title"]')?.getAttribute('title')).toBe(hover);
    expect(row.querySelector('[data-testid="task-word"]')?.parentElement?.getAttribute('title')).toBe(
      hover,
    );
    const figures = [...row.querySelectorAll('[title]')].filter((node) =>
      /^\d+\/\d+$/.test(node.textContent ?? ''),
    );
    expect(figures).toHaveLength(1);
    expect(figures[0].getAttribute('title')).toBe(hover);
  }
});

it('pins the tooltip parts that stay whole: only the title shrinks and wraps', () => {
  expect(tipPartStyles.whiteSpace).toBe('nowrap');
  expect(tipPartStyles.flexShrink).toBe('0');
  expect(tipTitleStyles.overflowWrap).toBe('anywhere');
  // The box itself no longer breaks words anywhere: that is what split "done" letter by letter.
  expect(stepTipStyles.overflowWrap).toBe('normal');
  expect(stepTipStyles.wordBreak).toBe('normal');
});

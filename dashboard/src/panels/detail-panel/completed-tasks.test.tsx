// Completion ordering and preview use canonical served membership, without document scans.
import { cleanup, fireEvent, render, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { TaskEntry } from '../../types/projection';
import { completedEntries } from './completedTasksModel';
import { GroupViewBody, TaskChildrenBlock } from './task-groups';
import { taskEntry, taskReference, taskSummary, taskTree } from './test-utils';

function dataset(
  numbers = ['L153', 'L46', 'L2', 'L9', 'L12', 'L80', 'L7'],
  form: 'master' | 'sprint' = 'master',
  stage: TaskEntry['stage'] = 'completed',
) {
  const children = numbers.map((id, index) =>
    taskEntry({
      reference: taskReference(`repo-a/completed/${id}.json`),
      id,
      title: `Task ${id}`,
      stage,
      summary: taskSummary({
        completedAt:
          index === 3
            ? undefined
            : index === 2
              ? '2026-10-09T15:12:00Z'
              : `2026-10-${String(index + 1).padStart(2, '0')}T12:00:00Z`,
      }),
    }),
  );
  const [entry, ...rest] = taskTree(
    taskEntry({
      reference: taskReference('repo-a/completed/task.json'),
      id: 'M',
      summary: taskSummary({ kind: 'master', form }),
    }),
    children,
  );
  const tasks = Object.fromEntries([entry, ...rest].map((item) => [item.key, item]));
  return { entry, tasks, doc: entry.summary! };
}
function rowNumbers(root: HTMLElement): string[] {
  return [...root.querySelectorAll('[data-testid^="group-row-"]')].map(
    (row) => row.querySelector('[data-completed-number]')?.textContent ?? '',
  );
}
afterEach(cleanup);

describe('master completed list', () => {
  it('sorts by time and numeric id and resets ephemeral ordering on reopening', () => {
    const data = dataset();
    const draw = () => <GroupViewBody {...data} stage="completed" onJump={vi.fn()} />;
    const view = render(draw());
    const table = view.getByTestId('group-table');
    expect(rowNumbers(table)).toEqual(['L2', 'L7', 'L80', 'L12', 'L46', 'L153', 'L9']);
    expect(within(view.getByTestId('group-row-3')).getByText('2026-10-09 15:12')).toBeTruthy();
    const number = view.getByRole('button', { name: /number/i });
    fireEvent.click(number);
    expect(rowNumbers(table)).toEqual(['L2', 'L7', 'L9', 'L12', 'L46', 'L80', 'L153']);
    expect(number.closest('th')?.getAttribute('aria-sort')).toBe('ascending');
    fireEvent.click(number);
    expect(rowNumbers(table)).toEqual(['L153', 'L80', 'L46', 'L12', 'L9', 'L7', 'L2']);
    expect(number.closest('th')?.getAttribute('aria-sort')).toBe('descending');
    const time = view.getByRole('button', { name: /completed/i });
    fireEvent.click(time);
    expect(rowNumbers(table)[0]).toBe('L2');
    expect(time.closest('th')?.getAttribute('aria-sort')).toBe('descending');
    fireEvent.click(time);
    expect(rowNumbers(table)).toEqual(['L153', 'L46', 'L12', 'L80', 'L7', 'L2', 'L9']);
    expect(time.closest('th')?.getAttribute('aria-sort')).toBe('ascending');
    view.unmount();
    expect(rowNumbers(render(draw()).getByTestId('group-table'))[0]).toBe('L2');
  });

  it.each([7, 3, 0])('previews newest five above the link, with %i completed tasks', (count) => {
    const data = dataset(['L153', 'L46', 'L2', 'L9', 'L12', 'L80', 'L7'].slice(0, count));
    const openView = vi.fn();
    const view = render(<TaskChildrenBlock {...data} onJump={vi.fn()} openView={openView} />);
    if (!count) {
      expect(view.queryByTestId('completed-preview')).toBeNull();
      expect(view.queryByTestId('group-done-link')).toBeNull();
      return;
    }
    const preview = view.getByTestId('completed-preview');
    expect(rowNumbers(preview)).toEqual(
      count === 7 ? ['L2', 'L7', 'L80', 'L12', 'L46'] : ['L2', 'L46', 'L153'],
    );
    expect(preview.querySelectorAll('time')).toHaveLength(Math.min(count, 5));
    expect(
      preview.compareDocumentPosition(view.getByTestId('group-done-link')) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).not.toBe(0);
    fireEvent.click(view.getByTestId('group-done-link'));
    expect(openView).toHaveBeenCalledWith({ kind: 'group', status: 'done' });
  });

  it.each(['aborted', 'sprint'] as const)(
    'keeps %s rows in authored order without sort controls',
    (kind) => {
      const data = dataset(
        ['L153', 'L46'],
        kind === 'sprint' ? 'sprint' : 'master',
        kind === 'aborted' ? 'aborted' : 'completed',
      );
      const view = render(
        <GroupViewBody
          {...data}
          stage={kind === 'aborted' ? 'aborted' : 'completed'}
          onJump={vi.fn()}
        />,
      );
      expect(view.queryByRole('button', { name: /completed/i })).toBeNull();
      expect(
        [...view.getByTestId('group-table').children].map((row) => row.getAttribute('data-testid')),
      ).toEqual(['group-row-1', 'group-row-2']);
    },
  );
});

describe('composed completed lookup operation counts', () => {
  it.each([100, 300].flatMap((size) => [false, true].map((preview) => ({ size, preview }))))(
    'keeps output and qualified navigation linear at $size rows, preview=$preview',
    ({ size, preview }) => {
      const data = dataset(Array.from({ length: size }, (_, i) => `L${i}`));
      const missing = taskEntry({
        reference: taskReference('repo-a/completed/missing.json'),
        id: 'missing',
        title: 'Missing document',
        stage: 'completed',
        summary: undefined,
        parent: data.entry.key,
      });
      data.entry.children.push(missing.key);
      data.entry.counts.completed++;
      data.tasks[missing.key] = missing;
      let reads = 0;
      const counted = new Proxy(data.tasks, {
        get(target, key: string) {
          reads++;
          return target[key];
        },
      });
      expect(completedEntries(data.entry, counted)).toEqual(
        completedEntries(data.entry, data.tasks),
      );
      expect(reads).toBe(size + 1);
      reads = 0;
      const onJump = vi.fn();
      const draw = (tasks: Record<string, TaskEntry>) =>
        preview ? (
          <TaskChildrenBlock {...data} tasks={tasks} onJump={onJump} openView={vi.fn()} />
        ) : (
          <GroupViewBody {...data} tasks={tasks} stage="completed" onJump={onJump} />
        );
      const control = render(draw(data.tasks));
      const html = control.container.innerHTML;
      control.unmount();
      const measured = render(draw(counted));
      expect(measured.container.innerHTML).toBe(html);
      const table = measured.getByTestId(preview ? 'completed-preview' : 'group-table');
      expect(rowNumbers(table)).toHaveLength(preview ? 5 : size + 1);
      for (const button of table.querySelectorAll<HTMLButtonElement>(
        'button[data-testid^="subtask-open-"]',
      )) {
        const position = Number(button.dataset.testid?.split('-').pop());
        fireEvent.click(button);
        expect(onJump).toHaveBeenLastCalledWith(`taskdoc:${data.entry.children[position - 1]}`);
      }
      if (!preview) {
        expect(measured.getByTestId(`group-row-${size + 1}`).textContent).toContain(
          'Missing document',
        );
        fireEvent.click(measured.getByRole('button', { name: /number/i }));
        fireEvent.click(measured.getByRole('button', { name: /completed/i }));
      }
      expect(reads).toBeLessThanOrEqual((size + 1) * 4);
    },
  );
});

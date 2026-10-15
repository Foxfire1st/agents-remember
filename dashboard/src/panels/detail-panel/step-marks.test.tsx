// MIK-R128 evidence 8/9: major-only summaries and pointer-rest details.
import { act, cleanup, fireEvent, render, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { MasterNowBlock } from './task-groups';
import { StepsBlock } from './taskBlocks';
import { LifecycleList } from '../lifecycle-list/LifecycleList';
import {
  EMPTY_ANALYTICS,
  enclosure,
  lifecycle,
  projection,
  seed,
} from '../lifecycle-list/test-utils';
import { taskDoc } from './test-utils';
import { stepMarkStates } from './model';
import type { TaskStepNode } from '../../types/projection';

const MASTER = '/tasks/repo-a/dots/task.json';
const LEAF = '/tasks/repo-a/dots/01_leaf.json';
function fiveSteps(): TaskStepNode[] {
  return Array.from({ length: 5 }, (_, i) => ({
    id: `S${i + 1}`,
    title: `Major ${i + 1}`,
    status: i === 0 ? 'done' : 'pending',
    noteTruncated: false,
    outcomeTruncated: false,
    substeps: Array.from({ length: i < 4 ? 2 : 0 }, (_, j) => ({
      id: `S${i + 1}.${j + 1}`,
      title: `Child ${i + 1}.${j + 1}`,
      status: i === 0 || (i === 1 && j === 0) ? 'done' : 'pending',
    })),
  }));
}
function docs() {
  return [
    taskDoc({
      kind: 'master',
      docPath: MASTER,
      id: 'M',
      title: 'Master',
      subTasks: [
        { number: 'L1', name: 'Leaf', file: '01_leaf.md', status: 'inProgress', scope: '' },
      ],
    }),
    taskDoc({
      kind: 'subTask',
      docPath: LEAF,
      id: 'M-L1',
      title: 'Leaf',
      lifecycleId: 'DOT-LC',
      steps: fiveSteps(),
      stepsDone: 1,
      stepsTotal: 5,
    }),
  ];
}
function MasterLine() {
  const data = docs();
  return <MasterNowBlock doc={data[0]} sliceDocs={[data[1]]} onOpen={() => {}} onJump={() => {}} />;
}
function TaskLine() {
  return <StepsBlock steps={fiveSteps()} done={1} total={5} />;
}
function marks(root: HTMLElement) {
  return root.querySelectorAll<HTMLElement>('[data-step-dot]');
}

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe('major step marks', () => {
  it('draws five dots and the same major count on master, task line and sidebar; keeps nested list', () => {
    const data = docs();
    const master = render(<MasterLine />);
    expect(marks(master.getByTestId('now-steps-1'))).toHaveLength(5);
    expect(master.getByTestId('now-steps-1').textContent).toBe('1/5');
    master.unmount();
    const leaf = render(<TaskLine />);
    expect(marks(leaf.getByTestId('steps-count'))).toHaveLength(5);
    expect(leaf.getByTestId('steps-progress').textContent).toBe('1 of 5 done');
    expect(leaf.getByTestId('step-S2').contains(leaf.getByTestId('substep-S2-S2.1'))).toBe(true);
    expect(
      leaf.getByTestId('steps-list').querySelectorAll('[data-testid^="substep-"]'),
    ).toHaveLength(8);
    leaf.unmount();
    seed(
      projection({
        lifecycles: [lifecycle({ id: 'DOT-LC' })],
        enclosures: [
          enclosure({
            enclosure: '/contracts/dots',
            lifecycleId: 'DOT-LC',
            taskRoot: '/tasks/repo-a/dots',
            leafId: '01_leaf',
          }),
        ],
        analytics: { ...EMPTY_ANALYTICS, taskDocuments: data },
      }),
    );
    const sidebar = render(<LifecycleList selectedId={null} onSelect={() => {}} />);
    const row = sidebar.getByText('Leaf').closest<HTMLElement>('[role="option"]')!;
    expect(marks(row)).toHaveLength(5);
    expect(row.textContent).toContain('1/5');
  });

  it('makes an unfinished major current for begun or done children, but never uncompletes a done major', () => {
    const steps = fiveSteps();
    expect(stepMarkStates(steps)).toEqual(['done', 'current', 'pending', 'pending', 'pending']);
    steps[2].substeps[0].status = 'inProgress';
    steps[2].status = 'blocked';
    expect(stepMarkStates(steps)[2]).toBe('current');
    expect(stepMarkStates(steps)[0]).toBe('done');
  });

  it.each(['master', 'task'] as const)(
    'shows cached title/state/ordered children after rest on the %s line, not immediately',
    (place) => {
      vi.useFakeTimers();
      const view = render(place === 'master' ? <MasterLine /> : <TaskLine />);
      const root = view.getByTestId(place === 'master' ? 'now-steps-1' : 'steps-count');
      const dots = marks(root);
      fireEvent.pointerEnter(dots[1]);
      expect(view.queryByRole('tooltip')).toBeNull();
      act(() => {
        vi.advanceTimersByTime(500);
      });
      const tip = view.getByRole('tooltip');
      expect(tip.textContent).toContain('Major 2');
      expect(tip.textContent).toContain('in work');
      const children = within(tip).getAllByRole('listitem');
      expect(children.map((child) => child.textContent)).toEqual([
        'S2.1Child 2.1done',
        'S2.2Child 2.2pending',
      ]);
      expect(dots[1].getAttribute('tabindex')).toBeNull();
      expect(within(root).queryByRole('button')).toBeNull();
      fireEvent.pointerLeave(dots[1]);
      expect(view.queryByRole('tooltip')).toBeNull();
      fireEvent.pointerEnter(dots[4]);
      act(() => {
        vi.advanceTimersByTime(500);
      });
      expect(view.getByRole('tooltip').textContent).toContain('Major 5');
      expect(view.getByRole('tooltip').textContent).toContain('pending');
      expect(within(view.getByRole('tooltip')).queryByRole('list')).toBeNull();
    },
  );
});

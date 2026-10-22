// MIK-R190 rule 13: a surface with no display clock still owes its reset to the accessible status
// routes. The order graph's unit, the constellation's nodes and the sidebar's progress mark each
// carry the served end in a computed name, and a keyboard reader can reach the constellation's
// selectable nodes through its text alternative.
import { fireEvent, render } from '@testing-library/react';
import { expect, it, vi } from 'vitest';

import { taskEntrySelectionKey, taskReferenceKey } from '../../data/taskReference';
import type { ProviderLimit, TaskEntry, TaskExecutionNodeView } from '../../types/projection';
import { formatLimitTime } from './pageParts';
import { OrderBlock } from './orderGraph';
import { Topology } from '../Topology';
import { LifecycleList } from '../lifecycle-list/LifecycleList';
import { installListCleanup, seed, task } from '../lifecycle-list/test-utils';
import { taskEntry, taskReference } from './test-utils';
import { dashboardStore } from '../../data/store';
import { enclosure, projection } from '../../test/fixtures/wire';

installListCleanup();

const UNTIL = '2026-10-21T12:00:00+02:00';

const LIMIT: ProviderLimit = {
  until: UNTIL,
  seats: [
    {
      role: 'worker',
      agentId: '0f0c1d2e-0000-4000-8000-000000000001',
      source: 'Claude',
      account: 'Max 20x',
      window: 'Session',
      resetsAt: UNTIL,
    },
  ],
};

const REF = taskReference('repo-a/master/task.json');

function limitedMaster(): TaskEntry {
  return taskEntry({
    id: 'M',
    title: 'Master',
    reference: REF,
    stage: 'started',
    activity: 'paused',
    providerLimit: LIMIT,
    worktrees: [],
  });
}

function graphNode(): TaskExecutionNodeView {
  return {
    nodeId: 'repo-a/master/task.json',
    kind: 'lump',
    masterRef: REF,
    masterTitle: 'Master',
    leafIds: [],
    leafTitles: [],
    waveIndex: 0,
    frontierState: 'in-flight',
    predecessors: [],
  };
}

it('names the served end in the order graph unit the keyboard reaches', () => {
  const entry = limitedMaster();
  const view = render(
    <OrderBlock
      graphView={{ nodes: [graphNode()] }}
      handlers={{ tasks: { [taskReferenceKey(REF)]: entry }, onJump: vi.fn() }}
      openView={vi.fn()}
      form="master"
    />,
  );

  const unit = view.getByRole('button', {
    name: `M Master, rate limited, until ${formatLimitTime(UNTIL)}`,
  });

  expect(unit.getAttribute('tabIndex')).toBeNull();
  expect(unit.textContent).toContain('rate limited');
  view.unmount();
});

it('names the served end on the sidebar progress mark', () => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date('2026-10-21T10:00:00+02:00'));
  const entry = task('L', { activity: 'paused', providerLimit: LIMIT });
  seed([entry]);
  const list = render(<LifecycleList selectedId={null} onSelect={vi.fn()} />);
  const title = list.getByText('L', { selector: '[data-part="title"]' });
  const row = title.closest("[role='option']");
  const mark = row?.querySelector('[data-testid="task-state"]');
  const label = mark?.getAttribute('aria-label') ?? '';

  expect(label).toBe(`Task progress: rate limited, until ${formatLimitTime(UNTIL)}`);
  expect(label).toContain('until ');
  list.unmount();
  vi.useRealTimers();
});

it('offers the constellation as text, with its selectable nodes reachable and working', () => {
  const entry = limitedMaster();
  const bound = enclosure({ worktreeGroup: '/worktrees/repo-a/master-ar' });
  dashboardStore.getState().applySnapshot(
    projection({
      tasks: [{ ...entry, worktrees: [bound] }],
      activeWorktreeGroups: [bound.worktreeGroup],
    }),
  );
  const onSelect = vi.fn();
  const view = render(<Topology onSelect={onSelect} />);
  const alternative = view.getByTestId('topology-alternative');

  // The canvas itself has no text; this is the route a screen reader and the keyboard get.
  expect(alternative.getAttribute('aria-hidden')).toBeNull();
  expect(alternative.textContent).toContain(
    `Master — worktree · rate limited, until ${formatLimitTime(UNTIL)}`,
  );
  expect(alternative.textContent).toContain('Session');
  const select = view.getByRole('button', {
    name: `Master — worktree · rate limited, until ${formatLimitTime(UNTIL)}\nworker · 0f0c1d2e-0000-4000-8000-000000000001 · Claude · Max 20x · Session · resets ${formatLimitTime(UNTIL)}`,
  });
  expect(select.getAttribute('tabIndex')).toBeNull();
  fireEvent.click(select);
  expect(onSelect).toHaveBeenCalledWith(taskEntrySelectionKey(entry.key));
  view.unmount();
});

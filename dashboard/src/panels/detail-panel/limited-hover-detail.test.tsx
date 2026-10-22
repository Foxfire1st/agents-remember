// MIK-R190 rule 13: no surface of the dashboard shows a countdown, but every one of them owes the
// served fact. The order graph, the sprint graph and the constellation each carry the whole detail
// of every limited seat — its provider account, its window and that window's own reset.
import { render } from '@testing-library/react';
import { expect, it, vi } from 'vitest';

import type { ProviderLimit, TaskEntry, TaskExecutionNodeView } from '../../types/projection';
import { taskReferenceKey } from '../../data/taskReference';
import { seedTaskEntries, taskEntry, taskReference } from '../detail-panel/test-utils';
import { formatLimitTime } from '../detail-panel/pageParts';
import { OrderBlock } from './orderGraph';
import { SprintGraphView } from '../sprint-graph/SprintGraphView';
import { Topology } from '../Topology';
import { buildTopology } from '../../topology/model';
import { enclosure, projection } from '../../test/fixtures/wire';
import { dashboardStore } from '../../data/store';

const SESSION_UNTIL = '2026-10-21T12:00:00+02:00';
const WEEKLY_UNTIL = '2026-10-24T09:30:00+02:00';

const TWO_SEATS: ProviderLimit = {
  until: SESSION_UNTIL,
  seats: [
    {
      role: 'worker',
      agentId: '0f0c1d2e-0000-4000-8000-000000000001',
      source: 'Claude',
      account: 'Max 20x',
      window: 'Session',
      resetsAt: SESSION_UNTIL,
    },
    {
      role: 'reviewer',
      agentId: '0f0c1d2e-0000-4000-8000-000000000002',
      source: 'Codex',
      account: 'Pro',
      window: 'Weekly',
      resetsAt: WEEKLY_UNTIL,
    },
  ],
};

const REF = taskReference('repo-a/master/task.json');

function seatLines(): string[] {
  return TWO_SEATS.seats.map(
    (seat) =>
      `${seat.role} · ${seat.agentId} · ${seat.source} · ${seat.account} · ${seat.window} · resets ${formatLimitTime(seat.resetsAt)}`,
  );
}

function master(): TaskEntry {
  return taskEntry({
    id: 'M',
    title: 'Master',
    reference: REF,
    stage: 'started',
    activity: 'paused',
    providerLimit: TWO_SEATS,
    worktrees: [],
  });
}

function node(): TaskExecutionNodeView {
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

it('names both served seats in the order graph word, with their own windows and resets', () => {
  const entry = master();
  const view = render(
    <OrderBlock
      graphView={{ nodes: [node()] }}
      handlers={{ tasks: { [taskReferenceKey(REF)]: entry }, onJump: vi.fn() }}
      openView={vi.fn()}
      form="master"
    />,
  );
  const word = view.container.querySelector('[data-status]');
  const title = word?.getAttribute('title') ?? '';

  expect(word?.textContent).toBe('rate limited');
  expect(title.split('\n')[0]).toBe(`until ${formatLimitTime(SESSION_UNTIL)}`);
  expect(title.split('\n').slice(1)).toEqual(seatLines());
  expect(title).toContain('Codex · Pro · Weekly');
  view.unmount();
});

it('names both served seats in the sprint graph box word', () => {
  const entry = master();
  seedTaskEntries([entry]);
  const view = render(<SprintGraphView graphView={{ nodes: [node()] }} />);
  const word = view.getByTestId('graph-task-status');
  const title = word.getAttribute('title') ?? '';

  expect(word.textContent).toBe('rate limited');
  expect(title.split('\n').slice(1)).toEqual(seatLines());
  view.unmount();
});

it('carries both served seats into the constellation node and its accessible text', () => {
  const entry = master();
  const bound = enclosure({ worktreeGroup: '/worktrees/repo-a/master-ar' });
  dashboardStore.getState().applySnapshot(
    projection({
      tasks: [{ ...entry, worktrees: [bound] }],
      activeWorktreeGroups: [bound.worktreeGroup],
    }),
  );
  const [worktree] = buildTopology([{ entry, enclosure: bound }], []).filter(
    (candidate) => candidate.kind === 'wt',
  );

  // The tip the pointer raises reads this field, line for line.
  expect(worktree.detail?.split('\n').slice(1)).toEqual(seatLines());

  const view = render(<Topology onSelect={vi.fn()} />);
  const alternative = view.getByTestId('topology-alternative');
  for (const line of seatLines()) {
    expect(alternative.textContent).toContain(line);
  }
  // The bare word and the aggregate end stay where they were.
  expect(alternative.textContent).toContain(
    `worktree · rate limited, until ${formatLimitTime(SESSION_UNTIL)}`,
  );
  view.unmount();
});

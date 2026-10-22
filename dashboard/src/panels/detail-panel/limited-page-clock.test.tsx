// MIK-R190 rule 13: the authored task page counts a served limit down even when the host timed no
// event on the entry — the limit is the fact that moves, and its end arrives without a new
// snapshot. A hidden page keeps its no-timer behavior.
import { act, render } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';

import { taskReferenceKey } from '../../data/taskReference';
import type { ProviderLimit, TaskEntry } from '../../types/projection';
import { TaskReader } from './taskReader';
import { seedTaskEntries, taskEntry, taskReference, taskSummary } from './test-utils';

const LIMIT: ProviderLimit = {
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

function limitedLeaf(): TaskEntry {
  return taskEntry({
    id: 'L',
    title: 'Limited leaf',
    stage: 'started',
    activity: 'paused',
    providerLimit: LIMIT,
    reference: taskReference('repo/leaf/task.json'),
    worktrees: [],
  });
}

function props(entry: TaskEntry) {
  return {
    entry,
    doc: taskSummary({ kind: 'subTask', form: 'task', objective: 'Leaf objective' }),
    bodyState: 'available' as const,
    tasks: { [taskReferenceKey(entry.reference!)]: entry },
    onJump: vi.fn(),
  };
}

function clockAt(iso: string) {
  vi.useFakeTimers();
  vi.setSystemTime(new Date(iso));
}

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

it('counts an untimed limited page down and hands it back to paused with no new snapshot', () => {
  clockAt('2026-10-21T10:00:00+02:00');
  const entry = limitedLeaf();
  // The host timed no event on this entry: the served limit alone has to open the page's clock.
  expect(entry.eventAt).toBeUndefined();
  seedTaskEntries([entry]);
  const page = render(<TaskReader {...props(entry)} />);
  const word = () => page.getByTestId('doc-status');

  expect(word().textContent).toBe('rate limited: 2h');
  act(() => vi.advanceTimersByTime(3_600_000));
  expect(word().textContent).toBe('rate limited: 1h');
  act(() => vi.advanceTimersByTime(3_600_000));
  // The display clock alone reaches the served end; nothing was re-served.
  expect(word().textContent).toBe('paused');
  expect(word().getAttribute('data-status')).toBe('paused');
  page.unmount();
});

it('leaves a hidden limited page stopped, and invents no age for an untimed entry', () => {
  clockAt('2026-10-21T10:00:00+02:00');
  const entry = limitedLeaf();
  seedTaskEntries([entry]);
  const page = render(<TaskReader {...props(entry)} active={false} />);
  const word = () => page.getByTestId('doc-status');

  expect(word().textContent).toBe('rate limited');
  act(() => vi.advanceTimersByTime(3_600_000));
  // No countdown and no age: a hidden page shows the served words and nothing it made up.
  expect(word().textContent).toBe('rate limited');
  page.unmount();
});
